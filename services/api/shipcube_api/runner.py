"""Background operations runner.

For the demo, operations run in-process: one asyncio loop on a daemon thread, each operation a
task, each blocking cloud call in a worker thread. It works the same under uvicorn, the in-process
MCP transport and the test client. The trade-off: operations die with the process, so the
service runs with min-instances=1 and one instance. The Cloud Tasks step machine in
docs/research/networking.md §3.3 replaces this once operations live in Postgres.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)


class Runner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="shipcube-runner", daemon=True).start()
            return self._loop

    def submit(self, coro: Coroutine[Any, Any, Any]) -> None:
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        fut.add_done_callback(lambda f: f.exception() and log.error("operation crashed", exc_info=f.exception()))

    def every(self, seconds: float, fn: Callable[[], None]) -> None:
        async def tick() -> None:
            while True:
                await asyncio.sleep(seconds)
                try:
                    await asyncio.to_thread(fn)
                except Exception:
                    log.exception("periodic task failed")

        self.submit(tick())


runner = Runner()
