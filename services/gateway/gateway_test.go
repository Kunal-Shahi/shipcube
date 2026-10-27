package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/big"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	appToken = "scg_test_tracker_token"
	testKey  = "sk-test-SECRET-key-123"
)

type memSink struct {
	mu     sync.Mutex
	events []UsageEvent
}

func (m *memSink) Record(e UsageEvent) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.events = append(m.events, e)
}

func (m *memSink) last(t *testing.T) UsageEvent {
	t.Helper()
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(m.events) == 0 {
		t.Fatal("no usage recorded")
	}
	return m.events[len(m.events)-1]
}

func tokenPrincipal(tok string) string {
	sum := sha256.Sum256([]byte(tok))
	return "token:" + hex.EncodeToString(sum[:])
}

type harness struct {
	g    *Gateway
	srv  *httptest.Server
	sink *memSink
	logs *bytes.Buffer
}

func newHarness(t *testing.T, pc ProviderConfig, caps Caps, upstream string) *harness {
	t.Helper()
	t.Setenv("TEST_PROVIDER_KEY", testKey)
	if pc.SecretRef == "" && pc.Kind != ProviderMeta && pc.Kind != ProviderMock {
		pc.SecretRef = "env:TEST_PROVIDER_KEY"
	}
	logs := &bytes.Buffer{}
	sink := &memSink{}
	g := &Gateway{
		Auth: &Authenticator{Tokens: true},
		Registry: &FileRegistry{Apps: []FileApp{{Principal: tokenPrincipal(appToken), AppConfig: AppConfig{
			AppID: "app-1", WorkspaceID: "ws-1", App: "tracker", Provider: pc, Caps: caps}}}},
		Secrets:          EnvSecrets{},
		Limiter:          &Limiter{},
		Usage:            multiSink{sink, LogSink{Log: slog.New(slog.NewJSONHandler(logs, nil))}},
		Upstream:         guardedClient(true),
		Prices:           defaultPrices,
		Log:              slog.New(slog.NewJSONHandler(logs, nil)),
		MetaBaseURL:      upstream,
		OpenAIBaseURL:    upstream,
		AnthropicBaseURL: upstream,
		AllowPrivate:     true,
	}
	srv := httptest.NewServer(g.Handler())
	t.Cleanup(srv.Close)
	return &harness{g, srv, sink, logs}
}

func (h *harness) post(t *testing.T, body string, hdr ...string) *http.Response {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, h.srv.URL+"/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+appToken)
	for i := 0; i+1 < len(hdr); i += 2 {
		req.Header.Set(hdr[i], hdr[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func readJSON(t *testing.T, r io.Reader) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.NewDecoder(r).Decode(&m); err != nil {
		t.Fatal(err)
	}
	return m
}

func sseData(t *testing.T, r io.Reader) []string {
	t.Helper()
	var out []string
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		if d, ok := strings.CutPrefix(sc.Text(), "data: "); ok {
			out = append(out, d)
		}
	}
	return out
}

const chatBody = `{"model":"default","messages":[{"role":"system","content":"Be brief."},{"role":"user","content":"hi"}]}`

func TestOpenAICompatibleForwardsWithKeyAndMeters(t *testing.T) {
	var got map[string]any
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+testKey {
			t.Errorf("unexpected upstream request %s %q", r.URL.Path, r.Header.Get("Authorization"))
		}
		got = readJSON(t, r.Body)
		_, _ = io.WriteString(w, `{"id":"c1","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"hello"}}],"usage":{"prompt_tokens":11,"completion_tokens":3}}`)
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderOpenAICompatible, Model: "llama-4", BaseURL: up.URL + "/v1"}, Caps{}, "")

	resp := h.post(t, chatBody)
	if resp.StatusCode != 200 || resp.Header.Get("X-ShipCube-Model") != "llama-4" {
		t.Fatalf("status %d model %q", resp.StatusCode, resp.Header.Get("X-ShipCube-Model"))
	}
	if body := readJSON(t, resp.Body); body["id"] != "c1" {
		t.Fatalf("body not passed through: %v", body)
	}
	if got["model"] != "llama-4" {
		t.Fatalf("model not resolved to the app's config: %v", got["model"])
	}
	e := h.sink.last(t)
	if e.InputTokens != 11 || e.OutputTokens != 3 || e.Provider != ProviderOpenAICompatible || e.Status != 200 {
		t.Fatalf("usage %+v", e)
	}
	if strings.Contains(h.logs.String(), testKey) {
		t.Fatal("provider key appeared in logs")
	}
}

func TestOpenAIStreamingPassesThroughAndMeters(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := readJSON(t, r.Body)
		if so, _ := b["stream_options"].(map[string]any); so["include_usage"] != true {
			t.Errorf("include_usage not requested: %v", b)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		for _, d := range []string{
			`{"choices":[{"index":0,"delta":{"content":"he"}}]}`,
			`{"choices":[{"index":0,"delta":{"content":"llo"}}]}`,
			`{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}`,
			`[DONE]`,
		} {
			fmt.Fprintf(w, "data: %s\n\n", d)
			w.(http.Flusher).Flush()
		}
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderOpenAI, Model: "gpt-x"}, Caps{}, up.URL)

	resp := h.post(t, `{"stream":true,"messages":[{"role":"user","content":"hi"}]}`)
	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("content type %q", ct)
	}
	data := sseData(t, resp.Body)
	if len(data) != 4 || data[3] != "[DONE]" {
		t.Fatalf("stream %v", data)
	}
	if e := h.sink.last(t); e.InputTokens != 5 || e.OutputTokens != 2 || !e.Stream {
		t.Fatalf("usage %+v", e)
	}
}

func TestAnthropicTranslation(t *testing.T) {
	var got map[string]any
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/messages" || r.Header.Get("x-api-key") != testKey ||
			r.Header.Get("anthropic-version") != anthropicVersion {
			t.Errorf("bad upstream request %s %v", r.URL.Path, r.Header)
		}
		got = readJSON(t, r.Body)
		_, _ = io.WriteString(w, `{"id":"msg_1","model":"claude-opus-5-5","stop_reason":"end_turn",
			"content":[{"type":"thinking","thinking":""},{"type":"text","text":"Hi there"}],
			"usage":{"input_tokens":1000000,"output_tokens":1000000}}`)
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderAnthropic, Model: "claude-opus-5-5"}, Caps{}, up.URL)

	resp := h.post(t, `{"temperature":0.2,"top_p":0.9,"stop":"END","messages":[{"role":"system","content":"Be brief."},
		{"role":"user","content":[{"type":"text","text":"hi"},{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}]}]}`)
	body := readJSON(t, resp.Body)
	if resp.StatusCode != 200 {
		t.Fatalf("status %d: %v", resp.StatusCode, body)
	}
	for _, k := range []string{"temperature", "top_p", "top_k"} {
		if _, ok := got[k]; ok {
			t.Errorf("%s must not be sent on anthropic-version %s", k, anthropicVersion)
		}
	}
	if got["system"] != "Be brief." || got["max_tokens"].(float64) != anthropicDefaultMaxTokens {
		t.Errorf("request %v", got)
	}
	if ss := got["stop_sequences"].([]any); ss[0] != "END" {
		t.Errorf("stop_sequences %v", ss)
	}
	msg := body["choices"].([]any)[0].(map[string]any)["message"].(map[string]any)
	if msg["content"] != "Hi there" {
		t.Fatalf("content %v", msg)
	}
	if e := h.sink.last(t); e.CostUSD != 24 || !e.Priced {
		t.Fatalf("cost %+v (want 1M in × $4 + 1M out × $20)", e)
	}
}

func TestAnthropicStreaming(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		for _, ev := range []string{
			`{"type":"message_start","message":{"id":"msg_2","model":"claude-opus-5-5","usage":{"input_tokens":9}}}`,
			`{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"..."}}`,
			`{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Hel"}}`,
			`{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"lo"}}`,
			`{"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":4}}`,
			`{"type":"message_stop"}`,
		} {
			var e struct{ Type string }
			_ = json.Unmarshal([]byte(ev), &e)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", e.Type, ev)
		}
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderAnthropic, Model: "claude-opus-5-5"}, Caps{}, up.URL)

	data := sseData(t, h.post(t, `{"stream":true,"messages":[{"role":"user","content":"hi"}]}`).Body)
	var text strings.Builder
	var finish any
	for _, d := range data[:len(data)-1] {
		var c struct {
			Choices []struct {
				Delta        struct{ Content string }
				FinishReason any `json:"finish_reason"`
			}
		}
		_ = json.Unmarshal([]byte(d), &c)
		for _, ch := range c.Choices {
			text.WriteString(ch.Delta.Content)
			if ch.FinishReason != nil {
				finish = ch.FinishReason
			}
		}
	}
	if text.String() != "Hello" || finish != "length" || data[len(data)-1] != "[DONE]" {
		t.Fatalf("text %q finish %v data %v", text.String(), finish, data)
	}
	if e := h.sink.last(t); e.InputTokens != 9 || e.OutputTokens != 4 {
		t.Fatalf("usage %+v", e)
	}
}

func TestAnthropicRejectsTools(t *testing.T) {
	h := newHarness(t, ProviderConfig{Kind: ProviderAnthropic, Model: "claude-opus-5-5"}, Caps{}, "http://unused")
	resp := h.post(t, `{"tools":[{}],"messages":[{"role":"user","content":"hi"}]}`)
	if resp.StatusCode != 400 {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestMetaWithoutKeyUsesMock(t *testing.T) {
	h := newHarness(t, ProviderConfig{Kind: ProviderMeta, Model: "muse-spark"}, Caps{}, "http://unused")
	resp := h.post(t, chatBody)
	if resp.StatusCode != 200 || resp.Header.Get("X-ShipCube-Provider") != ProviderMock {
		t.Fatalf("status %d provider %q", resp.StatusCode, resp.Header.Get("X-ShipCube-Provider"))
	}
	data := sseData(t, h.post(t, `{"stream":true,"messages":[{"role":"user","content":"hi"}]}`).Body)
	if data[len(data)-1] != "[DONE]" {
		t.Fatalf("mock stream %v", data)
	}
}

func TestRequestsPerMinuteCap(t *testing.T) {
	h := newHarness(t, ProviderConfig{Kind: ProviderMock, Model: "m"}, Caps{RequestsPerMinute: 2}, "")
	for i := 0; i < 2; i++ {
		if s := h.post(t, chatBody).StatusCode; s != 200 {
			t.Fatalf("request %d: %d", i, s)
		}
	}
	resp := h.post(t, chatBody)
	body := readJSON(t, resp.Body)
	errBody := body["error"].(map[string]any)
	if resp.StatusCode != 429 || errBody["cap"] != "requests_per_minute" || resp.Header.Get("Retry-After") == "" {
		t.Fatalf("status %d body %v", resp.StatusCode, body)
	}
	if e := h.sink.last(t); e.CapHit != "requests_per_minute" {
		t.Fatalf("cap hit not metered: %+v", e)
	}
}

func TestDailyTokenCapClampsThenBlocks(t *testing.T) {
	var maxTokens float64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		maxTokens, _ = readJSON(t, r.Body)["max_tokens"].(float64)
		_, _ = io.WriteString(w, `{"choices":[],"usage":{"prompt_tokens":60,"completion_tokens":40}}`)
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderOpenAICompatible, Model: "m", BaseURL: up.URL}, Caps{AITokensPerDay: 100}, "")

	if s := h.post(t, `{"max_tokens":5000,"messages":[{"role":"user","content":"hi"}]}`).StatusCode; s != 200 {
		t.Fatalf("status %d", s)
	}
	if maxTokens != 100 {
		t.Fatalf("max_tokens not clamped to the remaining cap: %v", maxTokens)
	}
	resp := h.post(t, chatBody)
	if resp.StatusCode != 429 || readJSON(t, resp.Body)["error"].(map[string]any)["cap"] != "ai_tokens_per_day" {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestSpendCap(t *testing.T) {
	l := &Limiter{Now: func() time.Time { return time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC) }}
	cfg := &AppConfig{AppID: "a", Caps: Caps{SpendUSDPerMonth: 1}, SpentUSDMonth: 0.9}
	if _, err := l.Allow(cfg); err != nil {
		t.Fatal(err)
	}
	l.Record(cfg, 10, 0.2)
	_, err := l.Allow(cfg)
	if err == nil || err.Cap != "spend_usd_per_month" || !err.ResetsAt.Equal(time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)) {
		t.Fatalf("got %v", err)
	}
}

func TestUpstreamErrorsRedactKey(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
		fmt.Fprintf(w, `{"error":{"message":"Incorrect API key provided: %s"}}`, testKey)
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderOpenAI, Model: "gpt-x"}, Caps{}, up.URL)
	resp := h.post(t, chatBody)
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 401 || strings.Contains(string(b), testKey) || !strings.Contains(string(b), "[REDACTED]") {
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
}

func TestAuthFailures(t *testing.T) {
	h := newHarness(t, ProviderConfig{Kind: ProviderMock, Model: "m"}, Caps{}, "")
	for name, tok := range map[string]string{"missing": "", "unknown app": "scg_someone_else", "not a jwt": "abc"} {
		req, _ := http.NewRequest(http.MethodPost, h.srv.URL+"/v1/chat/completions", strings.NewReader(chatBody))
		if tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		want := 401
		if name == "unknown app" {
			want = 403
		}
		if resp.StatusCode != want {
			t.Errorf("%s: status %d, want %d", name, resp.StatusCode, want)
		}
	}
}

func TestBYOKBaseURLGuard(t *testing.T) {
	for _, u := range []string{"http://api.example.com", "https://127.0.0.1", "https://169.254.169.254/v1",
		"https://10.0.0.8", "https://user:pw@api.example.com", "https://[::1]"} {
		if _, err := validateBaseURL(u, false); err == nil {
			t.Errorf("%s accepted", u)
		}
	}
	if _, err := validateBaseURL("https://api.fireworks.ai/inference/v1", false); err != nil {
		t.Error(err)
	}
	// The dialer refuses private addresses even when the hostname looked fine.
	local := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer local.Close()
	_, err := guardedClient(false).Get(strings.Replace(local.URL, "127.0.0.1", "localhost", 1))
	if !errors.Is(err, errBlockedAddress) {
		t.Fatalf("expected the dialer to block localhost, got %v", err)
	}
}

func TestSharedServiceAccountNeedsAppHeader(t *testing.T) {
	sa := "shipcube-apps@user-proj.iam.gserviceaccount.com"
	r := &FileRegistry{Apps: []FileApp{
		{Principal: sa, AppConfig: AppConfig{AppID: "1", App: "a"}},
		{Principal: sa, AppConfig: AppConfig{AppID: "2", App: "b"}},
	}}
	if _, err := r.Lookup(context.Background(), Principal{Subject: sa}); !errors.Is(err, ErrUnknownApp) {
		t.Fatal("ambiguous principal must need X-ShipCube-App")
	}
	if c, _ := r.Lookup(context.Background(), Principal{Subject: sa, AppHeader: "b"}); c == nil || c.AppID != "2" {
		t.Fatalf("got %v", c)
	}
}

// --- Google ID tokens -------------------------------------------------------------

func signJWT(t *testing.T, key *rsa.PrivateKey, kid string, claims map[string]any) string {
	t.Helper()
	enc := func(v any) string {
		b, _ := json.Marshal(v)
		return base64.RawURLEncoding.EncodeToString(b)
	}
	head := enc(map[string]string{"alg": "RS256", "kid": kid, "typ": "JWT"}) + "." + enc(claims)
	sum := sha256.Sum256([]byte(head))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, sum[:])
	if err != nil {
		t.Fatal(err)
	}
	return head + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func TestGoogleIDTokenVerification(t *testing.T) {
	key, _ := rsa.GenerateKey(rand.Reader, 2048)
	jwks := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []any{map[string]any{
			"kid": "k1", "kty": "RSA", "alg": "RS256",
			"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
			"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
		}}})
	}))
	defer jwks.Close()
	now := time.Now()
	v := &GoogleVerifier{Audience: "https://gw.example.run.app", JWKSURL: jwks.URL}
	claims := func(mod func(map[string]any)) map[string]any {
		c := map[string]any{"iss": "https://accounts.google.com", "aud": "https://gw.example.run.app",
			"exp": now.Add(time.Hour).Unix(), "iat": now.Unix(),
			"email": "app-tracker@shipcube-apps-ks.iam.gserviceaccount.com", "email_verified": true}
		if mod != nil {
			mod(c)
		}
		return c
	}
	email, err := v.Verify(context.Background(), signJWT(t, key, "k1", claims(nil)))
	if err != nil || email != "app-tracker@shipcube-apps-ks.iam.gserviceaccount.com" {
		t.Fatalf("valid token: %v %q", err, email)
	}
	other, _ := rsa.GenerateKey(rand.Reader, 2048)
	for name, tok := range map[string]string{
		"wrong audience": signJWT(t, key, "k1", claims(func(c map[string]any) { c["aud"] = "https://evil" })),
		"expired":        signJWT(t, key, "k1", claims(func(c map[string]any) { c["exp"] = now.Add(-time.Hour).Unix() })),
		"wrong issuer":   signJWT(t, key, "k1", claims(func(c map[string]any) { c["iss"] = "https://evil" })),
		"unverified":     signJWT(t, key, "k1", claims(func(c map[string]any) { c["email_verified"] = false })),
		"bad signature":  signJWT(t, other, "k1", claims(nil)),
		"unknown kid":    signJWT(t, key, "k9", claims(nil)),
	} {
		if _, err := v.Verify(context.Background(), tok); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

// --- Secret Manager -----------------------------------------------------------------

func TestSecretManagerCachesAndServesStale(t *testing.T) {
	calls, fail := 0, false
	sm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if fail {
			w.WriteHeader(503)
			return
		}
		if r.URL.Path != "/projects/p/secrets/ws-1-ai_key/versions/3:access" || r.Header.Get("Authorization") != "Bearer at" {
			t.Errorf("unexpected request %s", r.URL.Path)
		}
		fmt.Fprintf(w, `{"payload":{"data":%q}}`, base64.StdEncoding.EncodeToString([]byte(testKey+"\n")))
	}))
	defer sm.Close()
	s := &SecretManager{Client: sm.Client(), Endpoint: sm.URL, TTL: time.Nanosecond,
		Token: func(context.Context) (string, error) { return "at", nil }}
	ref := "projects/p/secrets/ws-1-ai_key/versions/3"
	if v, err := s.Get(context.Background(), ref); err != nil || v != testKey {
		t.Fatalf("%v %q", err, v)
	}
	fail = true
	if v, err := s.Get(context.Background(), ref); err != nil || v != testKey {
		t.Fatalf("stale value not served: %v", err)
	}
	if _, err := s.Get(context.Background(), "not-a-ref"); err == nil {
		t.Fatal("invalid ref accepted")
	}
	if calls != 2 {
		t.Fatalf("calls %d", calls)
	}
}

func TestHTTPRegistryCachesAndAuthenticates(t *testing.T) {
	calls := 0
	api := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer id-token" || r.URL.Query().Get("principal") != "sa@x" {
			t.Errorf("bad resolve request %v", r.URL)
		}
		if r.URL.Query().Get("app") == "missing" {
			w.WriteHeader(404)
			return
		}
		_ = json.NewEncoder(w).Encode(AppConfig{AppID: "a1", App: "tracker", Provider: ProviderConfig{Kind: "meta", Model: "muse-spark"}})
	}))
	defer api.Close()
	r := &HTTPRegistry{Base: api.URL, Client: api.Client(),
		Token: func(context.Context, string) (string, error) { return "id-token", nil }}
	for i := 0; i < 3; i++ {
		c, err := r.Lookup(context.Background(), Principal{Subject: "sa@x"})
		if err != nil || c.AppID != "a1" {
			t.Fatal(err)
		}
	}
	if _, err := r.Lookup(context.Background(), Principal{Subject: "sa@x", AppHeader: "missing"}); !errors.Is(err, ErrUnknownApp) {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("calls %d, want 2 (cached)", calls)
	}
}

func TestModelsEndpoint(t *testing.T) {
	h := newHarness(t, ProviderConfig{Kind: ProviderMeta, Model: "muse-spark"}, Caps{}, "")
	req, _ := http.NewRequest(http.MethodGet, h.srv.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+appToken)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if m := readJSON(t, resp.Body); m["data"].([]any)[0].(map[string]any)["id"] != "muse-spark" {
		t.Fatalf("%v", m)
	}
}
