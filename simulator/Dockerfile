# StateSync live-data simulator (docs/V3_PLAN.md Phase 2b, migration 020).
# Build by explicit service name only: `docker compose build simulator`.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
RUN uv pip install --system --no-cache "psycopg[binary]>=3.1.0"

COPY model.py main.py ./

# Config is mounted at runtime (./config:/app/config:ro), not baked in.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN useradd --system --no-create-home simulator
USER simulator

CMD ["python", "main.py"]
