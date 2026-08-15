#!/usr/bin/env bash
# scripts/verify_fv3.sh
# F-V3 — Full metric re-verification.
#
# "Execute, unchanged: B-V1 (M10), C1-V1 (M1), C1-V3 (M2), C2-V2 (M5),
# C2-V8 (M4), D-V1/D-V2 (M3), E1-V3 (M6), E2-V1 (M7)." (F-V3)
#
# Reuses each phase's own bench script UNCHANGED (F2 Notes: "do not write a
# second, looser procedure for the re-check"). Each script writes its own
# JSON report to docs/*_report.json, overwriting the prior run -- the
# resulting file IS the fresh number; this wrapper just sequences them
# (LLM-heavy, must not run concurrently with each other or with
# experiment_tool_ceiling.py's over-exposure part -- shared local-model
# contention would distort every number) and records exit codes.
#
# E2-V1 (M7) is DELIBERATELY NOT run for a quality number here -- the
# Synthesizer still emits chart:null on every route (Task 10, deferred).
# scripts/verify_e2.py already re-confirms E2-V1's validity/rejection half
# (model-independent) separately.
#
# BUG FOUND AND FIXED (Phase F): several of these scripts toggle
# config/flags.toml's memory.enabled / normalization.enabled as PART of
# their own control-vs-treatment procedure, and check_c2v8.py's own last
# step (the golden-recovery check) deliberately leaves memory.enabled=true
# on exit -- by design, for that script's own final assertion. Chaining
# these scripts back-to-back (as this wrapper does) means a later script
# can silently inherit a flag state a PRIOR script left behind, contaminating
# its own control arm. This actually happened on first use: D-V1/D-V2 ran
# immediately after C2-V8 and inherited memory.enabled=true, producing a
# collapsed English reference line (25% vs. the recorded 80%) that had
# nothing to do with normalization at all -- see docs/phase_f_log.md.
# Reset to the shipped defaults before every step now, so no step can ever
# start from a flag state a previous one left behind.

set -uo pipefail   # not -e: one script's failure must not skip the rest

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Shipped defaults (config/flags.toml comments / Appendix 3). Reset before
# every step -- cheap, and it makes each step's starting state independent
# of whatever the previous step happened to leave behind.
reset_flags_to_defaults() {
  python3 - <<'PYEOF'
import re
from pathlib import Path
p = Path("config/flags.toml")
text = p.read_text()
text, n1 = re.subn(r'(\[memory\]\s*\n\s*enabled\s*=\s*)(true|false)', r'\g<1>false', text)
text, n2 = re.subn(r'(\[normalization\]\s*\n\s*enabled\s*=\s*)(true|false)', r'\g<1>true', text)
assert n1 == 1 and n2 == 1, f"flag reset failed: memory matches={n1} normalization matches={n2}"
p.write_text(text)
print(f"    Reset: memory.enabled=false, normalization.enabled=true (shipped defaults).")
PYEOF
}

RESULTS=()

run_step() {
  local label="$1"; shift
  echo ""
  echo "── ${label} ──────────────────────────────────────────"
  local start end
  start=$(date +%s)
  if "$@"; then
    end=$(date +%s)
    echo "  [${label}] exit 0 ($((end - start))s)"
    RESULTS+=("${label} PASS $((end - start))s")
  else
    end=$(date +%s)
    echo "  [${label}] exit non-zero ($((end - start))s) -- see output above"
    RESULTS+=("${label} FAIL $((end - start))s")
  fi
}

echo "F-V3 — Full metric re-verification. Started $(date -u +%Y-%m-%dT%H:%M:%SZ)."

reset_flags_to_defaults
run_step "B-V1 (M10)"     bash scripts/verify_b.sh

reset_flags_to_defaults
run_step "C1-V1 (M1)"     python3 scripts/bench_memory.py

reset_flags_to_defaults
run_step "C1-V3 (M2)"     python3 scripts/check_consistency.py

reset_flags_to_defaults
run_step "C2-V2 (M5)"     python3 scripts/bench_correction.py

reset_flags_to_defaults
run_step "C2-V8 (M4)"     python3 scripts/check_c2v8.py

reset_flags_to_defaults
run_step "D-V1/D-V2 (M3)" python3 scripts/bench_multilingual.py

reset_flags_to_defaults
run_step "E1-V3 (M6)"     python3 scripts/bench_grounding.py

reset_flags_to_defaults
echo ""
echo "── Summary ──────────────────────────────────────────"
for r in "${RESULTS[@]}"; do
  echo "  ${r}"
done
echo ""
echo "F-V3 re-verification run complete. $(date -u +%Y-%m-%dT%H:%M:%SZ)."
echo "Numbers: each step's own docs/*_report.json (freshly overwritten)."
echo "E2-V1 (M7) quality: not run here -- deferred (Task 10), see docs/phase_e2_log.md."
