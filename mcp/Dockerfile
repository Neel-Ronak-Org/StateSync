FROM python:3.12.14-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app

# Exact versions from the lock (pyproject.toml declares the compatible ranges; the lock is what
# runs). The old inline list was a second, unpinned copy of pyproject's dependencies.
COPY pyproject.toml requirements.lock ./
RUN uv pip install --system --no-cache -r requirements.lock

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
