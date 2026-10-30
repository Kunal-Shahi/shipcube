package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// fakeCounters is the API's /internal/gateway/* in miniature: one token budget per app, shared by
// every gateway pointed at it.
type fakeCounters struct {
	mu      sync.Mutex
	used    map[string]int64
	auth    []string
	settled chan map[string]any
	down    bool
}

func (f *fakeCounters) server(t *testing.T) *httptest.Server {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.auth = append(f.auth, r.Header.Get("Authorization"))
		if f.down {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		switch r.URL.Path {
		case "/internal/gateway/reserve":
			app := body["app_id"].(string)
			limit := int64(body["limits"].(map[string]any)["app_tokens"].(float64))
			want := int64(body["want_out"].(float64)) + int64(body["in_tokens"].(float64))
			if limit > 0 && f.used[app]+want > limit {
				_ = json.NewEncoder(w).Encode(map[string]any{"cap": "ai_tokens_per_day", "limit": limit,
					"resets_at": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)})
				return
			}
			f.used[app] += want
			_ = json.NewEncoder(w).Encode(map[string]any{"granted": body["want_out"], "reservation": map[string]any{"app": app, "tokens": want}})
		case "/internal/gateway/settle":
			f.settled <- body
			w.WriteHeader(http.StatusNoContent)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func sharedHarness(t *testing.T, kind string, counters *httptest.Server, upstream string) *harness {
	pc := ProviderConfig{Kind: kind, Model: "m"}
	h := newHarness(t, pc, Caps{AITokensPerDay: 1000}, upstream)
	if kind == ProviderMeta {
		h.g.MetaSecretRef = "env:TEST_PROVIDER_KEY"
	}
	h.g.Shared = &SharedLimiter{URL: counters.URL, Client: http.DefaultClient, Local: h.g.Limiter,
		Token: func(_ context.Context, aud string) (string, error) { return "id-token-for-" + aud, nil }}
	return h
}

func okUpstream(t *testing.T) (*httptest.Server, *int) {
	calls := 0
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		_, _ = io.WriteString(w, `{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":5}}`)
	}))
	t.Cleanup(up.Close)
	return up, &calls
}

func TestTwoGatewaysShareOneBudget(t *testing.T) {
	f := &fakeCounters{used: map[string]int64{}, settled: make(chan map[string]any, 10)}
	api := f.server(t)
	up, _ := okUpstream(t)
	a, b := sharedHarness(t, ProviderMeta, api, up.URL), sharedHarness(t, ProviderMeta, api, up.URL)
	body := `{"max_tokens":600,"messages":[{"role":"user","content":"hi"}]}`
	if r := a.post(t, body); r.StatusCode != 200 {
		t.Fatalf("first instance: %d", r.StatusCode)
	}
	// The second instance sees the first's usage: its own memory would have let it through.
	if r := b.post(t, body); r.StatusCode != 429 {
		t.Fatalf("second instance: %d, want 429 from the shared cap", r.StatusCode)
	}
	select {
	case s := <-f.settled:
		if s["tokens"].(float64) != 10 || s["reservation"].(map[string]any)["app"] != "app-1" {
			t.Fatalf("settle %v", s)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no settle")
	}
	if f.auth[0] != "Bearer id-token-for-"+api.URL {
		t.Fatalf("auth %q: the gateway's ID token for the API's audience", f.auth[0])
	}
}

func TestUnreachableCapsFailClosedOnThePlatformKey(t *testing.T) {
	f := &fakeCounters{used: map[string]int64{}, settled: make(chan map[string]any, 10), down: true}
	api := f.server(t)
	up, calls := okUpstream(t)
	h := sharedHarness(t, ProviderMeta, api, up.URL)
	r := h.post(t, chatBody)
	if r.StatusCode != 503 || readJSON(t, r.Body)["error"].(map[string]any)["code"] != "caps_unavailable" || *calls != 0 {
		t.Fatalf("status %d, upstream calls %d", r.StatusCode, *calls)
	}
	// A BYOK request (the app's own key) uses this instance's counters, and says so in the logs.
	byok := sharedHarness(t, ProviderOpenAI, api, up.URL)
	if r := byok.post(t, chatBody); r.StatusCode != 200 {
		t.Fatalf("BYOK: %d", r.StatusCode)
	}
	if !bytesContains(byok.logs.Bytes(), "shared caps unreachable") {
		t.Fatal("the fallback must be logged")
	}
}

func bytesContains(b []byte, s string) bool { return bytes.Contains(b, []byte(s)) }
