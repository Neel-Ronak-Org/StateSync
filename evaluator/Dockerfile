FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

RUN uv pip install --system --no-cache \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "httpx>=0.27.0" \
    "apscheduler>=3.10.0" \
    "vl-convert-python>=1.6.0"

COPY main.py config_loader.py mcp_client.py scorers.py history_store.py vision.py ./

# Config is mounted as a volume at runtime (not baked into the image).
# /app/data is the evaluator_data volume mount point for persisted history
# (BD8) -- created here so it exists with the right ownership even before
# the volume is first attached.
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8095

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8095", \
     "--log-level", "info"]
