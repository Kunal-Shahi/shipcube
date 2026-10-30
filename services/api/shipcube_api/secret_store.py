"""Where BYOK keys live. The database keeps only a reference (secret_refs: resource name, pinned
version, last 4 characters, SHA-256 fingerprint); values are written here and never read back by
the API.

- GcpSecretStore: Secret Manager in the control project. sc-api may create secrets, and add, read
  and destroy versions of ws-* secrets only (infra/terraform/secrets.tf), so every id starts ws-.
- LocalSecretStore (DEV_MODE): a JSON file of {secret id: value}, the format security's Secret
  Manager emulator (infra/local/secretmanager.py) serves, so the local gateway can read the key.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
from typing import Protocol

from .settings import Settings

SM = "https://secretmanager.googleapis.com/v1"


class SecretStoreError(Exception):
    pass


class SecretStore(Protocol):
    def put(self, secret_id: str, value: str, labels: dict[str, str]) -> tuple[str, str]:
        """Store a new version; returns (secret resource name, version number)."""

    def destroy(self, resource_name: str, version: str) -> None: ...


class GcpSecretStore:
    def __init__(self, project: str) -> None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.project, self.sess = project, AuthorizedSession(creds)

    def _call(self, method: str, url: str, ok: tuple[int, ...] = (200,), **kw) -> dict:
        r = self.sess.request(method, url, timeout=30, **kw)
        if r.status_code not in ok:
            raise SecretStoreError(f"Secret Manager {method} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def put(self, secret_id: str, value: str, labels: dict[str, str]) -> tuple[str, str]:
        name = f"projects/{self.project}/secrets/{secret_id}"
        self._call("POST", f"{SM}/projects/{self.project}/secrets", ok=(200, 409), params={"secretId": secret_id},
                   json={"replication": {"automatic": {}}, "labels": labels})
        v = self._call("POST", f"{SM}/{name}:addVersion",
                       json={"payload": {"data": base64.b64encode(value.encode()).decode()}})
        return name, v["name"].rsplit("/", 1)[-1]

    def destroy(self, resource_name: str, version: str) -> None:
        self._call("POST", f"{SM}/{resource_name}/versions/{version}:destroy", ok=(200, 404), json={})


class LocalSecretStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _locked(self):
        with open(self.path.with_suffix(".lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield data
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    def put(self, secret_id: str, value: str, labels: dict[str, str]) -> tuple[str, str]:
        with self._locked() as data:
            data[secret_id] = value
        return f"projects/local/secrets/{secret_id}", "1"

    def destroy(self, resource_name: str, version: str) -> None:
        with self._locked() as data:
            data.pop(resource_name.rsplit("/", 1)[-1], None)


def make_secret_store(s: Settings) -> SecretStore:
    if s.secret_store == "gcp":
        return GcpSecretStore(s.control_project)
    if not s.dev_mode:
        raise RuntimeError("SHIPCUBE_SECRET_STORE=local is allowed only with SHIPCUBE_DEV_MODE=1")
    return LocalSecretStore(s.local_secrets_file)
