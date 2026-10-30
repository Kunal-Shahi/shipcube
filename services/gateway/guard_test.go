package main

import (
	"io"
	"log/slog"
	"strings"
	"testing"
)

// The live shipcube-gateway service's settings (Oct 31, 2026), with the secret reference's names
// replaced but its shape kept. The guard must let exactly this start.
var liveGateway = map[string]string{
	"K_SERVICE":             "shipcube-gateway",
	"GATEWAY_AUDIENCE":      "https://shipcube-gateway-340481939948.asia-south1.run.app",
	"GATEWAY_REGISTRY_FILE": "/etc/shipcube/registry/registry.json",
	"META_SECRET_REF":       "projects/shipcube-control-ks/secrets/meta-api-key/versions/1",
	"GOOGLE_CLOUD_PROJECT":  "shipcube-control-ks",
	"GATEWAY_PRICES":        `{"meta/muse-spark-1.3": [1.25, 4.25], "meta/muse-spark": [1.25, 4.25]}`,
	"GATEWAY_COUNTERS_URL":  "https://shipcube-mcp-340481939948.asia-south1.run.app",
}

func envOf(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func with(base map[string]string, kv ...string) map[string]string {
	out := map[string]string{}
	for k, v := range base {
		out[k] = v
	}
	for i := 0; i < len(kv); i += 2 {
		if kv[i+1] == "" {
			delete(out, kv[i])
		} else {
			out[kv[i]] = kv[i+1]
		}
	}
	return out
}

func TestTheLiveGatewayPassesTheGuard(t *testing.T) {
	if p := productionProblems(envOf(liveGateway)); len(p) != 0 {
		t.Fatalf("the deployed gateway would be refused: %v", p)
	}
}

func TestEachLocalSettingIsRefused(t *testing.T) {
	for _, tc := range []struct {
		kv     []string
		expect string
	}{
		{[]string{"DEV_MODE", "1"}, "DEV_MODE=1"},
		{[]string{"SHIPCUBE_DEV_MODE", "1"}, "SHIPCUBE_DEV_MODE=1"},
		{[]string{"DEV_MODE", "true"}, "DEV_MODE=1"},
		{[]string{"GATEWAY_ALLOW_PRIVATE_UPSTREAM", "1"}, "private addresses"},
		{[]string{"GATEWAY_AUDIENCE", ""}, "GATEWAY_AUDIENCE is unset"},
		{[]string{"META_SECRET_REF", ""}, "META_SECRET_REF"},
		{[]string{"META_SECRET_REF", "env:META_API_KEY"}, "META_SECRET_REF"},
		{[]string{"GATEWAY_COUNTERS_URL", ""}, "GATEWAY_COUNTERS_URL is unset"},
		{[]string{"GATEWAY_COUNTERS_URL", "http://localhost:8080"}, "GATEWAY_COUNTERS_URL=http://localhost:8080"},
		{[]string{"GATEWAY_API_URL", "http://api:8080"}, "GATEWAY_API_URL"},
		{[]string{"GATEWAY_USAGE_URL", "https://127.0.0.1/usage"}, "GATEWAY_USAGE_URL"},
		{[]string{"META_BASE_URL", "http://host.docker.internal:9000/v1"}, "META_BASE_URL"},
		{[]string{"OPENAI_BASE_URL", "https://llm.localhost/v1"}, "OPENAI_BASE_URL"},
		{[]string{"SECRET_MANAGER_ENDPOINT", "http://secretmanager:8085/v1"}, "SECRET_MANAGER_ENDPOINT"},
		{[]string{"GATEWAY_REGISTRY_FILE", "testdata/registry.example.json"}, "example registry"},
	} {
		got := strings.Join(productionProblems(envOf(with(liveGateway, tc.kv...))), "; ")
		if !strings.Contains(got, tc.expect) {
			t.Errorf("%v: want a problem mentioning %q, got %q", tc.kv, tc.expect, got)
		}
	}
}

func TestCloudRunIsDetectedByAnyOfItsVariables(t *testing.T) {
	if onCloudRun(envOf(map[string]string{})) {
		t.Fatal("no Cloud Run variables: not Cloud Run")
	}
	for _, k := range cloudRunVars {
		if !onCloudRun(envOf(map[string]string{k: "x"})) {
			t.Errorf("%s set: Cloud Run", k)
		}
	}
}

func TestRunRefusesDevModeOnCloudRun(t *testing.T) {
	for k, v := range with(liveGateway, "DEV_MODE", "1") {
		t.Setenv(k, v)
	}
	err := run(slog.New(slog.NewJSONHandler(io.Discard, nil)))
	if err == nil || !strings.Contains(err.Error(), "refusing to start on Cloud Run") {
		t.Fatalf("want a refusal, got %v", err)
	}
}

func TestDevModeStillWorksOffCloudRun(t *testing.T) {
	// No Cloud Run variables: the guard doesn't run, so local development is unchanged.
	if onCloudRun(envOf(map[string]string{"DEV_MODE": "1"})) {
		t.Fatal("DEV_MODE alone must not count as Cloud Run")
	}
}
