package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const (
	anthropicVersion = "2026-10-21"
	// Default max_tokens for Anthropic, which requires the field (OpenAI-style requests may omit it).
	anthropicDefaultMaxTokens = 16000
	maxUpstreamBody           = 16 << 20
)

// call is one chat completion on its way upstream.
type call struct {
	cfg      *AppConfig
	key      string
	baseURL  string
	model    string
	body     map[string]any
	stream   bool
	inTok    int64
	outTok   int64
	status   int
	upstream string // provider request id, when the provider returns one
	attempts int
	// estimated is set when the provider sent no usage, so tokens were estimated from bytes
	// (about 4 characters per token). Caps still apply to unmetered streams.
	estimated bool
}

func (c *call) estimate(requestBytes, contentChars int) {
	c.inTok = int64(requestBytes/4 + 1)
	c.outTok = int64(contentChars/4 + 1)
	c.estimated = true
}

// redact removes the key from anything that came back from the provider before it reaches the app
// or the logs.
func (c *call) redact(b []byte) []byte {
	if c.key == "" {
		return b
	}
	return bytes.ReplaceAll(b, []byte(c.key), []byte("[REDACTED]"))
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func openAIError(w http.ResponseWriter, status int, typ, code, msg string) {
	writeJSON(w, status, map[string]any{"error": map[string]any{"message": msg, "type": typ, "code": code}})
}

func sseHeaders(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Content-Type", "text/event-stream")
	h.Set("Cache-Control", "no-cache")
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
}

// send POSTs to the provider with retries (RetryPolicy) under the per-attempt header timeout set on
// the client. Nothing has been written to the app yet, so retrying is safe for the app; a retried
// 5xx may still have been billed by the provider, as with the providers' own SDKs.
func (g *Gateway) send(ctx context.Context, c *call, url string, payload []byte, headers map[string]string) (*http.Response, error) {
	resp, attempts, err := g.Retry.Do(ctx, g.Upstream, func() (*http.Request, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/json")
		for k, v := range headers {
			req.Header.Set(k, v)
		}
		return req, nil
	})
	c.attempts = attempts
	return resp, err
}

func (g *Gateway) upstreamFailed(w http.ResponseWriter, c *call, err error) {
	c.status = http.StatusBadGateway
	code := "upstream_unreachable"
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		c.status, code = http.StatusGatewayTimeout, "upstream_timeout"
	case errors.Is(err, errBlockedAddress):
		c.status, code = http.StatusBadRequest, "blocked_base_url"
	}
	openAIError(w, c.status, "upstream_error", code, string(c.redact([]byte(err.Error()))))
}

// --- OpenAI-compatible: Meta (Muse), OpenAI, BYOK base URLs ------------------------------

func (g *Gateway) forwardOpenAI(ctx context.Context, w http.ResponseWriter, c *call) {
	c.body["model"] = c.model
	if c.stream {
		// Ask for a final usage chunk so streamed calls are metered too.
		so, _ := c.body["stream_options"].(map[string]any)
		if so == nil {
			so = map[string]any{}
		}
		so["include_usage"] = true
		c.body["stream_options"] = so
	}
	payload, _ := json.Marshal(c.body)
	resp, err := g.send(ctx, c, strings.TrimRight(c.baseURL, "/")+"/chat/completions", payload, map[string]string{
		"Authorization": "Bearer " + c.key,
	})
	if err != nil {
		g.upstreamFailed(w, c, err)
		return
	}
	defer resp.Body.Close()
	c.status = resp.StatusCode
	c.upstream = resp.Header.Get("X-Request-Id")

	if resp.StatusCode != http.StatusOK || !c.stream {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody))
		var parsed struct {
			Usage *struct {
				PromptTokens     int64 `json:"prompt_tokens"`
				CompletionTokens int64 `json:"completion_tokens"`
			} `json:"usage"`
		}
		if json.Unmarshal(b, &parsed) == nil && parsed.Usage != nil {
			c.inTok, c.outTok = parsed.Usage.PromptTokens, parsed.Usage.CompletionTokens
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		_, _ = w.Write(c.redact(b))
		return
	}

	sseHeaders(w)
	flusher, _ := w.(http.Flusher)
	r := bufio.NewReaderSize(resp.Body, 64<<10)
	metered, contentChars := false, 0
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			if data, ok := bytes.CutPrefix(bytes.TrimSpace(line), []byte("data:")); ok {
				data = bytes.TrimSpace(data)
				if !bytes.Equal(data, []byte("[DONE]")) {
					var chunk struct {
						Choices []struct {
							Delta struct {
								Content string `json:"content"`
							} `json:"delta"`
						} `json:"choices"`
						Usage *struct {
							PromptTokens     int64 `json:"prompt_tokens"`
							CompletionTokens int64 `json:"completion_tokens"`
						} `json:"usage"`
					}
					if json.Unmarshal(data, &chunk) == nil {
						for _, ch := range chunk.Choices {
							contentChars += len(ch.Delta.Content)
						}
						if chunk.Usage != nil {
							c.inTok, c.outTok, metered = chunk.Usage.PromptTokens, chunk.Usage.CompletionTokens, true
						}
					}
				}
			}
			_, _ = w.Write(c.redact(line))
			if flusher != nil && len(bytes.TrimSpace(line)) == 0 {
				flusher.Flush()
			}
		}
		if err != nil {
			break
		}
	}
	if flusher != nil {
		flusher.Flush()
	}
	if !metered {
		c.estimate(len(payload), contentChars)
	}
}

// --- Anthropic: OpenAI chat completions <-> Messages API ------------------------------------

type anthropicMessage struct {
	Role    string `json:"role"`
	Content []any  `json:"content"`
}

// toAnthropic translates an OpenAI chat-completions body. Text and images are supported; tool
// calls are not (yet), and a request with tools is rejected rather than silently changed.
// temperature/top_p/top_k are dropped: API version 2026-10-21 rejects them on every model.
func toAnthropic(body map[string]any, model string, stream bool, maxTokens int64) (map[string]any, error) {
	if _, ok := body["tools"]; ok {
		return nil, fmt.Errorf("tool calls are not supported for the anthropic provider yet")
	}
	msgs, _ := body["messages"].([]any)
	var system []string
	var out []anthropicMessage
	for _, m := range msgs {
		mm, _ := m.(map[string]any)
		role, _ := mm["role"].(string)
		parts, err := contentParts(mm["content"])
		if err != nil {
			return nil, err
		}
		switch role {
		case "system", "developer":
			for _, p := range parts {
				if t, ok := p.(map[string]any)["text"].(string); ok {
					system = append(system, t)
				}
			}
		case "user", "assistant":
			if len(out) > 0 && out[len(out)-1].Role == role {
				out[len(out)-1].Content = append(out[len(out)-1].Content, parts...)
			} else {
				out = append(out, anthropicMessage{Role: role, Content: parts})
			}
		default:
			return nil, fmt.Errorf("message role %q is not supported for the anthropic provider", role)
		}
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("messages must include at least one user message")
	}
	req := map[string]any{"model": model, "messages": out, "max_tokens": maxTokens}
	if len(system) > 0 {
		req["system"] = strings.Join(system, "\n\n")
	}
	switch s := body["stop"].(type) {
	case string:
		req["stop_sequences"] = []string{s}
	case []any:
		req["stop_sequences"] = s
	}
	if stream {
		req["stream"] = true
	}
	return req, nil
}

func contentParts(content any) ([]any, error) {
	switch v := content.(type) {
	case string:
		return []any{map[string]any{"type": "text", "text": v}}, nil
	case []any:
		var parts []any
		for _, p := range v {
			pm, _ := p.(map[string]any)
			switch pm["type"] {
			case "text":
				parts = append(parts, map[string]any{"type": "text", "text": pm["text"]})
			case "image_url":
				iu, _ := pm["image_url"].(map[string]any)
				u, _ := iu["url"].(string)
				if rest, ok := strings.CutPrefix(u, "data:"); ok {
					mediaType, data, found := strings.Cut(rest, ";base64,")
					if !found {
						return nil, fmt.Errorf("image data URLs must be base64")
					}
					parts = append(parts, map[string]any{"type": "image",
						"source": map[string]any{"type": "base64", "media_type": mediaType, "data": data}})
				} else {
					parts = append(parts, map[string]any{"type": "image", "source": map[string]any{"type": "url", "url": u}})
				}
			default:
				return nil, fmt.Errorf("content part type %v is not supported for the anthropic provider", pm["type"])
			}
		}
		return parts, nil
	case nil:
		return nil, nil
	}
	return nil, fmt.Errorf("unsupported message content")
}

func finishReason(stop string) string {
	switch stop {
	case "end_turn", "stop_sequence", "pause_turn":
		return "stop"
	case "max_tokens", "model_context_window_exceeded":
		return "length"
	case "tool_use":
		return "tool_calls"
	case "refusal":
		return "content_filter"
	}
	return "stop"
}

func (g *Gateway) forwardAnthropic(ctx context.Context, w http.ResponseWriter, c *call, maxTokens int64) {
	areq, err := toAnthropic(c.body, c.model, c.stream, maxTokens)
	if err != nil {
		c.status = http.StatusBadRequest
		openAIError(w, c.status, "invalid_request_error", "unsupported", err.Error())
		return
	}
	payload, _ := json.Marshal(areq)
	resp, err := g.send(ctx, c, strings.TrimRight(c.baseURL, "/")+"/v1/messages", payload, map[string]string{
		"x-api-key": c.key, "anthropic-version": anthropicVersion,
	})
	if err != nil {
		g.upstreamFailed(w, c, err)
		return
	}
	defer resp.Body.Close()
	c.status = resp.StatusCode
	c.upstream = resp.Header.Get("request-id")

	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, maxUpstreamBody))
		var e struct {
			Error struct {
				Type    string `json:"type"`
				Message string `json:"message"`
			} `json:"error"`
		}
		_ = json.Unmarshal(b, &e)
		if e.Error.Message == "" {
			e.Error.Message = string(b)
		}
		openAIError(w, resp.StatusCode, e.Error.Type, "upstream_error", string(c.redact([]byte(e.Error.Message))))
		return
	}

	created := time.Now().Unix()
	if !c.stream {
		var m struct {
			ID         string `json:"id"`
			Model      string `json:"model"`
			StopReason string `json:"stop_reason"`
			Content    []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"content"`
			Usage struct {
				InputTokens  int64 `json:"input_tokens"`
				OutputTokens int64 `json:"output_tokens"`
			} `json:"usage"`
		}
		if err := json.NewDecoder(io.LimitReader(resp.Body, maxUpstreamBody)).Decode(&m); err != nil {
			c.status = http.StatusBadGateway
			openAIError(w, c.status, "upstream_error", "bad_response", "could not parse the provider response")
			return
		}
		var text strings.Builder
		for _, b := range m.Content {
			if b.Type == "text" { // thinking blocks are not part of the OpenAI shape
				text.WriteString(b.Text)
			}
		}
		c.inTok, c.outTok = m.Usage.InputTokens, m.Usage.OutputTokens
		writeJSON(w, http.StatusOK, map[string]any{
			"id": m.ID, "object": "chat.completion", "created": created, "model": m.Model,
			"choices": []any{map[string]any{"index": 0, "finish_reason": finishReason(m.StopReason),
				"message": map[string]any{"role": "assistant", "content": text.String()}}},
			"usage": map[string]any{"prompt_tokens": c.inTok, "completion_tokens": c.outTok,
				"total_tokens": c.inTok + c.outTok},
		})
		return
	}

	sseHeaders(w)
	flusher, _ := w.(http.Flusher)
	id, model, stop := "", c.model, ""
	emit := func(v any) {
		b, _ := json.Marshal(v)
		_, _ = fmt.Fprintf(w, "data: %s\n\n", b)
		if flusher != nil {
			flusher.Flush()
		}
	}
	chunk := func(delta map[string]any, finish any) map[string]any {
		return map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": model,
			"choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": finish}}}
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 64<<10), 4<<20)
	for sc.Scan() {
		data, ok := strings.CutPrefix(sc.Text(), "data:")
		if !ok {
			continue
		}
		var ev struct {
			Type    string `json:"type"`
			Message struct {
				ID    string `json:"id"`
				Model string `json:"model"`
				Usage struct {
					InputTokens int64 `json:"input_tokens"`
				} `json:"usage"`
			} `json:"message"`
			Delta struct {
				Type       string `json:"type"`
				Text       string `json:"text"`
				StopReason string `json:"stop_reason"`
			} `json:"delta"`
			Usage struct {
				OutputTokens int64 `json:"output_tokens"`
			} `json:"usage"`
			Error struct {
				Type    string `json:"type"`
				Message string `json:"message"`
			} `json:"error"`
		}
		if json.Unmarshal([]byte(strings.TrimSpace(data)), &ev) != nil {
			continue
		}
		switch ev.Type {
		case "message_start":
			id, c.inTok = ev.Message.ID, ev.Message.Usage.InputTokens
			if ev.Message.Model != "" {
				model = ev.Message.Model
			}
			emit(chunk(map[string]any{"role": "assistant", "content": ""}, nil))
		case "content_block_delta":
			if ev.Delta.Type == "text_delta" {
				emit(chunk(map[string]any{"content": ev.Delta.Text}, nil))
			}
		case "message_delta":
			stop = ev.Delta.StopReason
			c.outTok = ev.Usage.OutputTokens
		case "message_stop":
			emit(chunk(map[string]any{}, finishReason(stop)))
			emit(map[string]any{"id": id, "object": "chat.completion.chunk", "created": created, "model": model,
				"choices": []any{}, "usage": map[string]any{"prompt_tokens": c.inTok, "completion_tokens": c.outTok,
					"total_tokens": c.inTok + c.outTok}})
		case "error":
			c.status = http.StatusBadGateway
			emit(map[string]any{"error": map[string]any{"type": ev.Error.Type,
				"message": string(c.redact([]byte(ev.Error.Message)))}})
		}
	}
	_, _ = io.WriteString(w, "data: [DONE]\n\n")
	if flusher != nil {
		flusher.Flush()
	}
}

// --- Mock: lets apps and the demo run before a Meta key exists ------------------------------

func (g *Gateway) serveMock(w http.ResponseWriter, c *call) {
	text := "This is a mock reply from the ShipCube AI gateway (no provider key is configured yet)."
	c.status, c.inTok, c.outTok = http.StatusOK, 12, 18
	id, created := fmt.Sprintf("mock-%d", time.Now().UnixNano()), time.Now().Unix()
	if !c.stream {
		writeJSON(w, http.StatusOK, map[string]any{
			"id": id, "object": "chat.completion", "created": created, "model": c.model,
			"choices": []any{map[string]any{"index": 0, "finish_reason": "stop",
				"message": map[string]any{"role": "assistant", "content": text}}},
			"usage": map[string]any{"prompt_tokens": c.inTok, "completion_tokens": c.outTok, "total_tokens": 30},
		})
		return
	}
	sseHeaders(w)
	flusher, _ := w.(http.Flusher)
	for i, word := range strings.SplitAfter(text, " ") {
		delta := map[string]any{"content": word}
		if i == 0 {
			delta["role"] = "assistant"
		}
		b, _ := json.Marshal(map[string]any{"id": id, "object": "chat.completion.chunk", "created": created,
			"model": c.model, "choices": []any{map[string]any{"index": 0, "delta": delta, "finish_reason": nil}}})
		_, _ = fmt.Fprintf(w, "data: %s\n\n", b)
		if flusher != nil {
			flusher.Flush()
		}
	}
	b, _ := json.Marshal(map[string]any{"id": id, "object": "chat.completion.chunk", "created": created,
		"model": c.model, "choices": []any{map[string]any{"index": 0, "delta": map[string]any{}, "finish_reason": "stop"}},
		"usage": map[string]any{"prompt_tokens": c.inTok, "completion_tokens": c.outTok, "total_tokens": 30}})
	_, _ = fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", b)
}
