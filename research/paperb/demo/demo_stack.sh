#!/usr/bin/env bash
# research/paperb/demo/demo_stack.sh
# A second copy of StateSync running the architecture Paper B evaluated, for demos. Nothing shared is touched:
# the shared app (localhost:3000 -> orchestrator 8090) keeps config/flags.toml's defaults.
#   up    : Part D MCP + an orchestrator on 127.0.0.1:8091 with research/paperb/demo/flags_demo.toml and
#           statesync_demo.toml mounted, and a web app on http://127.0.0.1:3090 calling it, with the demo's
#           sample questions. Images: :partd (research/paperb/partd_stack.sh build) and statesync-frontend:demo.
#   build : build the :partd images and the demo web image from HEAD
#   down  : remove the demo containers
set -euo pipefail
cd "$(dirname "$0")/../../.."
D=research/paperb/demo
MCP=statesync-mcp-paperb; ORCH=statesync-orchestrator-paperb; WEB=statesync-frontend-demo
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f research/paperb/compose.partd-images.yml)
case "${1:-}" in
  build)
    research/paperb/partd_stack.sh build
    docker build -q -t statesync-frontend:demo ./web >/dev/null && echo "built statesync-frontend:demo from $(git rev-parse --short HEAD)" ;;
  up)
    docker rm -f "$MCP" "$ORCH" "$WEB" >/dev/null 2>&1 || true
    "${COMPOSE[@]}" run -d --no-deps --name "$MCP" mcp >/dev/null
    for _ in $(seq 1 60); do [[ $(docker inspect -f '{{.State.Health.Status}}' $MCP) == healthy ]] && break; sleep 2; done
    "${COMPOSE[@]}" run -d --no-deps --name "$ORCH" -p 127.0.0.1:8091:8090 -e MCP_URL="http://$MCP:8080" \
      -v "$PWD/$D/flags_demo.toml:/app/config/flags.toml:ro" -v "$PWD/$D/statesync_demo.toml:/app/config/statesync.toml:ro" \
      -v "$PWD/secrets/opencode_api_key:/run/secrets/opencode_api_key:ro" orchestrator >/dev/null
    for _ in $(seq 1 90); do [[ $(docker inspect -f '{{.State.Health.Status}}' $ORCH) == healthy ]] && break; sleep 2; done
    docker run -d --name "$WEB" -p 127.0.0.1:3090:80 -e ORCHESTRATOR_URL=http://127.0.0.1:8091 \
      -v "$PWD/$D/suggestions.json:/etc/statesync/suggestions.json:ro" statesync-frontend:demo >/dev/null
    echo "demo up: http://127.0.0.1:3090 (API 127.0.0.1:8091)" ;;
  down)
    docker rm -f "$MCP" "$ORCH" "$WEB" >/dev/null 2>&1 || true; echo "demo removed" ;;
  *) echo "usage: $0 build | up | down" >&2; exit 2 ;;
esac
