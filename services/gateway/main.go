// Command gateway is ShipCube's AI gateway: an OpenAI-compatible endpoint for deployed apps that
// authenticates the app, applies its caps, injects the provider key from Secret Manager and meters
// usage. Configuration is by environment variable; see README.md.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"maps"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func durationEnv(k string, def time.Duration) (time.Duration, error) {
	v := os.Getenv(k)
	if v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil || d <= 0 {
		return 0, errors.New(k + " must be a positive duration like 300s")
	}
	return d, nil
}

func intEnv(k string, def int64) (int64, error) {
	v := os.Getenv(k)
	if v == "" {
		return def, nil
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil || n < 0 {
		return 0, errors.New(k + " must be a non-negative integer")
	}
	return n, nil
}

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		// Cloud Logging reads "severity" and "message".
		ReplaceAttr: func(_ []string, a slog.Attr) slog.Attr {
			switch a.Key {
			case slog.LevelKey:
				a.Key = "severity"
				if lv, ok := a.Value.Any().(slog.Level); ok && lv >= levelCritical {
					a.Value = slog.StringValue("CRITICAL")
				}
			case slog.MessageKey:
				a.Key = "message"
			}
			return a
		},
	}))
	if err := run(log); err != nil {
		log.Error("gateway stopped", "error", err.Error())
		os.Exit(1)
	}
}

// levelCritical is logged as Cloud Logging's CRITICAL severity.
const levelCritical = slog.LevelError + 4

func run(log *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	if onCloudRun(os.Getenv) {
		if problems := productionProblems(os.Getenv); len(problems) > 0 {
			for _, p := range problems {
				log.Log(ctx, levelCritical, "refusing to start on Cloud Run", "problem", p)
			}
			return errors.New("refusing to start on Cloud Run: " + strings.Join(problems, "; "))
		}
	}

	dev := os.Getenv("DEV_MODE") == "1"
	headerTimeout, err := durationEnv("GATEWAY_UPSTREAM_HEADER_TIMEOUT", 300*time.Second)
	if err != nil {
		return err
	}
	requestTimeout, err := durationEnv("GATEWAY_REQUEST_TIMEOUT", 15*time.Minute)
	if err != nil {
		return err
	}
	retries, err := strconv.Atoi(env("GATEWAY_UPSTREAM_RETRIES", "2"))
	if err != nil || retries < 0 {
		return errors.New("GATEWAY_UPSTREAM_RETRIES must be a non-negative integer")
	}
	meta := &metadataToken{client: &http.Client{Timeout: 5 * time.Second}}
	plain := &http.Client{Timeout: 15 * time.Second}

	auth := &Authenticator{Tokens: os.Getenv("GATEWAY_ALLOW_APP_TOKENS") == "1"}
	if aud := os.Getenv("GATEWAY_AUDIENCE"); aud != "" {
		auth.Verifier = &GoogleVerifier{Audience: aud, Client: plain}
	}
	if auth.Verifier == nil && !auth.Tokens {
		return errors.New("set GATEWAY_AUDIENCE (ID-token auth) and/or GATEWAY_ALLOW_APP_TOKENS=1")
	}

	var reg Registry
	switch {
	case os.Getenv("GATEWAY_API_URL") != "":
		reg = &HTTPRegistry{Base: os.Getenv("GATEWAY_API_URL"), Client: plain, Token: meta.get}
	case os.Getenv("GATEWAY_REGISTRY_FILE") != "":
		r, err := LoadFileRegistry(os.Getenv("GATEWAY_REGISTRY_FILE"))
		if err != nil {
			return err
		}
		reg = r
	default:
		return errors.New("set GATEWAY_API_URL or GATEWAY_REGISTRY_FILE")
	}

	sm := &SecretManager{Client: plain, Token: func(ctx context.Context) (string, error) {
		return meta.get(ctx, "")
	}}
	// A local Secret Manager emulator (infra/local, the local stack) only in DEV_MODE; production
	// refuses any endpoint but Google's, so a stray env var can't redirect key reads.
	if ep := os.Getenv("SECRET_MANAGER_ENDPOINT"); ep != "" {
		if !dev && !strings.HasPrefix(ep, "https://secretmanager.googleapis.com/") {
			return errors.New("SECRET_MANAGER_ENDPOINT may point elsewhere than secretmanager.googleapis.com only with DEV_MODE=1")
		}
		sm.Endpoint = strings.TrimRight(ep, "/")
		if dev {
			sm.Token = func(context.Context) (string, error) { return "local", nil } // the emulator takes no credentials
		}
	}
	var secrets SecretSource = sm
	if dev {
		secrets = EnvSecrets{Next: secrets}
	}

	prices := maps.Clone(defaultPrices)
	if p := os.Getenv("GATEWAY_PRICES"); p != "" {
		var extra map[string][2]float64
		if err := json.Unmarshal([]byte(p), &extra); err != nil {
			return errors.New("GATEWAY_PRICES must be JSON like {\"meta/muse-spark\": [0.5, 1.5]}")
		}
		maps.Copy(prices, extra)
	}

	sinks := multiSink{LogSink{Log: log}}
	if u := os.Getenv("GATEWAY_USAGE_URL"); u != "" {
		hs := &HTTPSink{URL: u, Client: plain, Token: meta.get, Audience: env("GATEWAY_API_URL", u), Log: log}
		go hs.Run(ctx, 10*time.Second)
		sinks = append(sinks, hs)
	}

	allowPrivate := dev && os.Getenv("GATEWAY_ALLOW_PRIVATE_UPSTREAM") == "1"
	if os.Getenv("META_SECRET_REF") == "" && !dev {
		return errors.New("META_SECRET_REF is required (the mock provider is only available with DEV_MODE=1)")
	}
	// Caps on the platform key (#20): defaults for apps without their own, and per workspace.
	var lim [5]int64
	for i, kv := range []struct {
		k   string
		def int64
	}{{"GATEWAY_DEFAULT_TOKENS_PER_DAY", 200_000}, {"GATEWAY_DEFAULT_REQUESTS_PER_MINUTE", 60},
		{"GATEWAY_WORKSPACE_TOKENS_PER_DAY", 1_000_000}, {"GATEWAY_WORKSPACE_REQUESTS_PER_MINUTE", 300},
		{"GATEWAY_MAX_OUTPUT_TOKENS", defaultMaxOutputTokens}} {
		if lim[i], err = intEnv(kv.k, kv.def); err != nil {
			return err
		}
	}
	metaModels := map[string]bool{}
	for _, m := range strings.Split(os.Getenv("GATEWAY_META_MODELS"), ",") {
		if m = strings.TrimSpace(m); m != "" {
			metaModels[m] = true
		}
	}
	limiter := &Limiter{DefaultTokensPerDay: lim[0], DefaultRequestsPerMin: int(lim[1]),
		WorkspaceTokensPerDay: lim[2], WorkspaceRequestsPerMin: int(lim[3])}
	// Shared caps through the control-plane API (#20). Without it, each instance counts on its own.
	var shared *SharedLimiter
	if u := os.Getenv("GATEWAY_COUNTERS_URL"); u != "" {
		shared = &SharedLimiter{URL: u, Client: plain, Token: meta.get, Local: limiter, Log: log}
	} else if !dev {
		log.Warn("GATEWAY_COUNTERS_URL is not set: caps are per instance and restart with it")
	}
	g := &Gateway{
		Auth: auth, Registry: reg, Secrets: secrets, Limiter: limiter, Shared: shared, Usage: sinks,
		MaxOutputTokens: lim[4], MetaModels: metaModels,
		Upstream: guardedClient(allowPrivate, headerTimeout), Prices: prices, Log: log,
		MetaBaseURL:      env("META_BASE_URL", "https://api.meta.ai/v1"),
		OpenAIBaseURL:    env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
		AnthropicBaseURL: env("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
		MetaSecretRef:    os.Getenv("META_SECRET_REF"),
		AllowPrivate:     allowPrivate,
		DevMode:          dev,
		Retry:            RetryPolicy{Retries: retries, Base: 500 * time.Millisecond, MaxDelay: 10 * time.Second},
		RequestTimeout:   requestTimeout,
		Project:          os.Getenv("GOOGLE_CLOUD_PROJECT"),
	}
	if dev {
		log.Warn("DEV_MODE=1: env: secret refs and the mock provider are enabled; never set this in production")
	}

	srv := &http.Server{
		Addr:              ":" + env("PORT", "8080"),
		Handler:           g.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: streamed completions can run for minutes (Cloud Run's request timeout applies).
	}
	errc := make(chan error, 1)
	go func() { errc <- srv.ListenAndServe() }()
	log.Info("gateway listening", "addr", srv.Addr)
	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return srv.Shutdown(shutdown)
	}
}
