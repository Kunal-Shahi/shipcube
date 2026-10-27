# ShipCube MCP server + control-plane API, one process (services/mcp mounts services/api in-process).
# Build context: repo root.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /srv
COPY services/api services/api
COPY services/mcp services/mcp
COPY packages/monitor packages/monitor
WORKDIR /srv/services/mcp
RUN uv sync --locked --no-dev \
 && uv pip install --python .venv/bin/python "../../packages/monitor[gcp]"
RUN useradd --system --uid 10001 app
USER app
ENV HOST=0.0.0.0 PORT=8080
CMD [".venv/bin/python", "-m", "shipcube_mcp.server"]
