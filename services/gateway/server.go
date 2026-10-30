package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"time"
)

const maxRequestBody = 8 << 20

// Gateway is the OpenAI-compatible AI gateway that deployed apps call (services/gateway/DESIGN.md §1).
type Gateway struct {
	Auth     *Authenticator
	Registry Registry
	Secrets  SecretSource
	Limiter  *Limiter
	// Shared, when set, keeps the caps in the control-plane API so every instance shares them (#20).
	// If it can't be reached, platform-key requests fail with 503; BYOK requests use Limiter, logged.
	Shared *SharedLimiter
	Usage    UsageSink
	Upstream *http.Client
	Prices   map[string][2]float64
	Log      *slog.Logger

	MetaBaseURL      string
	OpenAIBaseURL    string
	AnthropicBaseURL string
	// MetaSecretRef is the platform's Meta key (Secret Manager). Required outside DEV_MODE.
	MetaSecretRef string
	// AllowPrivate lets BYOK base URLs reach private addresses. Tests and local development only.
	AllowPrivate bool
	// DevMode (DEV_MODE=1, local only) allows the built-in mock provider. In production a request
	// that would need it fails instead of returning mock output.
	DevMode bool
	// Retry is applied to provider calls before any byte is sent to the app.
	Retry RetryPolicy
	// RequestTimeout bounds one whole chat request, streaming included.
	RequestTimeout time.Duration
	// Project is the GCP project, for Cloud Logging trace correlation.
	Project string
	// MaxOutputTokens bounds every request's output tokens (max_tokens / max_completion_tokens),
	// whatever the app asks for. 0 means defaultMaxOutputTokens.
	MaxOutputTokens int64
	// MetaModels are the models apps may request on the platform key, besides their configured one.
	MetaModels map[string]bool
}

const defaultMaxOutputTokens = anthropicDefaultMaxTokens // room for reasoning models (Muse)

func (g *Gateway) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/chat/completions", g.chat)
	mux.HandleFunc("GET /v1/models", g.models)
	health := func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "ok\n")
	}
	// /health too: Cloud Run's front end reserves /healthz on run.app URLs (external checks use /health).
	mux.HandleFunc("GET /healthz", health)
	mux.HandleFunc("GET /health", health)
	return withRequestContext(g.Log, g.Project, mux)
}

// resolve authenticates the caller and finds its app. It writes the error response itself.
func (g *Gateway) resolve(w http.ResponseWriter, r *http.Request) *AppConfig {
	log := loggerFrom(r.Context(), g.Log)
	subject, err := g.Auth.Authenticate(r.Context(), r)
	if err != nil {
		log.Warn("auth failed", "error", err.Error(), "remote", r.RemoteAddr)
		openAIError(w, http.StatusUnauthorized, "invalid_request_error", "unauthenticated",
			"send a Google ID token for the app's service account (audience = gateway URL) or a gateway token")
		return nil
	}
	cfg, err := g.Registry.Lookup(r.Context(), Principal{Subject: subject, AppHeader: r.Header.Get("X-ShipCube-App")})
	switch {
	case errors.Is(err, ErrUnknownApp):
		openAIError(w, http.StatusForbidden, "invalid_request_error", "unknown_app",
			"no ShipCube app is registered for this identity (set X-ShipCube-App if apps share a service account)")
		return nil
	case err != nil:
		log.Error("registry lookup failed", "error", err.Error())
		openAIError(w, http.StatusServiceUnavailable, "api_error", "registry_unavailable", "try again shortly")
		return nil
	}
	return cfg
}

func (g *Gateway) models(w http.ResponseWriter, r *http.Request) {
	cfg := g.resolve(w, r)
	if cfg == nil {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"object": "list", "data": []any{
		map[string]any{"id": cfg.Provider.Model, "object": "model", "owned_by": cfg.Provider.Kind},
	}})
}

func (g *Gateway) chat(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	if g.RequestTimeout > 0 {
		ctx, cancel := context.WithTimeout(r.Context(), g.RequestTimeout)
		defer cancel()
		r = r.WithContext(ctx)
	}
	log := loggerFrom(r.Context(), g.Log)
	cfg := g.resolve(w, r)
	if cfg == nil {
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxRequestBody))
	if err != nil {
		openAIError(w, http.StatusRequestEntityTooLarge, "invalid_request_error", "body_too_large", "request body is too large")
		return
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		openAIError(w, http.StatusBadRequest, "invalid_request_error", "invalid_json", "request body must be JSON")
		return
	}
	if msgs, _ := body["messages"].([]any); len(msgs) == 0 {
		openAIError(w, http.StatusBadRequest, "invalid_request_error", "missing_messages", "messages is required")
		return
	}

	pc := cfg.Provider
	kind := pc.Kind
	if kind == "" {
		kind = ProviderMeta
	}
	model, _ := body["model"].(string)
	if model == "" || model == "default" || model == "shipcube-default" {
		model = pc.Model
	}
	stream, _ := body["stream"].(bool)
	c := &call{cfg: cfg, model: model, body: body, stream: stream}
	platform := kind == ProviderMeta
	if platform {
		// The platform key pays: only allowlisted models, and only priced ones (spend caps must bind).
		if model != pc.Model && !g.MetaModels[model] {
			openAIError(w, http.StatusForbidden, "invalid_request_error", "model_not_allowed",
				fmt.Sprintf("model %q isn't available on ShipCube's key; use %q or \"default\"", model, pc.Model))
			return
		}
		if _, priced := cost(g.Prices, kind, model, 0, 0); !priced && !g.DevMode {
			log.Error("platform model has no price; refusing", "model", model)
			openAIError(w, http.StatusServiceUnavailable, "api_error", "model_not_priced",
				"this model has no price configured on the gateway yet")
			return
		}
	}
	if n, ok := body["n"].(float64); ok && n != 1 {
		openAIError(w, http.StatusBadRequest, "invalid_request_error", "n_not_supported", "only n=1 is supported")
		return
	}

	var reservation *Reservation
	ev := UsageEvent{RequestID: requestID(r.Context()), AppID: cfg.AppID, WorkspaceID: cfg.WorkspaceID, App: cfg.App, Provider: kind, Model: model,
		Stream: stream, OccurredAt: start.UTC()}
	defer func() {
		ev.Provider, ev.InputTokens, ev.OutputTokens, ev.Status = kind, c.inTok, c.outTok, c.status
		ev.Estimated = c.estimated
		ev.CostUSD, ev.Priced = cost(g.Prices, kind, model, c.inTok, c.outTok)
		ev.LatencyMS = time.Since(start).Milliseconds()
		if reservation != nil && reservation.remote != nil {
			g.Shared.Settle(reservation, c.inTok+c.outTok, ev.CostUSD)
		} else {
			g.Limiter.Settle(reservation, c.inTok+c.outTok, ev.CostUSD)
		}
		g.Usage.Record(ev)
	}()

	// Reserve the most this request can use before calling the provider: a prompt estimate (about
	// 3 bytes per token, generous) plus its output allowance. Settle replaces it with actual usage.
	wantOut := requestedOutput(body, g.maxOutput())
	var rate Rate
	if p, ok := g.Prices[kind+"/"+model]; ok {
		rate = &p
	}
	inEstimate := int64(len(raw))/3 + 1
	var grantedOut int64
	var res *Reservation
	var capErr *CapError
	shared := false
	if g.Shared != nil {
		var err error
		grantedOut, res, capErr, err = g.Shared.Admit(r.Context(), cfg, platform, inEstimate, wantOut, rate)
		switch {
		case err == nil:
			shared = true
		case platform:
			// Never fall back to per-instance counters on ShipCube's own key.
			log.Error("shared caps unreachable; refusing a platform-key request", "error", err.Error())
			c.status = http.StatusServiceUnavailable
			openAIError(w, c.status, "api_error", "caps_unavailable", "usage limits can't be checked right now; try again shortly")
			return
		default:
			log.Warn("shared caps unreachable; this instance's counters apply to this BYOK request", "error", err.Error())
		}
	}
	if !shared {
		grantedOut, res, capErr = g.Limiter.Admit(cfg, platform, inEstimate, wantOut, rate)
	}
	reservation = res
	if capErr != nil {
		c.status, ev.CapHit = http.StatusTooManyRequests, capErr.Cap
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(time.Until(capErr.ResetsAt).Seconds()))))
		writeJSON(w, http.StatusTooManyRequests, map[string]any{"error": map[string]any{
			"message": capErr.Error(), "type": "rate_limit_exceeded", "code": "cap_exceeded",
			"cap": capErr.Cap, "limit": capErr.Limit, "resets_at": capErr.ResetsAt.UTC().Format(time.RFC3339),
		}})
		return
	}
	maxTokens := clampOutput(body, kind, grantedOut)

	// Pick the upstream and its key.
	ref := pc.SecretRef
	switch kind {
	case ProviderMeta:
		c.baseURL, ref = g.MetaBaseURL, g.MetaSecretRef
		if ref == "" && g.DevMode {
			kind = ProviderMock
		}
	case ProviderOpenAI:
		c.baseURL = g.OpenAIBaseURL
	case ProviderAnthropic:
		c.baseURL = g.AnthropicBaseURL
	case ProviderOpenAICompatible:
		u, err := validateBaseURL(pc.BaseURL, g.AllowPrivate)
		if err != nil {
			c.status = http.StatusBadRequest
			openAIError(w, c.status, "invalid_request_error", "invalid_base_url", err.Error())
			return
		}
		c.baseURL = u.String()
	case ProviderMock:
		if !g.DevMode {
			c.status = http.StatusInternalServerError
			openAIError(w, c.status, "api_error", "unknown_provider", "the app's provider is not supported")
			return
		}
	default:
		c.status = http.StatusInternalServerError
		openAIError(w, c.status, "api_error", "unknown_provider", "the app's provider is not supported")
		return
	}
	if kind != ProviderMock {
		if ref == "" {
			c.status = http.StatusFailedDependency
			openAIError(w, c.status, "invalid_request_error", "no_key", "no API key is configured for this provider")
			return
		}
		byok := kind != ProviderMeta
		if byok {
			if err := checkKeyRef(ref, cfg.WorkspaceID, g.DevMode); err != nil {
				log.Error("refused a provider key reference", "app", cfg.App, "error", err.Error())
				c.status = http.StatusForbidden
				openAIError(w, c.status, "permission_error", "key_not_bound", err.Error())
				return
			}
		}
		c.key, err = g.Secrets.Get(r.Context(), ref)
		if err != nil {
			log.Error("secret lookup failed", "app", cfg.App, "error", err.Error())
			c.status = http.StatusServiceUnavailable
			openAIError(w, c.status, "api_error", "secret_unavailable", "the provider key could not be read")
			return
		}
		if byok {
			if c.key, err = boundKey(c.key, ref, kind, c.baseURL, g.DevMode); err != nil {
				log.Error("refused a provider key", "app", cfg.App, "error", err.Error())
				c.status = http.StatusForbidden
				openAIError(w, c.status, "permission_error", "key_not_bound", err.Error())
				return
			}
		}
	}

	w.Header().Set("X-ShipCube-Provider", kind)
	w.Header().Set("X-ShipCube-Model", model)
	switch kind {
	case ProviderMock:
		g.serveMock(w, c)
	case ProviderAnthropic:
		g.forwardAnthropic(r.Context(), w, c, maxTokens)
	default:
		g.forwardOpenAI(r.Context(), w, c)
	}
	level := slog.LevelInfo
	if c.status >= 500 {
		level = slog.LevelError
	} else if c.status >= 400 {
		level = slog.LevelWarn
	}
	log.Log(r.Context(), level, "chat", "app", cfg.App, "app_id", cfg.AppID, "provider", kind, "model", model,
		"status", c.status, "stream", stream, "attempts", c.attempts, "input_tokens", c.inTok,
		"output_tokens", c.outTok, "upstream_request_id", c.upstream, "ms", time.Since(start).Milliseconds())
}

func (g *Gateway) maxOutput() int64 {
	if g.MaxOutputTokens > 0 {
		return g.MaxOutputTokens
	}
	return defaultMaxOutputTokens
}

// requestedOutput is the output tokens a request asks for, through either field (the larger if
// both are set), bounded by limit; limit when it names none.
func requestedOutput(body map[string]any, limit int64) int64 {
	want := int64(-1)
	for _, f := range []string{"max_tokens", "max_completion_tokens"} {
		if v, ok := body[f].(float64); ok && v > 0 {
			want = max(want, int64(v))
		}
	}
	if want < 0 || want > limit {
		return limit
	}
	return want
}

// clampOutput writes the granted output tokens into the request (both fields when the app set
// both, else the provider's own) and returns the value for Anthropic, which takes it separately.
func clampOutput(body map[string]any, kind string, granted int64) int64 {
	if kind == ProviderAnthropic {
		return granted
	}
	set := false
	for _, f := range []string{"max_tokens", "max_completion_tokens"} {
		if _, ok := body[f]; ok {
			body[f], set = granted, true
		}
	}
	if !set {
		field := "max_tokens"
		if kind == ProviderOpenAI {
			field = "max_completion_tokens"
		}
		body[field] = granted
	}
	return granted
}
