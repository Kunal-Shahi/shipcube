"""In-memory deployer: sleeps for roughly as long as the real steps take (scaled down), no cloud."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .base import AppRef, Deployed, ShouldStop


class MockDeployer:
    name = "mock"

    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self.access: dict[str, list[str]] = {}
        self.deleted: set[str] = set()

    def _sleep(self, s: float, should_stop: ShouldStop | None = None) -> None:
        end = time.monotonic() + s * self.speed
        while time.monotonic() < end:
            if should_stop and should_stop():
                return
            time.sleep(0.05)

    def create_upload(self, workspace_id: str, content_type: str) -> dict[str, Any]:
        upload_id = f"upl_{secrets.token_hex(4)}"
        return {
            "upload_id": upload_id, "method": "PUT",
            "url": f"https://storage.googleapis.com/shipcube-uploads/{upload_id}.tgz?X-Goog-Signature=mock",
            "headers": {"Content-Type": content_type}, "max_bytes": 100 * 1024 * 1024,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    def stage_source(self, app: AppRef, source: dict[str, Any], op_id: str) -> dict[str, Any]:
        self._sleep(1)
        return {"mock": source.get("kind")}

    def build(self, app: AppRef, staged: dict[str, Any], op_id: str, should_stop: ShouldStop,
              on_build: Callable[[str], None], existing_build_id: str | None = None) -> str:
        if not existing_build_id:
            on_build(f"mock-build-{op_id}")
        self._sleep(4, should_stop)
        return f"{app.region}-docker.pkg.dev/{app.target.project}/shipcube/{app.name}:{op_id}"

    def deploy(self, app: AppRef, image: str, caps: dict[str, Any], env: dict[str, str]) -> Deployed:
        self._sleep(3)
        self.deleted.discard(app.service)
        return Deployed(url=f"https://{app.service}-a1b2c3d4e5-el.a.run.app", revision=f"{app.service}-{secrets.token_hex(2)}",
                        image=image)

    def route_traffic(self, app: AppRef, revision: str) -> None:
        self._sleep(1)

    def download_upload(self, workspace_id: str, upload_id: str, dest: Any) -> Any:
        raise NotImplementedError("the mock deployer stores no uploads")

    def set_access(self, app: AppRef, principals: list[str]) -> None:
        self.access[app.service] = list(principals)

    def update_caps(self, app: AppRef, caps: dict[str, Any]) -> None:
        pass

    def status(self, app: AppRef) -> dict[str, Any]:
        return {"ready": app.service not in self.deleted, "iap_enabled": True, "backend": "mock"}

    def soft_delete(self, app: AppRef) -> None:
        self._sleep(1)
        self.deleted.add(app.service)
        self.access.pop(app.service, None)

    def restore(self, app: AppRef, principals: list[str]) -> None:
        self.deleted.discard(app.service)
        self.access[app.service] = list(principals)

    def purge(self, app: AppRef) -> None:
        self.access.pop(app.service, None)
