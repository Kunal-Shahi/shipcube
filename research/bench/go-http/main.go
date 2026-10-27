// Minimal HTTP service, and a reverse-proxy hop when UPSTREAM is set.
// Stdlib only, the same shape as a Cloud Run control-plane or gateway service.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	mux := http.NewServeMux()
	if up := os.Getenv("UPSTREAM"); up != "" {
		target, err := url.Parse(up)
		if err != nil {
			log.Fatal(err)
		}
		rp := httputil.NewSingleHostReverseProxy(target)
		rp.Transport = &http.Transport{MaxIdleConnsPerHost: 512}
		mux.Handle("/", rp)
	} else {
		mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte("ok"))
		})
		mux.HandleFunc("/echo", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]any{"ok": true, "path": r.URL.Path, "tier": "T1"})
		})
	}
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
