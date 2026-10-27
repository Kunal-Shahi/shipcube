"""Publish the trained model to GCS as an immutable, versioned object set.

    python scripts/publish_model.py gs://<bucket>/models/workload-classifier [--latest]

Uploads artefacts/model.json, manifest.json and metrics.json under <prefix>/<version>/
with ifGenerationMatch=0, so a published version can never be overwritten. With
--latest, it then points <prefix>/latest.json at the new version. Uses Application
Default Credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcube_classifier.gcp import GcpClient, GcpError  # noqa: E402
from shipcube_classifier.registry import _parse_gs  # noqa: E402

ART = ROOT / "src" / "shipcube_classifier" / "artefacts"


def upload(client: GcpClient, bucket: str, name: str, body: bytes, *, immutable: bool) -> None:
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
    params = {"uploadType": "media", "name": name}
    if immutable:
        params["ifGenerationMatch"] = "0"
    client.post(url, data=body, params=params, headers={"Content-Type": "application/json"},
                idempotent=immutable)  # a precondition-guarded create is safe to retry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", help="gs://<bucket>/models/workload-classifier")
    ap.add_argument("--latest", action="store_true", help="also point latest.json at this version")
    a = ap.parse_args()
    bucket, prefix = _parse_gs(a.prefix + "/x")
    prefix = prefix[:-2]
    manifest = json.loads((ART / "manifest.json").read_text())
    body = (ART / "model.json").read_bytes()
    if hashlib.sha256(body).hexdigest() != manifest["sha256"]:
        sys.exit("model.json does not match manifest.json; retrain or fix the artefacts")
    v = manifest["version"]
    client = GcpClient()
    for fname in ("model.json", "manifest.json", "metrics.json"):
        try:
            upload(client, bucket, f"{prefix}/{v}/{fname}", (ART / fname).read_bytes(), immutable=True)
            print(f"uploaded gs://{bucket}/{prefix}/{v}/{fname}")
        except GcpError as e:
            if e.status == 412:
                print(f"exists   gs://{bucket}/{prefix}/{v}/{fname} (immutable, left as is)")
            else:
                raise
    if a.latest:
        upload(client, bucket, f"{prefix}/latest.json",
               json.dumps({"version": v, "sha256": manifest["sha256"]}).encode(), immutable=False)
        print(f"latest -> {v}")
    print(f"SHIPCUBE_MODEL_URI=gs://{bucket}/{prefix}/{v}  SHIPCUBE_MODEL_SHA256={manifest['sha256']}")


if __name__ == "__main__":
    main()
