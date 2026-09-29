FROM python:3.12-slim

# The upload parser (parser/server.py): standard library + pypdf only.
RUN pip install --no-cache-dir "pypdf>=6.1,<7" \
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
