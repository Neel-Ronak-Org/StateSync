FROM python:3.12.14-slim

# The upload parser (parser/server.py): standard library and pypdf, with the exact versions in
# requirements.lock.
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
