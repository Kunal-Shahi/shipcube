package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

type mapSecrets map[string]string

func (m mapSecrets) Get(_ context.Context, ref string) (string, error) {
	if v, ok := m[ref]; ok {
		return v, nil
	}
	return "", errors.New("no such secret")
}

func envelope(provider, baseURL string) string {
	if baseURL == "" {
		return fmt.Sprintf(`{"shipcube_ai_key":1,"provider":%q,"base_url":null,"key":%q}`, provider, testKey)
	}
	return fmt.Sprintf(`{"shipcube_ai_key":1,"provider":%q,"base_url":%q,"key":%q}`, provider, baseURL, testKey)
}

func TestKeyRefsMustBeThisWorkspacesAIKeys(t *testing.T) {
	ok := "projects/p/secrets/ws-ai-ws-1-0a1b-2c3d/versions/4"
	if err := checkKeyRef(ok, "ws-1", false); err != nil {
		t.Fatal(err)
	}
	for _, ref := range []string{
		"projects/p/secrets/ws-ai-ws-2-0a1b/versions/4",                 // another workspace
		"projects/p/secrets/ws-ws-1-warehouse_credential-0a/versions/1", // another kind
		"projects/p/secrets/ws-ws-1-ai_key-0a1b/versions/4",             // the old naming
		"projects/p/secrets/warehouse-primary/versions/1",
		"projects/p/secrets/ws-ai-ws-1-0a/versions/latest",
		"env:TEST_PROVIDER_KEY", // only in DEV_MODE
	} {
		if err := checkKeyRef(ref, "ws-1", false); !errors.Is(err, errKeyNotBound) {
			t.Errorf("%s: want refused, got %v", ref, err)
		}
	}
	if err := checkKeyRef("env:X", "ws-1", true); err != nil {
		t.Fatal(err)
	}
}

func TestBoundKeyOnlyGoesWhereItWasSaved(t *testing.T) {
	ref := "projects/p/secrets/ws-ai-ws-1-1/versions/1"
	cases := []struct {
		raw, kind, base string
		ok              bool
	}{
		{envelope("openai_compatible", "https://llm.example.com/v1"), ProviderOpenAICompatible, "https://llm.example.com/v1/", true},
		{envelope("openai_compatible", "https://llm.example.com/v1"), ProviderOpenAICompatible, "https://collector.example.net/v1", false},
		{envelope("openai", ""), ProviderOpenAI, "https://api.openai.com/v1", true},
		{envelope("anthropic", ""), ProviderOpenAI, "https://api.openai.com/v1", false},            // saved for another provider
		{envelope("openai", ""), ProviderOpenAICompatible, "https://collector.example.net", false}, // no endpoint bound
		{testKey, ProviderOpenAI, "https://api.openai.com/v1", false},                              // a plain, unbound value
		{`{"shipcube_ai_key":2,"provider":"openai","key":"x"}`, ProviderOpenAI, "", false},
	}
	for i, c := range cases {
		key, err := boundKey(c.raw, ref, c.kind, c.base, false)
		if c.ok && (err != nil || key != testKey) {
			t.Errorf("case %d: want key, got %q %v", i, key, err)
		}
		if !c.ok && !errors.Is(err, errKeyNotBound) {
			t.Errorf("case %d: want refused, got %q %v", i, key, err)
		}
	}
}

func TestAKeyIsNeverSentToAnEndpointItWasNotSavedWith(t *testing.T) {
	called := false
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true }))
	defer up.Close()
	ref := "projects/p/secrets/ws-ai-ws-1-1/versions/1"
	h := newHarness(t, ProviderConfig{Kind: ProviderOpenAICompatible, Model: "m", BaseURL: up.URL + "/v1", SecretRef: ref}, Caps{}, "")
	h.g.Secrets = mapSecrets{ref: envelope("openai_compatible", "https://llm.example.com/v1")}
	resp := h.post(t, chatBody)
	if resp.StatusCode != http.StatusForbidden || called {
		t.Fatalf("status %d, upstream called %v", resp.StatusCode, called)
	}

	// The endpoint it was saved with works.
	h.g.Secrets = mapSecrets{ref: envelope("openai_compatible", up.URL+"/v1")}
	up.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testKey {
			t.Errorf("auth %q", r.Header.Get("Authorization"))
		}
		_, _ = w.Write([]byte(`{"id":"c1","object":"chat.completion","choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}`))
	})
	if resp := h.post(t, chatBody); resp.StatusCode != 200 {
		t.Fatalf("status %d", resp.StatusCode)
	}

	// Another workspace's key is refused before it's read.
	other := "projects/p/secrets/ws-ai-ws-2-1/versions/1"
	h2 := newHarness(t, ProviderConfig{Kind: ProviderOpenAI, Model: "m", SecretRef: other}, Caps{}, up.URL)
	h2.g.Secrets = mapSecrets{other: envelope("openai", "")}
	if resp := h2.post(t, chatBody); resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

// The exact name the API builds (workspace.py: ws-ai-<workspace uuid>-<uuid4>), checked from both
// sides: tests/test_console_api.py also applies this file's pattern to a name the API created.
func TestTheAPIsKeyNameMatches(t *testing.T) {
	ws := "3c1d5e2a-7b4f-4e8a-9c6d-2f1e0a9b8c7d"
	ref := "projects/123/secrets/ws-ai-" + ws + "-9f0e8d7c-6b5a-4f3e-8d2c-1b0a9f8e7d6c/versions/1"
	if err := checkKeyRef(ref, ws, false); err != nil {
		t.Fatal(err)
	}
	if err := checkKeyRef(ref, "9f0e8d7c-6b5a-4f3e-8d2c-1b0a9f8e7d6c", false); err == nil {
		t.Fatal("a key must not match a workspace whose id merely appears later in the name")
	}
}
