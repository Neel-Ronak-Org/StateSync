FROM python:3.12.14-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first (a cached layer): pyproject.toml declares the compatible ranges, requirements.lock
# is what is installed, exactly.
COPY pyproject.toml requirements.lock ./
RUN uv pip install --system --no-cache -r requirements.lock

# Fail the build, loudly, if any declared runtime dependency is absent.
RUN python -c "\
import importlib.util as u, sys; \
missing = [m for m in ('fastapi','uvicorn','httpx','pydantic','langgraph','langchain_core','openai','jsonschema','pglast') if u.find_spec(m) is None]; \
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
