# Migration job image: the runner plus the SQL files. Build context: repo root.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /srv/deploy/migrate
COPY deploy/migrate/ ./
RUN uv sync --locked --no-dev
COPY db/migrations /srv/db/migrations
RUN useradd --system --uid 10001 app
USER app
ENV MIGRATIONS_DIR=/srv/db/migrations
ENTRYPOINT [".venv/bin/python", "-m", "shipcube_migrate"]
CMD ["up"]
