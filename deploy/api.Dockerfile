# One image for the control plane, run as two Cloud Run services (deploy/deploy.sh):
#   shipcube-mcp     python -m shipcube_mcp.server        MCP + API, in one process
#   shipcube-worker  python /srv/deploy/worker_service.py durable operations worker
# Contents, all from this commit: services/api, services/mcp, the data layer (shipcube-data, pinned
# in services/mcp/uv.lock), the load estimator (packages/placement, packages/profiler,
# ml/workload-classifier) and the monitoring provider (packages/monitor). Build context: repo root.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv
COPY services/api services/api
COPY services/mcp services/mcp
COPY packages/placement packages/placement
COPY packages/profiler packages/profiler
COPY packages/monitor packages/monitor
COPY ml/workload-classifier ml/workload-classifier
WORKDIR /srv/services/mcp
# The lock pins shipcube-data; the estimator and monitor are installed from this commit's paths,
# so the image matches the code being deployed (the lock's estimator pin is an older commit).
RUN uv sync --locked --no-dev --extra data \
 && uv pip install --python .venv/bin/python --no-cache \
      "../../ml/workload-classifier[gcp]" ../../packages/profiler ../../packages/placement "../../packages/monitor[gcp]"
# shipcube-data's demo migrations aren't packaged; take them from the same pinned commit.
ARG SHIPCUBE_DATA_REV=5cdc6f8eb44209370e7f7da50104aa4a18ca2034
RUN git init -q /tmp/sd && cd /tmp/sd \
 && git fetch -q --depth 1 https://github.com/Kunal-Shahi/shipcube-data "$SHIPCUBE_DATA_REV" \
 && git checkout -q FETCH_HEAD && mkdir -p /srv/data && cp -r examples/olist/migrations /srv/data/olist-migrations

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PATH=/srv/services/mcp/.venv/bin:$PATH
COPY --from=build /srv /srv
COPY deploy/runtime/worker_service.py /srv/deploy/worker_service.py
RUN useradd --system --uid 10001 app
USER app
WORKDIR /srv/services/mcp
ENV HOST=0.0.0.0 PORT=8080 SHIPCUBE_DATA_MIGRATIONS=/srv/data/olist-migrations
CMD ["python", "-m", "shipcube_mcp.server"]
