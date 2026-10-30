package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// SharedLimiter keeps the caps in the control-plane API's rate_counters (#20), shared by every gateway
// instance and kept across restarts: POST /internal/gateway/reserve before the provider call and
// /internal/gateway/settle after, authenticated with the gateway's own Google ID token. Local holds
// the platform-key defaults and workspace caps, which it computes the limits from.
type SharedLimiter struct {
	URL     string // the API's base URL; also the ID token's audience
	Client  *http.Client
	Token   func(ctx context.Context, audience string) (string, error)
	Local   *Limiter
	Log     *slog.Logger
	Timeout time.Duration
}

type remoteReservation = json.RawMessage

func (s *SharedLimiter) post(ctx context.Context, path string, body any, out any) error {
	timeout := s.Timeout
	if timeout == 0 {
		timeout = 3 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	b, _ := json.Marshal(body)
	base := strings.TrimRight(s.URL, "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base+path, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if s.Token != nil {
		tok, err := s.Token(ctx, base)
		if err != nil {
			return fmt.Errorf("gateway ID token: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := s.Client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return fmt.Errorf("%s: status %d", path, resp.StatusCode)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// Admit reserves on the API. A non-nil error means the counters couldn't be reached.
func (s *SharedLimiter) Admit(ctx context.Context, cfg *AppConfig, platform bool, inTokens, wantOut int64, rate Rate) (int64, *Reservation, *CapError, error) {
	lim := s.Local.limitsFor(cfg, platform)
	body := map[string]any{
		"app_id": cfg.AppID, "workspace_id": cfg.WorkspaceID, "platform": platform,
		"in_tokens": inTokens, "want_out": wantOut,
		"limits": map[string]any{"app_tokens": lim.appTokens, "ws_tokens": lim.wsTokens, "app_rpm": lim.appRPM,
			"ws_rpm": lim.wsRPM, "spend_usd": lim.spendUSD},
	}
	if rate != nil {
		body["rate"] = []float64{rate[0], rate[1]}
	}
	var out struct {
		Granted     *int64            `json:"granted"`
		Reservation remoteReservation `json:"reservation"`
		Cap         string            `json:"cap"`
		Limit       float64           `json:"limit"`
		ResetsAt    time.Time         `json:"resets_at"`
	}
	if err := s.post(ctx, "/internal/gateway/reserve", body, &out); err != nil {
		return 0, nil, nil, err
	}
	if out.Cap != "" {
		return 0, nil, &CapError{out.Cap, out.Limit, out.ResetsAt}, nil
	}
	if out.Granted == nil {
		return 0, nil, nil, fmt.Errorf("reserve: no grant in the answer")
	}
	return *out.Granted, &Reservation{cfg: cfg, platform: platform, remote: out.Reservation}, nil, nil
}

// Settle reports actual usage in the background (the response has gone already); a failure leaves
// the reservation counted, which errs on the side of the cap.
func (s *SharedLimiter) Settle(res *Reservation, tokens int64, costUSD float64) {
	if res == nil || res.remote == nil {
		return
	}
	go func() {
		err := s.post(context.Background(), "/internal/gateway/settle",
			map[string]any{"reservation": res.remote, "tokens": tokens, "cost_usd": costUSD}, nil)
		if err != nil && s.Log != nil {
			s.Log.Error("settling shared caps failed; the reservation stays counted", "app", res.cfg.App, "error", err.Error())
		}
	}()
}
