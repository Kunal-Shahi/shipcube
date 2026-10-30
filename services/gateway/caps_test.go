package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func fixedNow() time.Time { return time.Date(2026, 10, 30, 12, 0, 0, 0, time.UTC) }

func TestAdmissionReservesSoConcurrentRequestsCannotOvershoot(t *testing.T) {
	l := &Limiter{Now: fixedNow}
	cfg := &AppConfig{AppID: "a", WorkspaceID: "w", Caps: Caps{AITokensPerDay: 1000}}
	var grants []int64
	var held []*Reservation
	for i := 0; i < 4; i++ { // four requests in flight at once, none settled yet
		g, res, err := l.Admit(cfg, false, 10, 400, nil)
		if err != nil {
			if i != 3 || err.Cap != "ai_tokens_per_day" {
				t.Fatalf("request %d: %v", i, err)
			}
			break
		}
		grants, held = append(grants, g), append(held, res)
	}
	if len(grants) != 3 || grants[0] != 400 || grants[1] != 400 || grants[2] != 170 {
		t.Fatalf("grants %v: the third must shrink to what's left, the fourth must wait", grants)
	}
	// Each settles at its real usage; the unused reservation comes back.
	for _, r := range held {
		l.Settle(r, 60, 0)
	}
	if g, _, err := l.Admit(cfg, false, 10, 400, nil); err != nil || g != 400 {
		t.Fatalf("after settling: %d %v", g, err)
	}
}

func TestPlatformDefaultsAndWorkspaceCaps(t *testing.T) {
	l := &Limiter{Now: fixedNow, DefaultTokensPerDay: 300, WorkspaceTokensPerDay: 500, WorkspaceRequestsPerMin: 3}
	a := &AppConfig{AppID: "a", WorkspaceID: "w"}
	b := &AppConfig{AppID: "b", WorkspaceID: "w"}

	// An app with no caps of its own gets the platform default on the platform key...
	if g, _, err := l.Admit(a, true, 0, 1000, nil); err != nil || g != 300 {
		t.Fatalf("default token cap: %d %v", g, err)
	}
	// ...but not on its own key (BYOK).
	if g, _, err := l.Admit(&AppConfig{AppID: "c", WorkspaceID: "w"}, false, 0, 1000, nil); err != nil || g != 1000 {
		t.Fatalf("BYOK: %d %v", g, err)
	}
	// The workspace's apps share one daily budget: b gets only what a left of the workspace's 500.
	if g, _, err := l.Admit(b, true, 0, 1000, nil); err != nil || g != 200 {
		t.Fatalf("workspace token cap: %d %v", g, err)
	}
	if _, _, err := l.Admit(b, true, 0, 10, nil); err == nil || err.Cap != "workspace_ai_tokens_per_day" {
		t.Fatalf("got %v", err)
	}
}

func TestWorkspaceRequestRateSpansApps(t *testing.T) {
	l := &Limiter{Now: fixedNow, WorkspaceRequestsPerMin: 2}
	for i, app := range []string{"a", "b"} {
		if _, _, err := l.Admit(&AppConfig{AppID: app, WorkspaceID: "w"}, true, 0, 1, nil); err != nil {
			t.Fatalf("request %d: %v", i, err)
		}
	}
	if _, _, err := l.Admit(&AppConfig{AppID: "c", WorkspaceID: "w"}, true, 0, 1, nil); err == nil ||
		err.Cap != "workspace_requests_per_minute" {
		t.Fatalf("got %v", err)
	}
	if _, _, err := l.Admit(&AppConfig{AppID: "d", WorkspaceID: "other"}, true, 0, 1, nil); err != nil {
		t.Fatalf("another workspace: %v", err)
	}
}

func TestPlatformKeyModelPolicyAndOutputClamp(t *testing.T) {
	var got map[string]any
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = readJSON(t, r.Body)
		_, _ = io.WriteString(w, `{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}`)
	}))
	defer up.Close()
	h := newHarness(t, ProviderConfig{Kind: ProviderMeta, Model: "muse-spark-1.3"}, Caps{}, up.URL)
	h.g.MetaSecretRef = "env:TEST_PROVIDER_KEY"
	h.g.MaxOutputTokens = 2000

	// Only the app's model (or an allowlisted one) on the platform key.
	resp := h.post(t, `{"model":"muse-ultra","messages":[{"role":"user","content":"hi"}]}`)
	if resp.StatusCode != 403 || readJSON(t, resp.Body)["error"].(map[string]any)["code"] != "model_not_allowed" {
		t.Fatalf("status %d", resp.StatusCode)
	}
	h.g.MetaModels = map[string]bool{"muse-ultra": true}
	if resp := h.post(t, `{"model":"muse-ultra","messages":[{"role":"user","content":"hi"}]}`); resp.StatusCode != 200 {
		t.Fatalf("allowlisted model: status %d", resp.StatusCode)
	}

	// Both output fields are clamped, whatever the app asks for, and only n=1 is accepted.
	if resp := h.post(t, `{"max_tokens":999999,"max_completion_tokens":50000,"messages":[{"role":"user","content":"hi"}]}`); resp.StatusCode != 200 {
		t.Fatalf("status %d", resp.StatusCode)
	}
	if got["max_tokens"].(float64) != 2000 || got["max_completion_tokens"].(float64) != 2000 {
		t.Fatalf("not clamped: %v %v", got["max_tokens"], got["max_completion_tokens"])
	}
	if resp := h.post(t, chatBody); resp.StatusCode != 200 || got["max_tokens"].(float64) != 2000 {
		t.Fatalf("a request naming no limit gets the maximum: %v", got["max_tokens"])
	}
	if resp := h.post(t, `{"n":4,"messages":[{"role":"user","content":"hi"}]}`); resp.StatusCode != 400 {
		t.Fatalf("n=4: status %d", resp.StatusCode)
	}

	// Outside DEV_MODE an unpriced platform model is refused, so spend caps always bind.
	h.g.DevMode = false
	resp = h.post(t, chatBody)
	if resp.StatusCode != 503 || readJSON(t, resp.Body)["error"].(map[string]any)["code"] != "model_not_priced" {
		t.Fatalf("status %d", resp.StatusCode)
	}
}

func TestASettleAfterMidnightDoesNotRefundTheNewDay(t *testing.T) {
	now := time.Date(2026, 10, 30, 23, 59, 0, 0, time.UTC)
	l := &Limiter{Now: func() time.Time { return now }}
	cfg := &AppConfig{AppID: "a", WorkspaceID: "w", Caps: Caps{AITokensPerDay: 1000}}
	_, res, err := l.Admit(cfg, false, 10, 900, nil) // reserves 910 on Oct 30
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(2 * time.Minute) // Oct 31
	if _, early, err := l.Admit(cfg, false, 10, 300, nil); err != nil {
		t.Fatal(err)
	} else {
		l.Settle(early, 310, 0) // 310 used on Oct 31
	}
	l.Settle(res, 100, 0) // the Oct 30 request used 100: its 810 refund belongs to Oct 30, not Oct 31
	if g, _, err := l.Admit(cfg, false, 10, 1000, nil); err != nil || g != 1000-310-10 {
		t.Fatalf("Oct 31 should have 680 left: granted %d, %v", g, err)
	}

	// Usage beyond the reservation is still charged to the new day.
	l2 := &Limiter{Now: func() time.Time { return now.Add(-2 * time.Minute) }}
	_, r2, _ := l2.Admit(cfg, false, 10, 90, nil) // reserves 100
	l2.Now = func() time.Time { return now }
	l2.Settle(r2, 400, 0)
	if g, _, _ := l2.Admit(cfg, false, 0, 1000, nil); g != 700 {
		t.Fatalf("300 tokens over the reservation belong to the new day: granted %d", g)
	}
}

func TestSpendIsReservedSoConcurrentRequestsCannotOvershoot(t *testing.T) {
	l := &Limiter{Now: fixedNow}
	cfg := &AppConfig{AppID: "a", WorkspaceID: "w", Caps: Caps{SpendUSDPerMonth: 1}}
	rate := Rate(&[2]float64{1, 100}) // $1 / $100 per million tokens in / out
	// Each wants 4,000 output tokens: $0.40 each. Two fit, the third gets what's left, the fourth waits.
	var grants []int64
	for i := 0; i < 4; i++ {
		g, _, err := l.Admit(cfg, false, 0, 4000, rate)
		if err != nil {
			if i != 3 || err.Cap != "spend_usd_per_month" {
				t.Fatalf("request %d: %v", i, err)
			}
			break
		}
		grants = append(grants, g)
	}
	if len(grants) != 3 || grants[0] != 4000 || grants[1] != 4000 || grants[2] != 2000 {
		t.Fatalf("grants %v", grants)
	}
	// Settling at the real cost frees the unused part of the reservation.
	l2 := &Limiter{Now: fixedNow}
	_, res, _ := l2.Admit(cfg, false, 0, 9000, rate) // reserves $0.90
	l2.Settle(res, 1000, 0.10)
	if g, _, err := l2.Admit(cfg, false, 0, 9000, rate); err != nil || g != 9000 {
		t.Fatalf("after settling at $0.10: %d %v", g, err)
	}
}
