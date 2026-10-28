"""Process-wide singletons: the deployer and the embedded worker. Production refuses mocks."""

from __future__ import annotations

import logging
import os
from functools import cache

from .deploy.base import Deployer
from .settings import settings

log = logging.getLogger(__name__)


@cache
def deployer() -> Deployer:
    if settings.backend == "gcp":
        from .deploy.gcp import GcpDeployer

        return GcpDeployer(settings)
    if settings.backend == "local":  # the local stack: apps as Docker containers (deploy/local)
        try:
            from .deploy.local import LocalDeployer  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError("SHIPCUBE_BACKEND=local needs shipcube_api.deploy.local (networking)") from e
        return LocalDeployer(settings)
    if settings.backend != "mock":
        raise RuntimeError(f"SHIPCUBE_BACKEND must be gcp, local or mock, not {settings.backend!r}")
    if not settings.dev_mode:
        raise RuntimeError("SHIPCUBE_BACKEND=mock is allowed only with SHIPCUBE_DEV_MODE=1")
    from .deploy.mock import MockDeployer

    log.warning("DEV_MODE: using the mock deployer; nothing is deployed")
    return MockDeployer(speed=float(os.environ.get("SHIPCUBE_MOCK_SPEED", "1")))


_worker = None


def start_embedded_worker() -> None:
    """Run a worker thread inside the API process (one instance with min-instances=1 is enough
    for the hackathon; more API instances or standalone workers can run side by side)."""
    global _worker
    default = "1" if settings.dev_mode else "0"  # production: a separate worker service as sc-worker
    if _worker is not None or os.environ.get("SHIPCUBE_EMBEDDED_WORKER", default) != "1":
        return
    from .worker import Worker

    _worker = Worker(deployer())
    _worker.start_thread()


def stop_embedded_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
