#!/bin/sh
# frontend/entrypoint.sh
# Substitutes the ORCHESTRATOR_URL placeholder in index.html at container start.
# This avoids baking the URL into the image (which would be environment-specific).

set -e

ORCH_URL="${ORCHESTRATOR_URL:-http://localhost:8090}"

# Replace the placeholder in the HTML file
sed -i "s|window.__ORCHESTRATOR_URL__ || 'http://localhost:8090'|window.__ORCHESTRATOR_URL__ = '${ORCH_URL}'; window.__ORCHESTRATOR_URL__|g" \
    /usr/share/nginx/html/index.html

echo "Frontend configured. Orchestrator URL: ${ORCH_URL}"

exec "$@"
