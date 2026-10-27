package main

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

// GatewayTokenPrefix marks per-app gateway tokens (the fallback when an app can't mint a Google ID
// token, for example when it runs outside GCP). They are stored as SHA-256 only.
const GatewayTokenPrefix = "scg_"

var errUnauthenticated = errors.New("missing or invalid credentials")

// Authenticator turns an Authorization header into a principal subject.
type Authenticator struct {
	Verifier *GoogleVerifier // nil disables ID-token auth
	Tokens   bool            // accept scg_ per-app tokens
}

func (a *Authenticator) Authenticate(ctx context.Context, r *http.Request) (string, error) {
	h := r.Header.Get("Authorization")
	tok, ok := strings.CutPrefix(h, "Bearer ")
	if !ok || tok == "" {
		return "", errUnauthenticated
	}
	if strings.HasPrefix(tok, GatewayTokenPrefix) {
		if !a.Tokens {
			return "", errUnauthenticated
		}
		sum := sha256.Sum256([]byte(tok))
		return "token:" + hex.EncodeToString(sum[:]), nil
	}
	if a.Verifier == nil {
		return "", errUnauthenticated
	}
	email, err := a.Verifier.Verify(ctx, tok)
	if err != nil {
		return "", fmt.Errorf("%w: %v", errUnauthenticated, err)
	}
	return email, nil
}

// GoogleVerifier checks Google-signed ID tokens (RS256) like the ones Cloud Run apps fetch from
// the metadata server: signature against Google's JWKS, issuer, audience, expiry and a verified
// email. Only the standard library is used.
type GoogleVerifier struct {
	Audience string
	JWKSURL  string // default https://www.googleapis.com/oauth2/v3/certs
	Client   *http.Client
	Now      func() time.Time

	mu      sync.Mutex
	keys    map[string]*rsa.PublicKey
	fetched time.Time
}

type idClaims struct {
	Iss           string `json:"iss"`
	Aud           string `json:"aud"`
	Exp           int64  `json:"exp"`
	Iat           int64  `json:"iat"`
	Email         string `json:"email"`
	EmailVerified bool   `json:"email_verified"`
}

func (v *GoogleVerifier) Verify(ctx context.Context, token string) (string, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return "", errors.New("not a JWT")
	}
	var hdr struct {
		Alg string `json:"alg"`
		Kid string `json:"kid"`
	}
	if err := decodeSegment(parts[0], &hdr); err != nil {
		return "", err
	}
	if hdr.Alg != "RS256" {
		return "", fmt.Errorf("unexpected alg %q", hdr.Alg)
	}
	key, err := v.key(ctx, hdr.Kid)
	if err != nil {
		return "", err
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig); err != nil {
		return "", errors.New("bad signature")
	}
	var c idClaims
	if err := decodeSegment(parts[1], &c); err != nil {
		return "", err
	}
	now := time.Now
	if v.Now != nil {
		now = v.Now
	}
	const skew = 60
	switch {
	case c.Iss != "https://accounts.google.com" && c.Iss != "accounts.google.com":
		return "", fmt.Errorf("unexpected issuer %q", c.Iss)
	case c.Aud != v.Audience:
		return "", fmt.Errorf("unexpected audience %q", c.Aud)
	case now().Unix() > c.Exp+skew:
		return "", errors.New("token expired")
	case c.Iat > now().Unix()+skew:
		return "", errors.New("token issued in the future")
	case c.Email == "" || !c.EmailVerified:
		return "", errors.New("token has no verified email")
	}
	return c.Email, nil
}

func (v *GoogleVerifier) key(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if k, ok := v.keys[kid]; ok && time.Since(v.fetched) < time.Hour {
		return k, nil
	}
	// Unknown kid or stale set: refetch, at most once a minute.
	if time.Since(v.fetched) > time.Minute || v.keys == nil {
		if err := v.refresh(ctx); err != nil {
			return nil, err
		}
	}
	if k, ok := v.keys[kid]; ok {
		return k, nil
	}
	return nil, fmt.Errorf("unknown key id %q", kid)
}

func (v *GoogleVerifier) refresh(ctx context.Context) error {
	u := v.JWKSURL
	if u == "" {
		u = "https://www.googleapis.com/oauth2/v3/certs"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	c := v.Client
	if c == nil {
		c = http.DefaultClient
	}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("jwks returned %d", resp.StatusCode)
	}
	var set struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&set); err != nil {
		return err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range set.Keys {
		if k.Kty != "RSA" {
			continue
		}
		n, err1 := base64.RawURLEncoding.DecodeString(k.N)
		e, err2 := base64.RawURLEncoding.DecodeString(k.E)
		if err1 != nil || err2 != nil {
			continue
		}
		keys[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
	}
	v.keys, v.fetched = keys, time.Now()
	return nil
}

func decodeSegment(seg string, out any) error {
	b, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, out)
}
