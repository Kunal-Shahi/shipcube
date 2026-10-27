"""ShipCube language benchmarks: cold start, memory, throughput, proxy hop.

Stdlib only. Needs the Go binaries built, the uv venvs synced, node_modules
installed and `oha` on PATH. See README.md for the exact commands.

    python3 bench.py all        # cold start + throughput + proxy
    python3 bench.py coldstart
    python3 bench.py throughput
    python3 bench.py proxy
"""
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RUNS = int(os.environ.get("RUNS", "20"))
DURATION = os.environ.get("DURATION", "10s")
CONC = os.environ.get("CONC", "64")

MCP_BODY = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "estimate_load", "arguments": {"repo": "x"}}})
MCP_HEADERS = {"content-type": "application/json",
               "accept": "application/json, text/event-stream",
               "mcp-protocol-version": "2025-06-18"}

# name -> (cwd, argv, extra env, first-request kind)
TARGETS = {
    "go-http (net/http)": ("go-http", ["./bin/gohttp"], {}, "echo"),
    "py-http (FastAPI+uvicorn)": ("py-http", [".venv/bin/uvicorn", "app:app", "--log-level", "warning", "--port", "{port}"], {}, "echo"),
    "go-mcp (official go-sdk)": ("go-mcp", ["./bin/gomcp"], {}, "mcp"),
    "py-mcp (official python sdk)": ("py-mcp", [".venv/bin/python", "server.py"], {}, "mcp"),
    "ts-mcp (official ts sdk)": ("ts-mcp", ["node", "server.mjs"], {}, "mcp"),
}


def rss_mb(pid):
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout
    return int(out.strip() or 0) / 1024


def first_request(port, kind):
    if kind == "mcp":
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=MCP_BODY.encode(),
                                     headers=MCP_HEADERS, method="POST")
    else:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/echo")
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200, r.status
        r.read()


def start(name, port, env_extra=None):
    cwd, argv, env, _ = TARGETS[name]
    argv = [a.replace("{port}", str(port)) for a in argv]
    e = {**os.environ, "PORT": str(port), **env, **(env_extra or {})}
    return subprocess.Popen(argv, cwd=HERE / cwd, env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(port, deadline_s=30):
    """Poll /healthz every 1 ms; return when it answers 200."""
    url = f"http://127.0.0.1:{port}/healthz"
    end = time.perf_counter() + deadline_s
    while time.perf_counter() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.001)
    raise TimeoutError(url)


def stop(p):
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def coldstart():
    out = {}
    for i, name in enumerate(TARGETS):
        port = 18100 + i
        kind = TARGETS[name][3]
        ready, first, rss = [], [], []
        for _ in range(RUNS):
            t0 = time.perf_counter()
            p = start(name, port)
            try:
                wait_ready(port)
                t1 = time.perf_counter()
                first_request(port, kind)
                t2 = time.perf_counter()
                time.sleep(0.2)
                rss.append(rss_mb(p.pid))
            finally:
                stop(p)
            ready.append((t1 - t0) * 1000)
            first.append((t2 - t0) * 1000)
            time.sleep(0.2)
        out[name] = {
            "ready_ms_p50": statistics.median(ready), "ready_ms_p95": pct(ready, 0.95),
            "first_response_ms_p50": statistics.median(first), "first_response_ms_p95": pct(first, 0.95),
            "rss_idle_mb_p50": statistics.median(rss), "runs": RUNS,
        }
        print(name, json.dumps(out[name]), flush=True)
    return out


def oha(url, mcp=False):
    cmd = ["oha", "-z", DURATION, "-c", CONC, "--no-tui", "--output-format", "json", url]
    if mcp:
        cmd[1:1] = ["-m", "POST", "-d", MCP_BODY] + sum((["-H", f"{k}: {v}"] for k, v in MCP_HEADERS.items()), [])
    r = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    s, lat = r["summary"], r["latencyPercentiles"]
    codes = r.get("statusCodeDistribution", {})
    return {"rps": round(s["requestsPerSec"]), "p50_ms": round(lat["p50"] * 1000, 2),
            "p99_ms": round(lat["p99"] * 1000, 2), "success": s["successRate"], "status": codes}


def throughput():
    """Steady-state rps for each server alone, plus RSS after the run."""
    out = {}
    for i, name in enumerate(TARGETS):
        port = 18200 + i
        kind = TARGETS[name][3]
        variants = [("", {})]
        if name.startswith("go-"):
            variants.append((" GOMAXPROCS=1", {"GOMAXPROCS": "1"}))
        for suffix, env in variants:
            p = start(name, port, env)
            try:
                wait_ready(port)
                url = f"http://127.0.0.1:{port}/" + ("mcp" if kind == "mcp" else "echo")
                oha(url, kind == "mcp")  # warm-up
                res = oha(url, kind == "mcp")
                res["rss_after_load_mb"] = round(rss_mb(p.pid), 1)
            finally:
                stop(p)
            out[name + suffix] = res
            print(name + suffix, json.dumps(res), flush=True)
    return out


def proxy():
    """One proxy hop in front of the Go upstream: direct vs Go proxy vs Python proxy."""
    up_port = 18300
    upstream = start("go-http (net/http)", up_port)
    out = {}
    try:
        wait_ready(up_port)
        up = f"http://127.0.0.1:{up_port}"
        cases = [
            ("direct to upstream", None, {}),
            ("Go httputil.ReverseProxy", "go-http (net/http)", {}),
            ("Go httputil.ReverseProxy GOMAXPROCS=1", "go-http (net/http)", {"GOMAXPROCS": "1"}),
            ("Python FastAPI+httpx (1 worker)", "py-http (FastAPI+uvicorn)", {}),
        ]
        for label, name, env in cases:
            if name is None:
                oha(up + "/echo")
                res = oha(up + "/echo")
            else:
                port = up_port + 1 + len(out)
                p = start(name, port, {"UPSTREAM": up, **env})
                try:
                    # the proxy forwards /healthz upstream, so readiness means the hop works
                    wait_ready(port)
                    oha(f"http://127.0.0.1:{port}/echo")
                    res = oha(f"http://127.0.0.1:{port}/echo")
                    res["proxy_rss_after_load_mb"] = round(rss_mb(p.pid), 1)
                finally:
                    stop(p)
            out[label] = res
            print(label, json.dumps(res), flush=True)
    finally:
        stop(upstream)
    return out


def machine():
    def sh(*a):
        return subprocess.run(a, capture_output=True, text=True).stdout.strip()
    return {
        "os": platform.platform(), "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
        "ncpu": os.cpu_count(), "go": sh("go", "version"), "node": sh("node", "--version"),
        "python": sh(str(HERE / "py-http/.venv/bin/python"), "--version"),
        "oha": sh("oha", "--version"), "runs": RUNS, "duration": DURATION, "concurrency": CONC,
    }


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    RESULTS.mkdir(exist_ok=True)
    fns = {"coldstart": coldstart, "throughput": throughput, "proxy": proxy}
    for key in (fns if what == "all" else [what]):
        data = {"machine": machine(), "results": fns[key]()}
        (RESULTS / f"{key}.json").write_text(json.dumps(data, indent=2) + "\n")
