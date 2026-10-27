package main

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

// TestIntegrationRealMuse runs a real Muse call through the gateway, with the key read from real
// Secret Manager. It is skipped unless GATEWAY_IT_META_SECRET_REF is set:
//
//	GATEWAY_IT_META_SECRET_REF=projects/shipcube-control-ks/secrets/meta-api-key/versions/1 \
//	GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) go test -run Integration -v ./...
func TestIntegrationRealMuse(t *testing.T) {
	ref := os.Getenv("GATEWAY_IT_META_SECRET_REF")
	token := os.Getenv("GOOGLE_OAUTH_ACCESS_TOKEN")
	if ref == "" || token == "" {
		t.Skip("set GATEWAY_IT_META_SECRET_REF and GOOGLE_OAUTH_ACCESS_TOKEN to run against real GCP and Meta")
	}
	model := os.Getenv("GATEWAY_IT_MODEL")
	if model == "" {
		model = "muse-spark-1.3"
	}
	sink := &memSink{}
	g := &Gateway{
		Auth: &Authenticator{Tokens: true},
		Registry: &FileRegistry{Apps: []FileApp{{Principal: tokenPrincipal(appToken), AppConfig: AppConfig{
			AppID: "it", WorkspaceID: "it", App: "it", Provider: ProviderConfig{Kind: ProviderMeta, Model: model},
			Caps: Caps{RequestsPerMinute: 10, AITokensPerDay: 20000}}}}},
		Secrets: &SecretManager{Client: &http.Client{Timeout: 15 * time.Second},
			Token: func(context.Context) (string, error) { return token, nil }},
		Limiter: &Limiter{}, Usage: sink, Prices: defaultPrices,
		Upstream:       guardedClient(false, 120*time.Second),
		Log:            slog.New(slog.NewJSONHandler(io.Discard, nil)),
		MetaBaseURL:    "https://api.meta.ai/v1",
		MetaSecretRef:  ref,
		Retry:          RetryPolicy{Retries: 2, Base: 500 * time.Millisecond, MaxDelay: 10 * time.Second},
		RequestTimeout: 2 * time.Minute,
	}
	srv := httptest.NewServer(g.Handler())
	defer srv.Close()
	h := &harness{g: g, srv: srv, sink: sink}

	resp := h.post(t, `{"model":"default","max_tokens":2000,"messages":[{"role":"user","content":"Reply with the single word: pong"}]}`)
	body := readJSON(t, resp.Body)
	if resp.StatusCode != 200 {
		t.Fatalf("status %d: %v", resp.StatusCode, body)
	}
	content := body["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)["content"].(string)
	if !strings.Contains(strings.ToLower(content), "pong") {
		t.Errorf("unexpected reply %q", content)
	}
	if e := sink.last(t); e.InputTokens == 0 || e.OutputTokens == 0 || e.Estimated {
		t.Errorf("not metered from provider usage: %+v", e)
	}

	data := sseData(t, h.post(t, `{"model":"default","stream":true,"max_tokens":2000,"messages":[{"role":"user","content":"Say hi"}]}`).Body)
	if len(data) < 2 || data[len(data)-1] != "[DONE]" {
		t.Fatalf("stream %v", data)
	}
	if e := sink.last(t); e.OutputTokens == 0 {
		t.Errorf("stream not metered: %+v", e)
	}
}
