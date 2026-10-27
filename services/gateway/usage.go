package main

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// UsageEvent is one metered call. It maps onto usage_events rows (source = 'gateway'): one row for
// ai_input_tokens and one for ai_output_tokens, with cost_usd split by price.
type UsageEvent struct {
	AppID        string    `json:"app_id"`
	WorkspaceID  string    `json:"workspace_id"`
	App          string    `json:"app"`
	Provider     string    `json:"provider"`
	Model        string    `json:"model"`
	InputTokens  int64     `json:"input_tokens"`
	OutputTokens int64     `json:"output_tokens"`
	CostUSD      float64   `json:"cost_usd"`
	Priced       bool      `json:"priced"`
	Estimated    bool      `json:"estimated"`
	Status       int       `json:"status"`
	Stream       bool      `json:"stream"`
	LatencyMS    int64     `json:"latency_ms"`
	CapHit       string    `json:"cap_hit,omitempty"`
	OccurredAt   time.Time `json:"occurred_at"`
}

type UsageSink interface {
	Record(UsageEvent)
}

// LogSink writes usage as structured log lines (Cloud Logging indexes jsonPayload), tagged
// shipcube_usage=true so a log sink or the monitor can pick them up.
type LogSink struct{ Log *slog.Logger }

func (s LogSink) Record(e UsageEvent) {
	s.Log.Info("usage", "shipcube_usage", true, "app_id", e.AppID, "workspace_id", e.WorkspaceID, "app", e.App,
		"provider", e.Provider, "model", e.Model, "input_tokens", e.InputTokens, "output_tokens", e.OutputTokens,
		"cost_usd", e.CostUSD, "priced", e.Priced, "estimated", e.Estimated, "status", e.Status, "stream", e.Stream,
		"latency_ms", e.LatencyMS, "cap_hit", e.CapHit)
}

// HTTPSink batches events to the control-plane API (POST {base}/internal/usage, a JSON array),
// which writes usage_events. Failed batches are logged and dropped; the log line is the backup.
type HTTPSink struct {
	URL      string
	Client   *http.Client
	Token    func(ctx context.Context, audience string) (string, error)
	Audience string
	Log      *slog.Logger

	mu  sync.Mutex
	buf []UsageEvent
}

func (s *HTTPSink) Record(e UsageEvent) {
	s.mu.Lock()
	s.buf = append(s.buf, e)
	s.mu.Unlock()
}

func (s *HTTPSink) Run(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			s.Flush(context.Background())
			return
		case <-t.C:
			s.Flush(ctx)
		}
	}
}

func (s *HTTPSink) Flush(ctx context.Context) {
	s.mu.Lock()
	batch := s.buf
	s.buf = nil
	s.mu.Unlock()
	if len(batch) == 0 {
		return
	}
	b, _ := json.Marshal(batch)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.URL, bytes.NewReader(b))
	if err == nil && s.Token != nil {
		var tok string
		if tok, err = s.Token(ctx, s.Audience); err == nil {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
	}
	if err == nil {
		req.Header.Set("Content-Type", "application/json")
		var resp *http.Response
		if resp, err = s.Client.Do(req); err == nil {
			resp.Body.Close()
			if resp.StatusCode >= 300 {
				err = &statusError{resp.StatusCode}
			}
		}
	}
	if err != nil {
		s.Log.Warn("usage flush failed", "events", len(batch), "error", err.Error())
	}
}

type statusError struct{ code int }

func (e *statusError) Error() string { return http.StatusText(e.code) }

type multiSink []UsageSink

func (m multiSink) Record(e UsageEvent) {
	for _, s := range m {
		s.Record(e)
	}
}

// Prices in USD per million tokens, [input, output], keyed "provider/model". Anthropic list prices
// from platform.claude.com pricing (Oct 2026). Muse and BYOK prices are unknown to the gateway
// until set with GATEWAY_PRICES (same JSON shape); unpriced calls are metered with cost 0 and
// priced=false, so token caps still apply but spend caps don't.
var defaultPrices = map[string][2]float64{
	"anthropic/claude-opus-5-5":   {4.00, 20.00},
	"anthropic/claude-sonnet-5-5": {2.00, 10.00},
	"anthropic/claude-haiku-5-5":  {0.10, 0.50},
}

func cost(prices map[string][2]float64, provider, model string, in, out int64) (float64, bool) {
	p, ok := prices[provider+"/"+model]
	if !ok {
		return 0, false
	}
	return (float64(in)*p[0] + float64(out)*p[1]) / 1e6, true
}
