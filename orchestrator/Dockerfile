FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifest first (cache layer)
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml 2>/dev/null || \
    uv pip install --system --no-cache \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "httpx>=0.27.0" \
    "pydantic>=2.7.0" \
    "langgraph>=0.1.0" \
    "langchain-core>=0.2.0" \
    "anthropic>=0.28.0" \
    "jsonschema>=4.22.0"

# Copy application source
COPY . .

# Config is mounted as a volume at runtime — not baked into the image
# (so secrets stay out of layers)

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8090

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8090", \
     "--log-level", "info"]
