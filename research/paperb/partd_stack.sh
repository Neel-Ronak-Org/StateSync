#!/usr/bin/env bash
# research/paperb/partd_stack.sh
# Part D v3's own MCP and orchestrator (docs/specs/routing_docs_fixes.md §7). The shared stack
# (statesync-mcp-1, statesync-orchestrator-1) and its images are never touched:
#   - images are built from the committed code under the tag :partd (compose.partd-images.yml);
#   - containers start with `docker compose run --no-deps --name ...`, which does not take the
#     service's network alias, so "mcp" still resolves to the shared MCP for everyone else;
#   - the Part D orchestrator reaches the Part D MCP by container name, on 127.0.0.1:8091.
# Both read the live config/ like the shared ones; the orchestrator gets a Part D flags file over
# /app/config/flags.toml.
#
#   research/paperb/partd_stack.sh build                 build both :partd images from HEAD
#   research/paperb/partd_stack.sh up <flags.toml>       (re)start both containers with that flags file
#   research/paperb/partd_stack.sh down                  remove both containers
set -euo pipefail
cd "$(dirname "$0")/../.."
MCP=statesync-mcp-paperb
ORCH=statesync-orchestrator-paperb
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f research/paperb/compose.partd-images.yml)
RECORD=research/paperb/partd_images.json

wait_healthy() {   # $1 container
  for _ in $(seq 1 90); do
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo missing)
    [[ "$state" == "healthy" ]] && return 0
    sleep 2
  done
  echo "$1 did not become healthy (last state: $state); see: docker logs $1" >&2
  return 1
}

case "${1:-}" in
  build)
    if ! git diff --quiet HEAD -- orchestrator mcp; then
      echo "orchestrator/ or mcp/ has uncommitted changes; commit first so the images match a commit" >&2
      exit 1
    fi
    commit=$(git rev-parse --short HEAD)
    docker build -q -t statesync-mcp:partd ./mcp >/dev/null
    docker build -q -t statesync-orchestrator:partd ./orchestrator >/dev/null
    printf '{"commit": "%s", "built_at": "%s", "mcp_image": "%s", "orchestrator_image": "%s"}\n' "$commit" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(docker image inspect -f '{{.Id}}' statesync-mcp:partd)" \
      "$(docker image inspect -f '{{.Id}}' statesync-orchestrator:partd)" > "$RECORD"
    echo "built :partd images from $commit"
    ;;
  up)
    flags="${2:?usage: $0 up <flags.toml>}"
    [[ -f "$flags" ]] || { echo "no such flags file: $flags" >&2; exit 2; }
    docker rm -f "$MCP" "$ORCH" >/dev/null 2>&1 || true
    "${COMPOSE[@]}" run -d --no-deps --name "$MCP" mcp >/dev/null
    wait_healthy "$MCP"
    "${COMPOSE[@]}" run -d --no-deps --name "$ORCH" -p 127.0.0.1:8091:8090 \
      -e MCP_URL="http://$MCP:8080" -v "$PWD/$flags:/app/config/flags.toml:ro" orchestrator >/dev/null
    wait_healthy "$ORCH"
    echo "$MCP and $ORCH up (127.0.0.1:8091) with $flags, images from $(sed -E 's/.*"commit": "([^"]+)".*/\1/' "$RECORD" 2>/dev/null || echo '?')"
    ;;
  down)
    docker rm -f "$MCP" "$ORCH" >/dev/null 2>&1 || true
    echo "removed $MCP and $ORCH"
    ;;
  *)
    echo "usage: $0 build | up <flags.toml> | down" >&2
    exit 2
    ;;
esac
