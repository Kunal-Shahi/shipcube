package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
)

type ctxKey int

const (
	ctxRequestID ctxKey = iota
	ctxTraceparent
	ctxLogger
)

var safeID = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// withRequestContext gives every request an id (the caller's X-Request-Id when it is well formed),
// echoes it in the response, and attaches a logger carrying it plus the Cloud Logging trace field, so
// gateway logs join the Cloud Run request log in the console.
func withRequestContext(base *slog.Logger, project string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-Id")
		if !safeID.MatchString(id) {
			id = newID()
		}
		w.Header().Set("X-Request-Id", id)
		log := base.With("request_id", id)
		tp := r.Header.Get("traceparent")
		if trace := traceID(r); trace != "" && project != "" {
			log = log.With("logging.googleapis.com/trace", "projects/"+project+"/traces/"+trace)
		}
		ctx := context.WithValue(r.Context(), ctxRequestID, id)
		ctx = context.WithValue(ctx, ctxTraceparent, tp)
		ctx = context.WithValue(ctx, ctxLogger, log)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// traceID reads the W3C traceparent, else Cloud Run's X-Cloud-Trace-Context.
func traceID(r *http.Request) string {
	if tp := r.Header.Get("traceparent"); tp != "" {
		if parts := strings.Split(tp, "-"); len(parts) == 4 && len(parts[1]) == 32 {
			return parts[1]
		}
	}
	if xc := r.Header.Get("X-Cloud-Trace-Context"); xc != "" {
		t, _, _ := strings.Cut(xc, "/")
		if len(t) == 32 {
			return t
		}
	}
	return ""
}

func requestID(ctx context.Context) string {
	id, _ := ctx.Value(ctxRequestID).(string)
	return id
}

func loggerFrom(ctx context.Context, fallback *slog.Logger) *slog.Logger {
	if l, ok := ctx.Value(ctxLogger).(*slog.Logger); ok {
		return l
	}
	return fallback
}

// propagate copies the request id and trace context onto calls to ShipCube's own services
// (the API). They are not sent to third-party model providers.
func propagate(ctx context.Context, req *http.Request) {
	if id := requestID(ctx); id != "" {
		req.Header.Set("X-Request-Id", id)
	}
	if tp, _ := ctx.Value(ctxTraceparent).(string); tp != "" {
		req.Header.Set("traceparent", tp)
	}
}
