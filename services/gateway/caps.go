package main

import (
	"encoding/json"
	"fmt"
	"sync"
	"time"
)

// Limiter enforces caps in memory, per app and, on the platform's own key, per workspace: a token
// bucket for requests per minute, and day/month counters for AI tokens and spend.
//
// Admission reserves the most a request can use (its input estimate plus the output tokens it's
// allowed) before the provider is called, and Settle replaces the reservation with what it actually
// used. Concurrent requests can't all pass a nearly spent cap at once (issue #20, G-3).
//
// Counters live in this instance's memory: with N instances a cap can be exceeded up to N times,
// and they restart from the registry's used_tokens_today when an instance starts. Shared counters
// (rate_counters) need a database path for the gateway; see DESIGN.md.
type Limiter struct {
	mu   sync.Mutex
	apps map[string]*usage
	ws   map[string]*usage
	Now  func() time.Time

	// Defaults on the platform key (Muse) for apps whose registry entry sets no cap. 0 = no default.
	DefaultTokensPerDay     int64
	DefaultRequestsPerMin   int
	WorkspaceTokensPerDay   int64
	WorkspaceRequestsPerMin int
}

type usage struct {
	bucket   float64
	refilled time.Time
	day      string
	tokens   int64
	month    string
	spentUSD float64
}

// CapError says which cap was hit and when it resets; it becomes a 429.
type CapError struct {
	Cap      string
	Limit    float64
	ResetsAt time.Time
}

func (e *CapError) Error() string {
	return fmt.Sprintf("%s cap of %g reached; resets at %s", e.Cap, e.Limit, e.ResetsAt.UTC().Format(time.RFC3339))
}

// Reservation is what Admit counted for one request, to be settled when it finishes. day and
// month say which counters it was added to, so a request that ends after midnight (or the month's
// end) never refunds into the new period's counters.
type Reservation struct {
	cfg      *AppConfig
	platform bool
	tokens   int64
	usd      float64
	day      string
	month    string
	remote   json.RawMessage // set by SharedLimiter: the API's reservation, sent back to settle
}

// Rate is a model's price in USD per million tokens, [input, output]; nil when it has none.
type Rate *[2]float64

// limits are the caps that apply to one request.
type limits struct {
	appTokens, wsTokens int64
	appRPM, wsRPM       int
	spendUSD            float64
}

func (l *Limiter) limitsFor(cfg *AppConfig, platform bool) limits {
	c := cfg.Caps
	out := limits{appTokens: c.AITokensPerDay, appRPM: c.RequestsPerMinute, spendUSD: c.SpendUSDPerMonth}
	if platform {
		if out.appTokens == 0 {
			out.appTokens = l.DefaultTokensPerDay
		}
		if out.appRPM == 0 {
			out.appRPM = l.DefaultRequestsPerMin
		}
		out.wsTokens, out.wsRPM = l.WorkspaceTokensPerDay, l.WorkspaceRequestsPerMin
	}
	return out
}

func (l *Limiter) now() time.Time {
	if l.Now != nil {
		return l.Now()
	}
	return time.Now()
}

func (l *Limiter) get(m *map[string]*usage, key string, rpm int, seedTokens int64, seedUSD float64, now time.Time) *usage {
	if *m == nil {
		*m = map[string]*usage{}
	}
	day, month := now.UTC().Format("2006-01-02"), now.UTC().Format("2006-01")
	u, ok := (*m)[key]
	if !ok {
		u = &usage{bucket: float64(rpm), refilled: now, day: day, month: month, tokens: seedTokens, spentUSD: seedUSD}
		(*m)[key] = u
	}
	if u.day != day {
		u.day, u.tokens = day, 0
	}
	if u.month != month {
		u.month, u.spentUSD = month, 0
	}
	return u
}

func tomorrow(t time.Time) time.Time {
	return time.Date(t.Year(), t.Month(), t.Day()+1, 0, 0, 0, 0, time.UTC)
}

// takeToken refills a requests-per-minute bucket and takes one request from it.
func takeToken(u *usage, rpm int, now time.Time, name string) *CapError {
	if rpm <= 0 {
		return nil
	}
	rate := float64(rpm) / 60
	u.bucket = min(float64(rpm), u.bucket+now.Sub(u.refilled).Seconds()*rate)
	u.refilled = now
	if u.bucket < 1 {
		wait := time.Duration((1 - u.bucket) / rate * float64(time.Second))
		return &CapError{name, float64(rpm), now.Add(wait)}
	}
	u.bucket--
	return nil
}

// Admit admits one request or returns the cap that blocks it. inTokens is an estimate of the
// prompt, wantOut the output tokens the request may use (already bounded by the gateway's maximum).
// It returns the output tokens granted: at most wantOut, and no more than today's remaining tokens
// or (when the model has a rate) this month's remaining spend leave room for. It reserves inTokens
// plus that grant, and their cost. platform is true on the platform key, where defaults and
// workspace caps apply.
func (l *Limiter) Admit(cfg *AppConfig, platform bool, inTokens, wantOut int64, rate Rate) (int64, *Reservation, *CapError) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	t := now.UTC()
	lim := l.limitsFor(cfg, platform)
	app := l.get(&l.apps, cfg.AppID, lim.appRPM, cfg.UsedTokensToday, cfg.SpentUSDMonth, now)
	var ws *usage
	if platform {
		ws = l.get(&l.ws, cfg.WorkspaceID, lim.wsRPM, 0, 0, now)
	}

	granted := wantOut
	for _, c := range []struct {
		u     *usage
		limit int64
		name  string
	}{{app, lim.appTokens, "ai_tokens_per_day"}, {ws, lim.wsTokens, "workspace_ai_tokens_per_day"}} {
		if c.u == nil || c.limit <= 0 {
			continue
		}
		room := c.limit - c.u.tokens - inTokens
		if room <= 0 {
			return 0, nil, &CapError{c.name, float64(c.limit), tomorrow(t)}
		}
		granted = min(granted, room)
	}
	usd := 0.0
	if lim.spendUSD > 0 {
		spendErr := &CapError{"spend_usd_per_month", lim.spendUSD, time.Date(t.Year(), t.Month()+1, 1, 0, 0, 0, 0, time.UTC)}
		room := lim.spendUSD - app.spentUSD
		if room <= 0 {
			return 0, nil, spendErr
		}
		if rate != nil {
			inUSD := float64(inTokens) * rate[0] / 1e6
			if inUSD >= room {
				return 0, nil, spendErr
			}
			if rate[1] > 0 {
				granted = min(granted, int64((room-inUSD)/(rate[1]/1e6)+1e-6)) // epsilon: float rounding
				if granted <= 0 {
					return 0, nil, spendErr
				}
			}
			usd = inUSD + float64(granted)*rate[1]/1e6
		}
	}
	// Check both buckets before taking from either, so a refusal costs nothing.
	if err := takeToken(app, lim.appRPM, now, "requests_per_minute"); err != nil {
		return 0, nil, err
	}
	if ws != nil {
		if err := takeToken(ws, lim.wsRPM, now, "workspace_requests_per_minute"); err != nil {
			app.bucket++ // give the app's request back
			return 0, nil, err
		}
	}
	res := &Reservation{cfg: cfg, platform: platform, tokens: inTokens + granted, usd: usd, day: app.day, month: app.month}
	app.tokens += res.tokens
	app.spentUSD += usd
	if ws != nil {
		ws.tokens += res.tokens
	}
	return granted, res, nil
}

// Settle replaces a reservation with the tokens and cost the request actually used.
func (l *Limiter) Settle(res *Reservation, tokens int64, costUSD float64) {
	if res == nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	lim := l.limitsFor(res.cfg, res.platform)
	app := l.get(&l.apps, res.cfg.AppID, lim.appRPM, res.cfg.UsedTokensToday, res.cfg.SpentUSDMonth, now)
	settle(app, res, tokens, costUSD)
	if res.platform {
		ws := l.get(&l.ws, res.cfg.WorkspaceID, lim.wsRPM, 0, 0, now)
		settle(ws, res, tokens, 0)
	}
}

// settle swaps the reservation for actual usage in the period it was reserved in. When that period
// has ended, its counters are gone: the new period is charged only what went beyond the reservation.
func settle(u *usage, res *Reservation, tokens int64, costUSD float64) {
	if u.day == res.day {
		u.tokens += tokens - res.tokens
	} else {
		u.tokens += max(0, tokens-res.tokens)
	}
	u.tokens = max(0, u.tokens)
	if costUSD > 0 || res.usd > 0 {
		if u.month == res.month {
			u.spentUSD += costUSD - res.usd
		} else {
			u.spentUSD += max(0, costUSD-res.usd)
		}
		u.spentUSD = max(0, u.spentUSD)
	}
}
