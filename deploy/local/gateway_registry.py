"""Print the local gateway's app registry: one app, authenticated by a per-app scg_ token (its hash).

Upstream, in order: Muse when META_API_KEY is set (in .env or the environment), else the local
Ollama model when LOCAL_LLM=ollama, else Muse with no key (the smoke test then skips the model call).
"""

import json
import os
import sys
from pathlib import Path


def env_file(key: str) -> str:
    p = Path(__file__).with_name(".env")
    for line in p.read_text().splitlines() if p.exists() else []:
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


token_hash, workspace = sys.argv[1], sys.argv[2]
meta = os.environ.get("META_API_KEY") or env_file("META_API_KEY")
llm = os.environ.get("LOCAL_LLM") or env_file("LOCAL_LLM")
if meta:
    provider = {"provider": "meta", "model": "muse-spark-1.3"}
elif llm == "ollama":
    model = os.environ.get("LOCAL_LLM_MODEL") or env_file("LOCAL_LLM_MODEL") or "qwen2.5:0.5b"
    provider = {"provider": "openai_compatible", "model": model, "base_url": "http://ollama:11434/v1",
                "secret_ref": "env:OLLAMA_API_KEY"}
else:
    provider = {"provider": "meta", "model": "muse-spark-1.3"}
print(json.dumps({"apps": [{
    "principal": f"token:{token_hash}", "app_id": "local-smoke", "workspace_id": workspace, "app": "smoke",
    "provider_config": provider, "caps": {"requests_per_minute": 60, "ai_tokens_per_day": 200000},
}]}, indent=2))
