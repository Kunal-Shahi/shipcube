package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// SecretSource returns a provider key for a Secret Manager reference. Values are never logged.
type SecretSource interface {
	Get(ctx context.Context, ref string) (string, error)
}

// SecretManager reads pinned versions over the REST API with the gateway's own credentials and
// caches them for TTL. If Secret Manager is down, a cached value keeps working until StaleFor runs
// out (design §1.4: "cached keys keep working for their TTL").
type SecretManager struct {
	Client   *http.Client
	Token    func(ctx context.Context) (string, error)
	TTL      time.Duration
	StaleFor time.Duration
	Endpoint string // default https://secretmanager.googleapis.com/v1

	mu    sync.Mutex
	cache map[string]secretEntry
}

type secretEntry struct {
	value string
	at    time.Time
}

func (s *SecretManager) Get(ctx context.Context, ref string) (string, error) {
	if !strings.HasPrefix(ref, "projects/") || !strings.Contains(ref, "/secrets/") {
		return "", fmt.Errorf("invalid secret reference")
	}
	ttl, stale := s.TTL, s.StaleFor
	if ttl == 0 {
		ttl = 5 * time.Minute
	}
	if stale == 0 {
		stale = 30 * time.Minute
	}
	s.mu.Lock()
	e, ok := s.cache[ref]
	s.mu.Unlock()
	if ok && time.Since(e.at) < ttl {
		return e.value, nil
	}
	v, err := s.access(ctx, ref)
	if err != nil {
		if ok && time.Since(e.at) < stale {
			return e.value, nil
		}
		return "", err
	}
	s.mu.Lock()
	if s.cache == nil {
		s.cache = map[string]secretEntry{}
	}
	s.cache[ref] = secretEntry{v, time.Now()}
	s.mu.Unlock()
	return v, nil
}

func (s *SecretManager) access(ctx context.Context, ref string) (string, error) {
	base := s.Endpoint
	if base == "" {
		base = "https://secretmanager.googleapis.com/v1"
	}
	tok, err := s.Token(ctx)
	if err != nil {
		return "", err
	}
	resp, _, err := internalRetry.Do(ctx, s.Client, func() (*http.Request, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/"+ref+":access", nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		return req, nil
	})
	if err != nil {
		return "", fmt.Errorf("secret manager: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		// The resource name is not secret; the response body is not echoed.
		return "", fmt.Errorf("secret manager returned %d for %s", resp.StatusCode, ref)
	}
	var body struct {
		Payload struct {
			Data string `json:"data"`
		} `json:"payload"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "", err
	}
	b, err := base64.StdEncoding.DecodeString(body.Payload.Data)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(b)), nil
}

// EnvSecrets resolves "env:NAME" references from the environment. Local development only: main
// enables it when GATEWAY_DEV=1.
type EnvSecrets struct{ Next SecretSource }

func (e EnvSecrets) Get(ctx context.Context, ref string) (string, error) {
	if name, ok := strings.CutPrefix(ref, "env:"); ok {
		if v := os.Getenv(name); v != "" {
			return v, nil
		}
		return "", fmt.Errorf("environment variable %s is empty", name)
	}
	if e.Next == nil {
		return "", errors.New("no secret source for reference")
	}
	return e.Next.Get(ctx, ref)
}

// metadataToken returns an access token (audience "") or ID token (audience set) for the
// service's own service account from the Cloud Run metadata server. Locally, set
// GOOGLE_OAUTH_ACCESS_TOKEN (e.g. from `gcloud auth print-access-token`).
type metadataToken struct {
	client *http.Client
	mu     sync.Mutex
	tokens map[string]secretEntry
}

func (m *metadataToken) get(ctx context.Context, audience string) (string, error) {
	if audience == "" {
		if v := os.Getenv("GOOGLE_OAUTH_ACCESS_TOKEN"); v != "" {
			return v, nil
		}
	}
	m.mu.Lock()
	if t, ok := m.tokens[audience]; ok && time.Since(t.at) < 45*time.Minute {
		m.mu.Unlock()
		return t.value, nil
	}
	m.mu.Unlock()

	u := "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
	if audience != "" {
		u = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=" + audience
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := m.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("metadata server returned %d", resp.StatusCode)
	}
	var tok string
	if audience == "" {
		var b struct {
			AccessToken string `json:"access_token"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&b); err != nil {
			return "", err
		}
		tok = b.AccessToken
	} else {
		b, err := io.ReadAll(io.LimitReader(resp.Body, 8192))
		if err != nil {
			return "", err
		}
		tok = strings.TrimSpace(string(b))
	}
	m.mu.Lock()
	if m.tokens == nil {
		m.tokens = map[string]secretEntry{}
	}
	m.tokens[audience] = secretEntry{tok, time.Now()}
	m.mu.Unlock()
	return tok, nil
}
