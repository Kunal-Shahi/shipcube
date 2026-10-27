# services/api

The ShipCube control-plane API. It owns all state (apps, operations, caps, access, data sources), drives GCP, and is the only place that makes authorization decisions. The MCP server and the web console are both clients of it.

- **Contract:** [`openapi.yaml`](openapi.yaml), hand-written and stable. `tests/test_api.py` fails if the FastAPI routes stop matching it.
- **Implementation:** Python 3.12, FastAPI + Pydantic (`shipcube_api/`), with in-memory mock state. Operations advance with wall-clock time so that polling and progress behave like a real deploy.
  - `models.py` holds the request and response models. The MCP tools reuse them for their schemas.
  - `auth.py` handles PATs as in security's `api_tokens` table: `sc_pat_<id>_<secret>`, HMAC-SHA-256 with a pepper, 90-day maximum, scopes.
  - `app.py` holds the routes. Every route checks a scope. Approve, reject and restore accept only a console session.
  - `explain.py` holds the estimate explainer: Muse Spark (mocked) with a rule-based fallback; `explained_by` says which ran.
  - `store.py` has the approvals: the first linked deploy, cap raises above the workspace default, `domain:` grants and deletes wait in `awaiting_approval` (72 h) until approved. Deletes are soft and restorable for 7 days.
- **Deploy shape:** normally served in the same process as the MCP server (`services/mcp`). It can also run alone.
- **Design:** `docs/research/networking.md` §3 (REST, versioning, the job model) and §4 (request flow).

```bash
cd services/api
SHIPCUBE_DEV_TOKEN=sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ uv run uvicorn shipcube_api.app:app --port 8081
curl -s localhost:8081/v1/me -H "Authorization: Bearer sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ"
uv run pytest
```
