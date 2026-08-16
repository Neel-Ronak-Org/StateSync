FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifest first (cache layer)
COPY pyproject.toml ./
# The `|| <literal list>` fallback below is a SECOND copy of pyproject.toml's
# dependencies, and it had already drifted: it was missing `pglast` (imported
# by sql_agent for interrogation targeting) and, once the Azure adapter landed,
# `openai`. If the first command ever failed, the fallback would quietly build
# an image with a DIFFERENT dependency set than the one declared -- and
# `2>/dev/null` hid the reason it fell back.
#
# Not deleted, because the fallback exists for a real uv/pyproject failure mode
# someone hit here. Made safe instead:
#   - stderr is no longer discarded, so a fallback says why it happened
#   - the list is synced with pyproject.toml
#   - an import check runs AFTER install, so a fallback that produced a
#     crippled image fails the BUILD instead of shipping
# That last line is the guard that matters. This repo has twice shipped code
# that was committed but not running (d5a5d35, 5301b89); this is the same
# defect one layer down -- declared but not installed.
RUN uv pip install --system --no-cache -r pyproject.toml || \
    uv pip install --system --no-cache \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "httpx>=0.27.0" \
    "pydantic>=2.7.0" \
    "langgraph>=0.1.0" \
    "langchain-core>=0.2.0" \
    "anthropic>=0.28.0" \
    "openai>=1.40.0" \
    "jsonschema>=4.22.0" \
    "digipin>=2.1.1" \
    "pglast>=6.2"

# Fail the build, loudly, if any declared runtime dependency is absent.
RUN python -c "\
import importlib.util as u, sys; \
missing = [m for m in ('fastapi','uvicorn','httpx','pydantic','langgraph','langchain_core','anthropic','openai','jsonschema','digipin','pglast') if u.find_spec(m) is None]; \
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
