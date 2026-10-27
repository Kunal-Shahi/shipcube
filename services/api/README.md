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

## Real GCP deploys (`SHIPCUBE_BACKEND=gcp`)

With `SHIPCUBE_BACKEND=gcp`, the deploy tools act on GCP with Application Default Credentials (`gcloud auth application-default login`). The code is in `shipcube_api/deploy/gcp.py`, behind the `Deployer` interface in `deploy/base.py`. `deploy/mock.py` is the default.

| Step | What happens |
|---|---|
| `create_upload` | V4 signed `PUT` URL for one object, `gs://shipcube-uploads-ks/uploads/<workspace>/<upload_id>.tgz`, valid 15 minutes, signed as `sc-api@shipcube-control-ks` through IAM signBlob |
| validate | `upload`: check the object exists and is ≤ 100 MB. `inline`: tar.gz the files into the bucket. `git`: Cloud Build `gitSource`. `image`: skip the build. |
| build | Cloud Build in `asia-south1` with buildpacks (`gcr.io/k8s-skaffold/pack`, `--builder gcr.io/buildpacks/builder:latest --publish`), running as `sc-builder@shipcube-apps-ks`, with logs in Cloud Logging. Publishes `asia-south1-docker.pkg.dev/shipcube-apps-ks/shipcube/<service>:<op id>`. Python apps need `requirements.txt` and a `Procfile`. |
| deploy | Creates the runtime SA `app-<hash>@shipcube-apps-ks`, with no project roles. Creates or updates the Cloud Run v2 service `<app>-<hash6>` with gen2, scale to zero (`min 0`), caps mapped to `maxInstanceCount`, concurrency, CPU, memory and timeout, `cpuIdle`, `iapEnabled`, and the invoker IAM check on. The IAP service agent gets `roles/run.invoker`. |
| configure_access | The app's access list becomes the IAP policy: `roles/iap.httpsResourceAccessor` on `projects/<num>/iap_web/cloud_run-<region>/services/<service>`. It's owner-only by default. |
| health_check | The service's terminal condition must be `CONDITION_SUCCEEDED`. |
| `delete_app` (approved) | Soft delete: the IAP accessors are removed and ingress is set to internal-only, so the app is unreachable but intact. `restore` reverses it. A purge sweep on the runner deletes the service, the runtime SA and the images after 7 days. |
| linked target | The same calls with credentials impersonating `ws-<id>-deployer@shipcube-control-ks`, using the project's `shipcube-builder@` and `shipcube-apps@` SAs from security's `link-project.sh`. **Not yet run against a real linked project.** |

Operations run on an in-process background runner (`runner.py`). They are lost if the process restarts, so run one instance with `min-instances=1`. The Cloud Tasks step machine in `docs/research/networking.md` §3.3 replaces it once operations are stored in Postgres.

**One-time setup:** `scripts/gcp_setup.sh` (idempotent) enables the APIs, creates the repo, the bucket (1-day lifecycle), `sc-builder`, `sc-api` and the IAP service agent, and makes the grants.

**IAP on a project without an organization** needs one manual step in the console:
1. In `shipcube-apps-ks`, set Google Auth Platform → Branding to **External**. While it's in Testing, add every Google account that should sign in as a test user.
2. Create a **Web application** OAuth client with the redirect URI `https://iap.googleapis.com/v1/oauth/clientIds/CLIENT_ID:handleRedirect`.
3. Apply it:

```bash
printf 'access_settings:\n  oauth_settings:\n    client_id: CLIENT_ID\n    client_secret: CLIENT_SECRET\n' > /tmp/iap.yaml && gcloud iap settings set /tmp/iap.yaml --project=shipcube-apps-ks && rm /tmp/iap.yaml
```

Without this step, IAP answers `502 Empty Google Account OAuth client ID(s)/secret(s)`.

```bash
SHIPCUBE_BACKEND=gcp SHIPCUBE_OWNER_EMAIL=you@gmail.com SHIPCUBE_DEV_TOKEN=sc_pat_... uv run python -m shipcube_mcp.server   # from services/mcp
```

