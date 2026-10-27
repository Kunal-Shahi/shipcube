package main

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"strconv"
	"time"
)

// RetryPolicy retries a request on transport errors and on retryable statuses, with exponential
// backoff and jitter. It is used only before any response byte reaches the app, so a retry can never
// duplicate output the app has seen.
type RetryPolicy struct {
	Retries  int           // extra attempts after the first
	Base     time.Duration // first backoff; doubles each attempt
	MaxDelay time.Duration // cap on one wait, including a provider's Retry-After
}

// retryableStatus: rate limits and transient server errors. 529 is Anthropic's "overloaded".
func retryableStatus(code int) bool {
	switch code {
	case http.StatusTooManyRequests, http.StatusInternalServerError, http.StatusBadGateway,
		http.StatusServiceUnavailable, http.StatusGatewayTimeout, 529:
		return true
	}
	return false
}

// Do sends the request built by newReq (called once per attempt, so bodies are fresh) and returns
// the final response and the number of attempts made. A Retry-After longer than MaxDelay is not
// waited for: that response is returned as is.
func (p RetryPolicy) Do(ctx context.Context, c *http.Client, newReq func() (*http.Request, error)) (*http.Response, int, error) {
	for attempt := 0; ; attempt++ {
		req, err := newReq()
		if err != nil {
			return nil, attempt + 1, err
		}
		resp, err := c.Do(req)
		last := attempt >= p.Retries
		var wait time.Duration
		switch {
		case err != nil:
			if last || ctx.Err() != nil || errors.Is(err, errBlockedAddress) {
				return nil, attempt + 1, err
			}
		case retryableStatus(resp.StatusCode) && !last:
			if ra := retryAfter(resp.Header.Get("Retry-After")); ra > 0 {
				if ra > p.MaxDelay {
					return resp, attempt + 1, nil
				}
				wait = ra
			}
			_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
			resp.Body.Close()
		default:
			return resp, attempt + 1, err
		}
		if wait == 0 {
			wait = p.Base << attempt
			wait = wait/2 + time.Duration(rand.Int64N(int64(wait)+1)) // jitter: 50–150%
		}
		wait = min(wait, p.MaxDelay)
		select {
		case <-ctx.Done():
			return nil, attempt + 1, ctx.Err()
		case <-time.After(wait):
		}
	}
}

func retryAfter(v string) time.Duration {
	if v == "" {
		return 0
	}
	if s, err := strconv.Atoi(v); err == nil && s >= 0 {
		return time.Duration(s) * time.Second
	}
	if t, err := http.ParseTime(v); err == nil {
		return time.Until(t)
	}
	return 0
}
