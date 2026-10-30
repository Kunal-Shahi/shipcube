package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// A BYOK key is stored by the API (workspace.key_payload) as a small JSON envelope that binds it to
// the provider and, for openai_compatible, the endpoint a person saved it with in the console:
//
//	{"shipcube_ai_key": 1, "provider": "openai_compatible", "base_url": "https://…/v1", "key": "…"}
//
// The gateway sends a key only where it was saved for (issue #42): never to a base URL the app's
// config names but the key wasn't saved with, and never a secret of another workspace or kind.

var errKeyNotBound = errors.New("the provider key is not bound to this provider or endpoint; save it again in the console")

// workspaceKeyRef matches the API's ai_key secrets for one workspace: projects/P/secrets/ws-<ws>-ai_key-<uuid>/versions/N.
func workspaceKeyRef(workspaceID string) *regexp.Regexp {
	return regexp.MustCompile(`^projects/[^/]+/secrets/ws-` + regexp.QuoteMeta(workspaceID) + `-ai_key-[A-Za-z0-9-]+/versions/[0-9]+$`)
}

// checkKeyRef refuses a secret reference that isn't one of this workspace's AI keys. DEV_MODE also
// accepts env:NAME references (local development and tests), whose values are plain keys.
func checkKeyRef(ref, workspaceID string, devMode bool) error {
	if devMode && strings.HasPrefix(ref, "env:") {
		return nil
	}
	if !workspaceKeyRef(workspaceID).MatchString(ref) {
		return fmt.Errorf("%w (not an AI key of this workspace)", errKeyNotBound)
	}
	return nil
}

type keyEnvelope struct {
	Version  int     `json:"shipcube_ai_key"`
	Provider string  `json:"provider"`
	BaseURL  *string `json:"base_url"`
	Key      string  `json:"key"`
}

func normURL(u string) string { return strings.TrimRight(u, "/") }

// boundKey returns the key to send for provider kind (and, for openai_compatible, baseURL), or
// errKeyNotBound. A plain value is accepted only from an env: reference in DEV_MODE.
func boundKey(raw, ref, kind, baseURL string, devMode bool) (string, error) {
	if devMode && strings.HasPrefix(ref, "env:") && !strings.HasPrefix(strings.TrimSpace(raw), "{") {
		return raw, nil
	}
	var env keyEnvelope
	if err := json.Unmarshal([]byte(raw), &env); err != nil || env.Version != 1 || env.Key == "" {
		return "", errKeyNotBound
	}
	if env.Provider != kind {
		return "", fmt.Errorf("%w (saved for %s)", errKeyNotBound, env.Provider)
	}
	if kind == ProviderOpenAICompatible && (env.BaseURL == nil || normURL(*env.BaseURL) != normURL(baseURL)) {
		return "", fmt.Errorf("%w (saved for another endpoint)", errKeyNotBound)
	}
	return env.Key, nil
}
