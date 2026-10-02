FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "psycopg[binary]>=3.1.0" \
    "psycopg-pool>=3.2.0" \
    "pglast>=6.2" \
    "pydantic>=2.7.0" \
    "httpx>=0.27.0" \
    "qdrant-client>=1.9.0,<1.10.0" \
    "cryptography>=42"

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# No limits are baked in here: row caps, SQL length and timeouts are read from
# config/statesync.toml ([mcp], [sql]); baked ENV copies used to shadow it.
# TOOL_CEILING is deliberately NOT set here: mcp/main.py reads mcp.tool_ceiling
# from config/statesync.toml. A baked copy silently overrode the config once.

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--log-level", "info"]
