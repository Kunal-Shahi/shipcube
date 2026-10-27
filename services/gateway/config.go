package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"
)

// Provider kinds, matching provider_configs.provider in docs/research/database-design.md.
const (
	ProviderMeta             = "meta"
	ProviderOpenAICompatible = "openai_compatible"
	ProviderOpenAI           = "openai"
	ProviderAnthropic        = "anthropic"
	ProviderMock             = "mock" // built in; used when no Meta key is configured yet
)

// ProviderConfig is the resolved provider for one app: its provider_configs row, else the
// workspace default, else Meta.
type ProviderConfig struct {
	Kind    string `json:"provider"`
	Model   string `json:"model"`
	BaseURL string `json:"base_url,omitempty"`
	// SecretRef is a pinned Secret Manager version, projects/P/secrets/S/versions/N.
	// Empty for Meta, which uses the platform key.
	SecretRef string `json:"secret_ref,omitempty"`
}

// Caps are the AI-relevant rows of the caps table. Zero means no limit.
type Caps struct {
	RequestsPerMinute int     `json:"requests_per_minute,omitempty"`
	AITokensPerDay    int64   `json:"ai_tokens_per_day,omitempty"`
	SpendUSDPerMonth  float64 `json:"spend_usd_per_month,omitempty"`
}

// AppConfig is everything the gateway needs to serve one calling app.
type AppConfig struct {
	AppID       string         `json:"app_id"`
	WorkspaceID string         `json:"workspace_id"`
	App         string         `json:"app"`
	Provider    ProviderConfig `json:"provider_config"`
	Caps        Caps           `json:"caps"`
	// Usage already recorded today and this month (from usage_daily), so caps survive restarts.
	UsedTokensToday int64   `json:"used_tokens_today,omitempty"`
	SpentUSDMonth   float64 `json:"spent_usd_month,omitempty"`
}

// Principal is who called: a service-account email from a Google ID token, or "token:<sha256>"
// for a per-app gateway token. AppHeader is X-ShipCube-App, needed when apps share an SA
// (linked projects use shipcube-apps@<project> for every app).
type Principal struct {
	Subject   string
	AppHeader string
}

var ErrUnknownApp = errors.New("no app is registered for this caller")

type Registry interface {
	Lookup(ctx context.Context, p Principal) (*AppConfig, error)
}

// FileRegistry reads apps from a JSON file: for local development, tests and the demo before the
// API serves the resolve endpoint.
type FileRegistry struct {
	Apps []FileApp `json:"apps"`
}

type FileApp struct {
	AppConfig
	// Principal is the runtime SA email, or "token:<sha256 hex of the gateway token>".
	Principal string `json:"principal"`
}

func LoadFileRegistry(path string) (*FileRegistry, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var r FileRegistry
	if err := json.Unmarshal(b, &r); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &r, nil
}

func (r *FileRegistry) Lookup(_ context.Context, p Principal) (*AppConfig, error) {
	var match []*AppConfig
	for i := range r.Apps {
		a := &r.Apps[i]
		if a.Principal == p.Subject {
			match = append(match, &a.AppConfig)
		}
	}
	return pick(match, p.AppHeader)
}

// pick chooses among apps that share a principal. One app needs no header; several need
// X-ShipCube-App to name one of them.
func pick(apps []*AppConfig, header string) (*AppConfig, error) {
	if len(apps) == 1 && (header == "" || header == apps[0].App) {
		return apps[0], nil
	}
	for _, a := range apps {
		if header != "" && a.App == header {
			return a, nil
		}
	}
	return nil, ErrUnknownApp
}

// HTTPRegistry asks the control-plane API, which owns the database:
//
//	GET {base}/internal/gateway/resolve?principal=...&app=...  ->  AppConfig JSON (404 when unknown)
//
// The gateway authenticates with an ID token for its own service account. Results are cached for
// 30 seconds, which bounds how long a provider or cap change takes to apply.
type HTTPRegistry struct {
	Base   string
	Client *http.Client
	Token  func(ctx context.Context, audience string) (string, error)
	TTL    time.Duration

	mu    sync.Mutex
	cache map[Principal]cached
}

type cached struct {
	cfg *AppConfig
	err error
	at  time.Time
}

func (r *HTTPRegistry) Lookup(ctx context.Context, p Principal) (*AppConfig, error) {
	ttl := r.TTL
	if ttl == 0 {
		ttl = 30 * time.Second
	}
	r.mu.Lock()
	if c, ok := r.cache[p]; ok && time.Since(c.at) < ttl {
		r.mu.Unlock()
		return c.cfg, c.err
	}
	r.mu.Unlock()

	cfg, err := r.fetch(ctx, p)
	if err != nil && !errors.Is(err, ErrUnknownApp) {
		return nil, err // don't cache transient failures
	}
	r.mu.Lock()
	if r.cache == nil {
		r.cache = map[Principal]cached{}
	}
	r.cache[p] = cached{cfg, err, time.Now()}
	r.mu.Unlock()
	return cfg, err
}

func (r *HTTPRegistry) fetch(ctx context.Context, p Principal) (*AppConfig, error) {
	q := url.Values{"principal": {p.Subject}}
	if p.AppHeader != "" {
		q.Set("app", p.AppHeader)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, r.Base+"/internal/gateway/resolve?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	if r.Token != nil {
		tok, err := r.Token(ctx, r.Base)
		if err != nil {
			return nil, fmt.Errorf("identity token for the API: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	resp, err := r.Client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound:
		return nil, ErrUnknownApp
	case resp.StatusCode != http.StatusOK:
		return nil, fmt.Errorf("resolve returned %d", resp.StatusCode)
	}
	var cfg AppConfig
	if err := json.NewDecoder(resp.Body).Decode(&cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}
