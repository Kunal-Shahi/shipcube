"""Run the durable operations worker as a Cloud Run service.

Cloud Run services must listen on $PORT, and the worker (`shipcube_api.worker`) is a poll loop
with no HTTP server. This wrapper:
- checks the schema like the worker's own main (and never migrates outside DEV_MODE),
- runs `Worker.run_forever()` in a thread,
- serves GET /healthz on $PORT: 200 while the loop thread is alive and has iterated in the last
  `SHIPCUBE_WORKER_STALL_S` seconds (default 1800, longer than the slowest step: a build), else 503, so the liveness probe restarts a
  stuck or dead worker,
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
from shipcube_api.worker import Worker

log = logging.getLogger("shipcube.worker_service")
STALL_S = float(os.environ.get("SHIPCUBE_WORKER_STALL_S", "1800"))


class HeartbeatWorker(Worker):
    """Records when the loop last made progress (one claim attempt, or a wait between polls)."""

    beat = time.monotonic()

    def run_once(self):  # type: ignore[override]
        HeartbeatWorker.beat = time.monotonic()
        return super().run_once()


def main() -> None:
    obs.setup_logging()
    db.check_schema(migrate_if_dev=settings.dev_mode)
    worker = HeartbeatWorker(deployer())
    loop = threading.Thread(target=worker.run_forever, name="shipcube-worker", daemon=True)

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/healthz":
                self.send_error(404)
                return
            age = time.monotonic() - HeartbeatWorker.beat
            ok = loop.is_alive() and age < STALL_S
            body = json.dumps({"ok": ok, "worker": worker.id, "last_loop_s": round(age, 1)}).encode()
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
        worker.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    loop.start()
    server.serve_forever()
    loop.join(timeout=8)
    log.info("worker stopped", extra={"clean": not loop.is_alive()})


if __name__ == "__main__":
    main()
