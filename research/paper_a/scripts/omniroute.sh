#!/usr/bin/env bash
# demo/omniroute.sh
# A local OmniRoute (github.com/diegosouzapw/OmniRoute, MIT) for trying free tiers and routed
# providers before paying for credits (owner, 2026-10-03). Not part of StateSync's stack: its own
# container, pinned version, bound to 127.0.0.1 only, data in the omniroute_data volume.
# StateSync reaches it as [llm.endpoints.local_router] (http://host.docker.internal:20128/v1)
# with provider = "openai_compat" and the client key in secrets/local_router_api_key.
#
#   ./demo/omniroute.sh         start (dashboard: http://localhost:20128)
#   ./demo/omniroute.sh stop    stop and remove the container (the volume is kept)
#
# In the dashboard, before any measurement: turn prompt compression OFF, create a client API key
# for StateSync, and connect providers yourself. Measurements keep fallback = "off".
set -euo pipefail
NAME=statesync-omniroute
IMAGE="diegosouzapw/omniroute:3.8.51"
if [[ "${1:-}" == "stop" ]]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME"; exit 0; fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped -p 127.0.0.1:20128:20128 \
  -v omniroute_data:/app/data "$IMAGE" >/dev/null
echo -n "waiting for OmniRoute"
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:20128/; then echo; echo "OmniRoute up: http://localhost:20128 (API: /v1)"; exit 0; fi
  echo -n "."; sleep 2
done
echo; echo "OmniRoute did not answer; see: docker logs $NAME" >&2; exit 1
