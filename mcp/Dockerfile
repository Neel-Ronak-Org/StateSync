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
    "qdrant-client>=1.9.0,<1.10.0"

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Hard caps — sourced from statesync.toml values at compose time
ENV SAMPLE_ROWS_HARD_CAP=20
ENV MAX_ROWS_HARD_CAP=5000
ENV MAX_ROWS_DEFAULT=1000
ENV MAX_SQL_LENGTH=8000
ENV STATEMENT_TIMEOUT_MS=15000
ENV SESSION_IDLE_TIMEOUT_MS=60000
# TOOL_CEILING is deliberately NOT set here: mcp/main.py reads mcp.tool_ceiling
# from config/statesync.toml. A baked copy silently overrode the config once.

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--log-level", "info"]
