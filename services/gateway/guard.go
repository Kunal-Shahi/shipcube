package main

import (
	"net/url"
	"path/filepath"
	"strings"
)

// The gateway refuses to start on Cloud Run with anything meant for local development (#29):
// dev mode (env: secret refs, the mock provider), private upstreams, a non-Google Secret Manager
// endpoint, missing auth, a missing platform key or shared caps, local URLs, or the example
// registry. Off Cloud Run nothing changes, so local development and tests behave as before.

var cloudRunVars = []string{"K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"}

func onCloudRun(getenv func(string) string) bool {
	for _, k := range cloudRunVars {
		if getenv(k) != "" {
			return true
		}
	}
	return false
}

// Settings that hold a URL the gateway calls or trusts. Unset is fine for the optional ones; set
// ones must be public https.
var urlSettings = []string{"GATEWAY_AUDIENCE", "GATEWAY_API_URL", "GATEWAY_USAGE_URL", "GATEWAY_COUNTERS_URL",
	"SECRET_MANAGER_ENDPOINT", "META_BASE_URL", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"}

func isLocalHost(host string) bool {
	h := strings.ToLower(strings.Trim(host, "[]"))
	switch h {
	case "localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal":
		return true
	}
	return strings.HasSuffix(h, ".localhost") || strings.HasSuffix(h, ".local")
}

// productionProblems lists why this process must not serve on Cloud Run; empty when it may.
func productionProblems(getenv func(string) string) []string {
	var out []string
	for _, k := range []string{"DEV_MODE", "SHIPCUBE_DEV_MODE"} {
		if v := strings.ToLower(getenv(k)); v == "1" || v == "true" {
			out = append(out, k+"=1: dev mode (env: secret refs, the mock provider) is for local development only")
		}
	}
	if getenv("GATEWAY_ALLOW_PRIVATE_UPSTREAM") == "1" {
		out = append(out, "GATEWAY_ALLOW_PRIVATE_UPSTREAM=1 lets apps reach private addresses")
	}
	if getenv("GATEWAY_AUDIENCE") == "" {
		out = append(out, "GATEWAY_AUDIENCE is unset: apps must authenticate with Google ID tokens")
	}
	if ref := getenv("META_SECRET_REF"); !strings.HasPrefix(ref, "projects/") || !strings.Contains(ref, "/secrets/") {
		out = append(out, "META_SECRET_REF must be a Secret Manager reference (projects/…/secrets/…)")
	}
	if getenv("GATEWAY_COUNTERS_URL") == "" {
		out = append(out, "GATEWAY_COUNTERS_URL is unset: caps would be per instance and reset on restart")
	}
	if f := getenv("GATEWAY_REGISTRY_FILE"); f != "" && (strings.Contains(filepath.Base(f), "example") || strings.Contains(f, "testdata")) {
		out = append(out, "GATEWAY_REGISTRY_FILE points at the example registry: "+f)
	}
	for _, k := range urlSettings {
		v := getenv(k)
		if v == "" {
			continue
		}
		u, err := url.Parse(v)
		if err != nil || u.Scheme != "https" || u.Host == "" || isLocalHost(u.Hostname()) {
			out = append(out, k+"="+v+" is not a public https URL")
		}
	}
	if ep := getenv("SECRET_MANAGER_ENDPOINT"); ep != "" && !strings.HasPrefix(ep, "https://secretmanager.googleapis.com/") {
		out = append(out, "SECRET_MANAGER_ENDPOINT must be https://secretmanager.googleapis.com/…")
	}
	return out
}
