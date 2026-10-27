// Minimal MCP server on the official Go SDK, streamable HTTP transport, one tool.
package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type EstimateIn struct {
	Repo string `json:"repo" jsonschema:"repository URL or path"`
}

type EstimateOut struct {
	Class string `json:"class"`
	Tier  string `json:"tier"`
}

func estimate(ctx context.Context, req *mcp.CallToolRequest, in EstimateIn) (*mcp.CallToolResult, EstimateOut, error) {
	return nil, EstimateOut{Class: "bursty_interactive", Tier: "T1"}, nil
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	server := mcp.NewServer(&mcp.Implementation{Name: "shipcube-bench", Version: "0.0.1"}, nil)
	mcp.AddTool(server, &mcp.Tool{Name: "estimate_load", Description: "Estimate workload class"}, estimate)
	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return server }, &mcp.StreamableHTTPOptions{Stateless: true, JSONResponse: true})
	mux := http.NewServeMux()
	mux.Handle("/mcp", handler)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) })
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
