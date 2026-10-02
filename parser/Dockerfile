FROM python:3.12.14-slim

# The upload parser (parser/server.py): standard library, pypdf (flat page text)
# and pdfplumber (layout blocks; pinned to the version scripts/ingest.py uses). Exact versions
# of everything in requirements.lock (frozen from the running image, 2026-10-02).
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
 && useradd --system --uid 10001 --no-create-home parser

WORKDIR /app
COPY server.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER parser
EXPOSE 8095

HEALTHCHECK --interval=10s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8095/health')" || exit 1

CMD ["python", "server.py"]
