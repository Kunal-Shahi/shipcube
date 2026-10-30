"""Run the durable operations worker as a Cloud Run service.

Cloud Run services must listen on $PORT, and the worker (`shipcube_api.worker`) is a poll loop
with no HTTP server. This wrapper:
- checks the schema like the worker's own main (and never migrates outside DEV_MODE),
- runs one worker thread per lane (`shipcube_api.worker.run_lanes`, SHIPCUBE_WORKER_LANES; default one
  deploy thread and three estimate threads, so estimates never hold up deploys),
- serves GET /healthz (probes) and /health (external checks; Cloud Run reserves /healthz) on $PORT: 200 while every lane thread is alive and
  has iterated in the last `SHIPCUBE_WORKER_STALL_S` seconds (default 1800, longer than the slowest step: a build), else 503, so the
  liveness probe restarts a stuck or dead worker,
- on SIGTERM (scale-in, new revision) stops claiming work and waits up to 8 s for the current step.
  An interrupted step is resumed by the next worker when its 90 s lease expires.

Deployed by deploy/deploy.sh as `shipcube-worker`: min 1 instance, CPU always allocated.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shipcube_api import db, obs
from shipcube_api.settings import settings
from shipcube_api.store_runtime import deployer
from shipcube_api.worker import Worker, run_lanes

log = logging.getLogger("shipcube.worker_service")
STALL_S = float(os.environ.get("SHIPCUBE_WORKER_STALL_S", "1800"))


class HeartbeatWorker(Worker):
    """Records when its loop last made progress (one claim attempt, or a wait between polls)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.beat = time.monotonic()

    def run_once(self):  # type: ignore[override]
        self.beat = time.monotonic()
        return super().run_once()


def main() -> None:
    obs.setup_logging()
    db.check_schema(migrate_if_dev=settings.dev_mode)
    workers: list[HeartbeatWorker] = run_lanes(deployer(), factory=HeartbeatWorker)  # type: ignore[assignment]

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in ("/healthz", "/health"):  # /healthz is reserved on run.app URLs
                self.send_error(404)
                return
            now = time.monotonic()
            lanes = [{"worker": w.id, "alive": w.thread.is_alive(), "last_loop_s": round(now - w.beat, 1)} for w in workers]
            ok = all(lane["alive"] and lane["last_loop_s"] < STALL_S for lane in lanes)
            body = json.dumps({"ok": ok, "lanes": lanes}).encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # health checks would flood the logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)

    def stop(signum, _frame) -> None:
        log.info("stopping worker", extra={"signal": signum})
        for w in workers:
            w.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    deadline = time.monotonic() + 8
    for w in workers:
        w.thread.join(timeout=max(0.0, deadline - time.monotonic()))
    log.info("worker stopped", extra={"clean": not any(w.thread.is_alive() for w in workers)})


if __name__ == "__main__":
    main()
