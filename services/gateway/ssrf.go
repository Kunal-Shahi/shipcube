package main

import (
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"syscall"
	"time"
)

var errBlockedAddress = errors.New("upstream address is not allowed")

var cgnat = &net.IPNet{IP: net.IPv4(100, 64, 0, 0), Mask: net.CIDRMask(10, 32)}

// blockedIP reports addresses a BYOK base URL must never reach: private, loopback, link-local
// (which includes the metadata server 169.254.169.254), unspecified, multicast and CGNAT.
func blockedIP(ip net.IP) bool {
	return ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsUnspecified() || ip.IsMulticast() || ip.IsInterfaceLocalMulticast() || cgnat.Contains(ip) ||
		ip.Equal(net.IPv4bcast) || (ip.To4() != nil && ip.To4()[0] == 0)
}

// guardedClient checks the resolved IP at connect time, so a hostname that resolves to a private
// address (or rebinds to one after the URL was saved) is refused. Redirects are not followed.
func guardedClient(allowPrivate bool) *http.Client {
	d := &net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}
	if !allowPrivate {
		d.Control = func(_, address string, _ syscall.RawConn) error {
			host, _, err := net.SplitHostPort(address)
			if err != nil {
				return err
			}
			ip := net.ParseIP(host)
			if ip == nil || blockedIP(ip) {
				return fmt.Errorf("%w: %s", errBlockedAddress, host)
			}
			return nil
		}
	}
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.DialContext = d.DialContext
	t.Proxy = nil
	return &http.Client{
		Transport:     t,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

// validateBaseURL checks a BYOK base URL before use: https only, no credentials, no IP literal in a
// blocked range. The dialer repeats the IP check after DNS resolution.
func validateBaseURL(raw string, allowPrivate bool) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("invalid base_url")
	}
	if allowPrivate {
		return u, nil
	}
	if u.Scheme != "https" || u.Host == "" || u.User != nil {
		return nil, fmt.Errorf("base_url must be https://host[/path] without credentials")
	}
	if ip := net.ParseIP(u.Hostname()); ip != nil && blockedIP(ip) {
		return nil, errBlockedAddress
	}
	return u, nil
}
