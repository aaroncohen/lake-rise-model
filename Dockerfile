# Stateless prediction API (spec 7: package as a Docker image, config via env).
# The tag follows Debian security rebuilds of this exact Python release.
FROM python:3.14.10-slim

# uv installs exactly what uv.lock pins (hash-checked). Pinned, and >= 7 days old when chosen.
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY artifacts ./artifacts
RUN uv sync --frozen --no-dev

EXPOSE 8000
# Config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT (optional).
CMD ["uvicorn", "lake_rise.api:app", "--host", "0.0.0.0", "--port", "8000"]
