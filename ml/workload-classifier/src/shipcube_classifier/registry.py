"""Versioned model storage: load the classifier from GCS (or the bundled copy) by checksum.

Layout in the model bucket (scripts/publish_model.py writes it; objects are immutable):
  gs://<bucket>/models/workload-classifier/<version>/model.json
  gs://<bucket>/models/workload-classifier/<version>/manifest.json   {version, sha256, ...}
  gs://<bucket>/models/workload-classifier/latest.json               {version, sha256}

Configuration (environment):
  SHIPCUBE_MODEL_URI     gs://<bucket>/models/workload-classifier/<version>  or .../latest
                         unset or "bundled": the copy shipped in this package
  SHIPCUBE_MODEL_SHA256  optional pin; loading fails unless model.json has this digest
  SHIPCUBE_MODEL_CACHE   local cache directory (default: $TMPDIR/shipcube-models)

A configured GCS model that can't be fetched or fails its checksum raises
ModelLoadError. There is no silent fall back to the bundled model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import obs
from .model import ARTEFACT, Model

log = logging.getLogger("shipcube.classifier")
BUNDLED_MANIFEST = ARTEFACT.parent / "manifest.json"
_lock = threading.Lock()
_loaded: dict[str, "LoadedModel"] = {}


class ModelLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedModel:
    model: Model
    version: str
    sha256: str
    source: str            # "bundled" or the gs:// URI


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _check(body: bytes, manifest: dict, pin: str | None, where: str) -> str:
    digest = _sha(body)
    if digest != manifest.get("sha256"):
        raise ModelLoadError(f"{where}: sha256 {digest[:12]} does not match manifest {str(manifest.get('sha256'))[:12]}")
    if pin and digest != pin:
        raise ModelLoadError(f"{where}: sha256 {digest[:12]} does not match SHIPCUBE_MODEL_SHA256 {pin[:12]}")
    return digest


def _parse_gs(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ModelLoadError(f"SHIPCUBE_MODEL_URI must start with gs:// (got {uri!r})")
    bucket, _, prefix = uri[5:].partition("/")
    if not bucket or not prefix:
        raise ModelLoadError("SHIPCUBE_MODEL_URI must be gs://<bucket>/<prefix>/<version|latest>")
    return bucket, prefix.rstrip("/")


def _gcs_get(client, bucket: str, name: str) -> bytes:
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}"
    return client.get(url, params={"alt": "media"}, raw=True)


def load_bundled(pin: str | None = None) -> LoadedModel:
    body = ARTEFACT.read_bytes()
    manifest = json.loads(BUNDLED_MANIFEST.read_text())
    digest = _check(body, manifest, pin, "bundled model")
    return LoadedModel(Model(json.loads(body)), manifest["version"], digest, "bundled")


def load_gcs(uri: str, pin: str | None = None, cache_dir: Path | None = None, client=None) -> LoadedModel:
    from .gcp import GcpClient, GcpError

    bucket, prefix = _parse_gs(uri)
    client = client or GcpClient(timeout_s=20)
    base, version = prefix.rsplit("/", 1) if "/" in prefix else ("", prefix)
    try:
        if version == "latest":
            latest = json.loads(_gcs_get(client, bucket, f"{base}/latest.json"))
            version = latest["version"]
        cache = (cache_dir or Path(os.environ.get("SHIPCUBE_MODEL_CACHE",
                                                  Path(tempfile.gettempdir()) / "shipcube-models"))) / version
        manifest = json.loads(_gcs_get(client, bucket, f"{base}/{version}/manifest.json"))
        if manifest.get("version") != version:
            raise ModelLoadError(f"manifest version {manifest.get('version')!r} != {version!r}")
        cached = cache / "model.json"
        body = cached.read_bytes() if cached.exists() else b""
        if not body or _sha(body) != manifest["sha256"]:
            body = _gcs_get(client, bucket, f"{base}/{version}/model.json")
            digest = _check(body, manifest, pin, f"{uri} ({version})")
            cache.mkdir(parents=True, exist_ok=True)
            tmp = cache / f"model.json.{os.getpid()}"
            tmp.write_bytes(body)
            tmp.replace(cached)
        else:
            digest = _check(body, manifest, pin, f"cached {version}")
    except GcpError as e:
        raise ModelLoadError(f"could not fetch model from {uri}: {e}") from e
    spec = json.loads(body)
    if spec.get("meta", {}).get("version") != version:
        raise ModelLoadError(f"model.json meta.version {spec.get('meta', {}).get('version')!r} != {version!r}")
    return LoadedModel(Model(spec), version, digest, f"gs://{bucket}/{base}/{version}")


def load_model(uri: str | None = None, pin: str | None = None, *, refresh: bool = False) -> LoadedModel:
    """Load (once per process) the configured model."""
    uri = uri if uri is not None else os.environ.get("SHIPCUBE_MODEL_URI", "bundled")
    pin = pin if pin is not None else os.environ.get("SHIPCUBE_MODEL_SHA256") or None
    key = f"{uri}|{pin}"
    with _lock:
        if key in _loaded and not refresh:
            return _loaded[key]
        with obs.timed(log, "model.loaded", uri=uri, pinned=bool(pin)) as extra:
            lm = load_bundled(pin) if uri in ("", "bundled") else load_gcs(uri, pin)
            extra.update(version=lm.version, sha256=lm.sha256[:16], source=lm.source)
        _loaded[key] = lm
        return lm
