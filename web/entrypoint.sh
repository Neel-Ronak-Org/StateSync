#!/bin/sh
# web/entrypoint.sh -- runtime configuration for the StateSync web container.
#
# ORCHESTRATOR_URL is where the USER'S browser reaches the orchestrator, which
# differs per deployment, so it is set at container start rather than baked into
# the bundle. It is written in two places: /config.js (read by the app) and the
# CSP's connect-src (so the browser may call it). It is validated first because
# it lands inside a script and an HTTP header: only an http(s) URL made of
# ordinary URL characters is accepted; anything else stops the container.
set -eu

URL="${ORCHESTRATOR_URL:-http://localhost:8090}"
case "$URL" in
  http://*|https://*) ;;
  *) echo "ORCHESTRATOR_URL must start with http:// or https:// (got: $URL)" >&2; exit 1 ;;
esac
if printf '%s' "$URL" | grep -q '[^A-Za-z0-9:/._-]'; then
  echo "ORCHESTRATOR_URL contains characters outside [A-Za-z0-9:/._-]; refusing to start." >&2
  exit 1
fi
ORIGIN=$(printf '%s' "$URL" | sed -E 's#^(https?://[^/]+).*$#\1#')

printf 'window.__STATESYNC_CONFIG__ = { orchestratorUrl: "%s" };\n' "$URL" > /usr/share/nginx/html/config.js
sed -i "s#__ORCHESTRATOR_ORIGIN__#${ORIGIN}#g" /etc/nginx/conf.d/default.conf

echo "StateSync web configured: orchestrator ${URL} (CSP connect-src ${ORIGIN})"
exec "$@"
