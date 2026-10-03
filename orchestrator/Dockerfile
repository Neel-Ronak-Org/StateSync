FROM python:3.12.14-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first (cache layer). Exact versions from the lock: pyproject.toml
# declares the compatible ranges, requirements.lock is what runs (frozen from the running image
# on 2026-10-02). The former `|| <inline list>` fallback guarded a uv failure parsing
# pyproject.toml; installing a plain pinned file removes that failure mode, and the import
# check below still fails the build if anything declared is missing.
COPY pyproject.toml requirements.lock ./
RUN uv pip install --system --no-cache -r requirements.lock

# Fail the build, loudly, if any declared runtime dependency is absent.
RUN python -c "\
import importlib.util as u, sys; \
missing = [m for m in ('fastapi','uvicorn','httpx','pydantic','langgraph','langchain_core','anthropic','openai','google.genai','jsonschema','digipin','pglast','vl_convert') if u.find_spec(m) is None]; \
sys.exit('MISSING DEPENDENCIES IN IMAGE: ' + ', '.join(missing) if missing else 0)"

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
