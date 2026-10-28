# Local stack image for the control plane: MCP + API, worker, operator CLI and migrations.
# Same contents as production (deploy/api.Dockerfile on PR #13): services/api, services/mcp, the data
# layer, the load estimator and the monitor, installed from this checkout. Adds psql (migrations run
# owner_defaults.sql) and the Docker CLI (the local deployer and profiler use the mounted socket).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl postgresql-client \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
 && rm -rf /var/lib/apt/lists/*
# pack (Cloud Native Buildpacks) builds apps without a Dockerfile, like Cloud Build does in production.
ARG TARGETARCH
ARG PACK_VERSION=0.40.9
RUN suffix=$([ "$TARGETARCH" = arm64 ] && echo linux-arm64 || echo linux) \
 && curl -fsSL "https://github.com/buildpacks/pack/releases/download/v${PACK_VERSION}/pack-v${PACK_VERSION}-${suffix}.tgz" \
    | tar -xz -C /usr/local/bin pack && pack --version
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /srv
COPY services/api services/api
COPY services/mcp services/mcp
COPY packages/placement packages/placement
COPY packages/profiler packages/profiler
COPY packages/monitor packages/monitor
COPY ml/workload-classifier ml/workload-classifier
WORKDIR /srv/services/mcp
RUN uv sync --locked --no-dev --extra data \
 && uv pip install --python .venv/bin/python --no-cache \
      "../../ml/workload-classifier[gcp]" ../../packages/profiler ../../packages/placement "../../packages/monitor[gcp]"
ARG SHIPCUBE_DATA_REV=21fc62d892d9e7f422a2645850d5f20310152ba5
RUN git init -q /tmp/sd && cd /tmp/sd \
 && git fetch -q --depth 1 https://github.com/Kunal-Shahi/shipcube-data "$SHIPCUBE_DATA_REV" \
 && git checkout -q FETCH_HEAD && mkdir -p /srv/data && cp -r examples/olist/migrations /srv/data/olist-migrations \
 && rm -rf /tmp/sd
COPY infra/db /srv/infra/db
ENV PATH=/srv/services/mcp/.venv/bin:$PATH SHIPCUBE_DATA_MIGRATIONS=/srv/data/olist-migrations HOST=0.0.0.0 PORT=8080
CMD ["python", "-m", "shipcube_mcp.server"]
