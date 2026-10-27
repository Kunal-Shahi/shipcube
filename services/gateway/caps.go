package main

import (
	"fmt"
	"sync"
	"time"
)

// Limiter enforces per-app caps in memory: a token bucket for requests per minute, and day/month
// counters for AI tokens and spend. With a few gateway instances this is approximate, which the
// design accepts; usage events are the durable record.
type Limiter struct {
	mu   sync.Mutex
	apps map[string]*appUsage
	Now  func() time.Time
}

type appUsage struct {
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

func (l *Limiter) now() time.Time {
	if l.Now != nil {
		return l.Now()
	}
	return time.Now()
}

func (l *Limiter) state(cfg *AppConfig, now time.Time) *appUsage {
	if l.apps == nil {
		l.apps = map[string]*appUsage{}
	}
	day, month := now.UTC().Format("2006-01-02"), now.UTC().Format("2006-01")
	u, ok := l.apps[cfg.AppID]
	if !ok {
		u = &appUsage{bucket: float64(cfg.Caps.RequestsPerMinute), refilled: now, day: day, month: month,
			tokens: cfg.UsedTokensToday, spentUSD: cfg.SpentUSDMonth}
		l.apps[cfg.AppID] = u
	}
	if u.day != day {
		u.day, u.tokens = day, 0
	}
	if u.month != month {
		u.month, u.spentUSD = month, 0
	}
	return u
}

// Allow admits one request or returns the cap that blocks it. It also returns how many AI tokens
// are left today (-1 when unlimited), so the request's max_tokens can be clamped.
func (l *Limiter) Allow(cfg *AppConfig) (int64, *CapError) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	u := l.state(cfg, now)
	c := cfg.Caps
	t := now.UTC()

	if c.AITokensPerDay > 0 && u.tokens >= c.AITokensPerDay {
		return 0, &CapError{"ai_tokens_per_day", float64(c.AITokensPerDay),
			time.Date(t.Year(), t.Month(), t.Day()+1, 0, 0, 0, 0, time.UTC)}
	}
	if c.SpendUSDPerMonth > 0 && u.spentUSD >= c.SpendUSDPerMonth {
		return 0, &CapError{"spend_usd_per_month", c.SpendUSDPerMonth,
			time.Date(t.Year(), t.Month()+1, 1, 0, 0, 0, 0, time.UTC)}
	}
	if c.RequestsPerMinute > 0 {
		rate := float64(c.RequestsPerMinute) / 60
		u.bucket = min(float64(c.RequestsPerMinute), u.bucket+now.Sub(u.refilled).Seconds()*rate)
		u.refilled = now
		if u.bucket < 1 {
			wait := time.Duration((1 - u.bucket) / rate * float64(time.Second))
			return 0, &CapError{"requests_per_minute", float64(c.RequestsPerMinute), now.Add(wait)}
		}
		u.bucket--
	}
	if c.AITokensPerDay > 0 {
		return c.AITokensPerDay - u.tokens, nil
	}
	return -1, nil
}

// Record adds a finished request's tokens and cost.
func (l *Limiter) Record(cfg *AppConfig, tokens int64, costUSD float64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	u := l.state(cfg, l.now())
	u.tokens += tokens
	u.spentUSD += costUSD
}
