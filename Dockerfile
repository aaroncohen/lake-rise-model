# Stateless prediction API (spec 7: package as a Docker image, config via env).
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
COPY artifacts ./artifacts
RUN pip install --no-cache-dir -e .

EXPOSE 8000
# Config via env: HA_URL, HA_TOKEN, LAKE_RISE_ARTIFACT (optional).
CMD ["uvicorn", "lake_rise.api:app", "--host", "0.0.0.0", "--port", "8000"]
