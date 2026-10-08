# Part D: where to pick up (2026-10-06)

Every run below saves after each answer and skips answered questions on resume, so stopping at any moment
loses at most the one answer in flight per arm.

## Held-out hybrid set (100 questions, `docs/eval/hybrid_set.json`)

**State at the stop (2026-10-06):** all three arms answered 100/100 (committed). Only grading and the
report remain (step 3); grading was stopped mid-batch and saves nothing until a batch ends, so it starts over.
The four Part D containers were stopped, not removed: `docker start statesync-mcp-paperb` first, then the three
orchestrators, keeps their images and flags.

1. If the Mac restarted, start the Part D containers again on the images in `partd_images.json`
   (MCP first, then the three orchestrators; see `partd_stack.sh` and the flags files):
   - 8092 `statesync-orchestrator-paperb-off`, flags `flags_partd_v3_off.toml` (shipped)
   - 8093 `statesync-orchestrator-paperb-route`, flags `flags_partd_v3_final_bge.toml` (v3)
   - 8091 `statesync-orchestrator-paperb`, flags `flags_partd_v3_final_bge_entities.toml` (v3_entities)
   - the host re-ranker on 127.0.0.1:8096 (`scripts/run_reranker_host.sh`)
2. Finish any arm short of 100 (the harness refuses stale containers):
   `python3 scripts/eval_hybrid.py --set held_out ask --arm <shipped|v3|v3_entities> --port <8092|8093|8091>`
3. `python3 scripts/eval_hybrid.py --set held_out grade`, then `... report`. Primary: v3 vs shipped;
   secondary: v3_entities vs v3 on the dependent 60 (fixed in commit 3bbf49f).

## Still open
- Jev routing arm (T4b): waits for the OpenRouter key in `secrets/openrouter_api_key`.
- Owner checks: `docs/eval/paperb_docs_owner_sheet_v3.md`, `docs/eval/hybrid_set_owner_check.md`,
  `docs/eval/routing_set_owner_check.md`.

## 2026-10-08 pause: R4 (router coverage line)
- Done: R4's four arms (docs/eval/routing_runs_r4.json); R2 no-harm `jev_choice+v2cov|r2|0` (180).
- To resume: `research/paperb/partd_stack.sh up research/paperb/flags_partd_v3_route_llm.toml`, then start the
  route container on 8093 with flags_partd_v3_route_llm.toml + statesync_coverage_on.toml (scratchpad route_arm.sh
  pattern: docker compose run --no-deps, -v flags, -v statesync override, -v secrets/opencode_api_key), then
  `python3 scripts/eval_routing.py --set r2 route --arm llm --port 8093 --split r2 --tag cov` (resumes at 79/180).
- Then: `python3 scripts/paperb_numbers.py`, write R4 into the paper, rebuild, commit.
