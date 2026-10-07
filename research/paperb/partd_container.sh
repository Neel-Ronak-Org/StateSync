#!/usr/bin/env bash
# research/paperb/partd_container.sh
# Paper B Part D's own orchestrator (docs/research/detect_then_fallback_study.md v2, §4): the image the
# shared stack built, with the same networks and secrets, but with flags_partd_<label>.toml mounted over
# /app/config/flags.toml and its own port (127.0.0.1:8091). The shared config/flags.toml and the shared
# orchestrator (statesync-orchestrator-1, :8090) are never touched, so a run cut off mid-way leaves
# nothing shared in a changed state.
#
#   research/paperb/partd_container.sh dense|bge   start it (replacing any running copy)
#   research/paperb/partd_container.sh stop        remove it
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME=statesync-orchestrator-paperb
docker rm -f "$NAME" >/dev/null 2>&1 || true
if [[ "${1:-}" == "stop" ]]; then echo "removed $NAME"; exit 0; fi
case "${1:-}" in dense|bge) ;; *) echo "usage: $0 dense|bge|stop" >&2; exit 2 ;; esac
docker compose run -d --no-deps --name "$NAME" -p 127.0.0.1:8091:8090 \
  -v "$PWD/research/paperb/flags_partd_$1.toml:/app/config/flags.toml:ro" orchestrator >/dev/null
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8091/health; then
    echo "$NAME up on 127.0.0.1:8091 with flags_partd_$1.toml"
    exit 0
  fi
  sleep 2
done
echo "$NAME did not answer; see: docker logs $NAME" >&2
exit 1
