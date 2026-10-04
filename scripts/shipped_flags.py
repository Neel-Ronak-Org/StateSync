#!/usr/bin/env python3
"""
scripts/shipped_flags.py
The shipped flag state, in one place, plus a guard that refuses to measure
against the wrong one.

WHY THIS EXISTS. Twice in one day a measurement was contaminated by flag state
left behind by another process:

  d7c0a6b  check_consistency.py was run while D-V1/D-V2 was mid-flight. It
           calls set_memory_enabled() and restart_orchestrator(), so it toggled
           the flag the running benchmark was measuring against. That D-V1 run
           was discarded.
  today    bench_multilingual.py was killed mid-run by a concurrent session. It
           had already written normalization.enabled=false and restarted the
           orchestrator. config/flags.toml was then restored to `true` on disk
           -- but flags are read at PROCESS START, so the live orchestrator kept
           serving with the gate disabled. A diagnose_m4.py run started in that
           window and had to be discarded.

verify_fv3.sh already calls reset_flags_to_defaults() between its steps for
this reason. That is a convention inside one harness; anything run outside it
is unprotected. This makes it a guard.

THE SECOND CASE IS WHY THIS CHECKS TWO THINGS, NOT ONE. A guard that only reads
config/flags.toml would have PASSED throughout the contamination window: the
file said `true` while the process served `false`. That is exactly the vacuous
pass this project keeps catching (F-V1's chart check passing before charts
existed; verify_seams importing repo source and so being unable to see a stale
image). So the guard also asserts LIVENESS:

  1. DISK    config/flags.toml matches the shipped defaults below.
  2. LIVE    the orchestrator container started at or after flags.toml's last
             write, so the process cannot be holding a superseded value.

Neither check alone is sufficient and neither is a substitute for the other.

DELIBERATELY NOT AN LLM-FREE PURITY TEST. A script may legitimately need a
non-shipped state -- bench_multilingual varies normalization by design, the M3
separation run varied memory on purpose. Those pass allow_nonshipped=True and
get a loud banner plus a machine-readable record in their own report, so the
deviation is stated rather than silent. What is forbidden is measuring in the
wrong state by ACCIDENT.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FLAGS_PATH = REPO_ROOT / "config" / "flags.toml"
# A few shipped switches live in config/statesync.toml, not flags.toml (the layout
# chunker, docs/specs/rag_v2.md Phase C: [library].chunker; and [retrieval].hybrid_dense_lists, F0). Sections listed here
# are read from that file with a real TOML parser; every other section is read
# from flags.toml as before.
STATESYNC_PATH = REPO_ROOT / "config" / "statesync.toml"
STATESYNC_SECTIONS = frozenset({"library", "retrieval"})
ORCHESTRATOR_CONTAINER = "statesync-orchestrator-1"

# The single source of truth. scripts/verify_seams.py's F-V6 imports this
# rather than keeping its own copy -- two hand-maintained lists of the same
# constants is the drift that BD4/BD5/BD7 are all instances of.
#
# Values are the parsed (unquoted) form: booleans as "true"/"false", strings
# bare. read_flag_state() matches TOML booleans OR quoted strings under the
# same regex and strips the quotes, so a string-valued flag is guarded no
# differently from a boolean one.
SHIPPED_DEFAULTS: dict[tuple[str, str], str] = {
    ("memory", "enabled"): "false",                     # NOTE-1, docs/spec-gaps.md
    ("normalization", "enabled"): "true",               # D13 control path for M3
    ("tenancy", "enforced"): "false",                   # D6, single-tenant as shipped
    ("evaluation.vision_scorer", "enabled"): "false",   # D11 stretch, off
    # audit 001 F-001-1: this is the flag 80d35c8 introduced to select the
    # corpus the SQL agent is shown (public=synthetic, olist=real). Its wrong
    # value is what produced 80d35c8's silent wrong-corpus measurement, and
    # until now nothing guarded it or recorded it in a report's provenance.
    ("schema_retriever", "target_schema"): "public",
    # Phase 3b lean-pipeline experiments (docs/V3_PLAN.md D-v3-16): each ships
    # at today's behaviour until the Groq baseline shows a variant holds.
    ("pipeline", "language_gate"): "llm",
    ("pipeline", "router_mode"): "always",
    ("pipeline", "sql_draft_mode"): "llm",
    ("pipeline", "synth_style"): "full",
    ("pipeline", "answer_checks"): "off",
    ("pipeline", "templates"): "off",
    # Answers you can check: annotation plus a caveat, never a change to SQL or rows.
    ("pipeline", "number_grounding"): "on",
    # RAG v2 Phase D: "dense" is today's cosine-only document search; "hybrid" fuses it with BM25.
    ("pipeline", "retrieval_mode"): "dense",
    # RAG v2 Phase E: "all" is today's search over every sample document; "dataset" keeps only
    # the question's dataset. "off" never abstains; "on" answers "not found" without a model call.
    ("pipeline", "doc_scope"): "all",
    ("pipeline", "doc_abstain"): "off",
    # RAG v3 (docs/specs/rag_v3.md): cross-encoder re-ranking and search-query rewriting, both off.
    ("pipeline", "repair_loop"): "off",   # the study's RQ4 loop; on only in measurement runs
    ("pipeline", "sql_row_cap"): "prompt",       # study fix F1; "executor" only in measurement runs until measured
    ("pipeline", "grounding_version"): "v1",     # study fix F3; "v2" only in measurement runs until measured
    ("pipeline", "self_consistency_k"): "0",     # the study's baseline; measurement runs only
    ("pipeline", "rerank"): "bge",   # owner decision 2026-10-03 (docs/eval/rag_v3_report.md)
    ("pipeline", "query_rewrite"): "off",
    # RAG v2 (docs/specs/rag_v2.md): how an upload is cut into chunks. "paragraph" is
    # today's flat-page chunker; "layout" cuts by heading and keeps tables whole.
    ("library", "chunker"): "paragraph",
    # RAG v2 F0 (post-hoc): "separate" hands RRF the sample and upload dense lists as two lists
    # (today's hybrid); "merged" sorts them together by cosine and hands it one. [retrieval] in statesync.toml.
    ("retrieval", "hybrid_dense_lists"): "separate",
}


class FlagStateError(RuntimeError):
    """Raised when a measurement would run against a non-shipped flag state."""


def _statesync_value(section: str, key: str) -> str | None:
    """A shipped switch from config/statesync.toml as its parsed string; None if it cannot be read."""
    import tomllib
    try:
        value = tomllib.loads(STATESYNC_PATH.read_text()).get(section, {}).get(key)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if value is None:
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


def read_flag_state(flags_text: str | None = None) -> dict[str, str | None]:
    """Parse the guarded flags out of flags.toml. A key that cannot be found
    reads as None, which never equals an expected value, so a parse failure
    fails the guard rather than silently passing it.

    The value alternation (true|false|quoted-string|integer) covers the flag
    shapes in SHIPPED_DEFAULTS; an integer reads as its digits (self_consistency_k). The skip-group changed from `?` to `*` (audit 001
    F-001-1) so a key can sit behind more than one comment line before it --
    `schema_retriever.target_schema` has five; the four boolean flags this
    guarded before all have zero, so their match is unchanged."""
    if flags_text is None:
        flags_text = FLAGS_PATH.read_text()
    state: dict[str, str | None] = {}
    for section, key in SHIPPED_DEFAULTS:
        if section in STATESYNC_SECTIONS:
            state[f"{section}.{key}"] = _statesync_value(section, key)
            continue
        pattern = (rf"\[{re.escape(section)}\]\s*\n(?:[^\[]*?\n)*\s*"
                   rf"{re.escape(key)}\s*=\s*(true|false|\"[^\"]*\"|-?\d+\b)")
        m = re.search(pattern, flags_text)
        if m is None:
            state[f"{section}.{key}"] = None
        else:
            val = m.group(1)
            state[f"{section}.{key}"] = val[1:-1] if val.startswith('"') else val
    return state


def disk_deviations(state: dict[str, str | None] | None = None) -> dict[str, dict[str, str | None]]:
    """{flag: {"expected": ..., "found": ...}} for every flag that is not shipped."""
    if state is None:
        state = read_flag_state()
    out = {}
    for (section, key), expected in SHIPPED_DEFAULTS.items():
        name = f"{section}.{key}"
        if state.get(name) != expected:
            out[name] = {"expected": expected, "found": state.get(name)}
    return out


def _container_started_at(container: str = ORCHESTRATOR_CONTAINER) -> float | None:
    """Epoch seconds the container last started, or None if it is not running."""
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f",
             "{{.State.Running}} {{.State.StartedAt}}", container],
            capture_output=True, text=True, timeout=30, cwd=REPO_ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    running, _, started = proc.stdout.strip().partition(" ")
    if running != "true":
        return None
    from datetime import datetime
    # Docker emits RFC3339 with nanoseconds; Python parses at most microseconds.
    cleaned = re.sub(r"\.(\d{6})\d*Z$", r".\1+00:00", started.strip()).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return None


def live_staleness() -> dict[str, object] | None:
    """None when the live process provably post-dates the flags file. Otherwise
    a dict describing the gap. Also returns a dict when the orchestrator is not
    running at all -- a benchmark cannot measure a stopped service, and
    reporting 'no problem' there is the same vacuous pass as skipping."""
    started = _container_started_at()
    if started is None:
        return {"reason": "orchestrator container is not running (or not inspectable)"}
    mtime = max(FLAGS_PATH.stat().st_mtime, STATESYNC_PATH.stat().st_mtime)
    if started >= mtime:
        return None
    from datetime import datetime
    return {
        "reason": "config/flags.toml or config/statesync.toml was modified AFTER the orchestrator "
                  "started, so the running process may hold superseded flag values "
                  "(flags are read at process start, not per query)",
        "flags_mtime": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
        "orchestrator_started": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
        "remedy": "docker compose restart orchestrator",
    }


def fixture_corpus(fixture_path: Path | str) -> str:
    """Which corpus a fixture's ground truth was written against, read from
    its own `_about.corpus` field. Every fixture in this repo except the two
    `olist*.json` ones declares no `_about.corpus` at all and is written
    against the shipped synthetic schema -- the field's ABSENCE is the
    "public" claim, not a gap, so it resolves there rather than to None.

    Audit 002 F-002-3: A1 (cycle 001) guards `schema_retriever.target_schema`
    against SHIPPED_DEFAULTS in isolation, so a harness pointed at the
    `olist_hard` fixture while `target_schema="public"` -- 80d35c8's EXACT
    configuration, since "public" IS the shipped default -- passed that guard
    clean. This function is what `require_shipped_flags(fixture=...)` uses to
    close that: it ties the flag to the fixture the harness was actually told
    to run, not just to its own committed default."""
    path = Path(fixture_path)
    try:
        about = json.loads(path.read_text()).get("_about", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return SHIPPED_DEFAULTS[("schema_retriever", "target_schema")]
    corpus = str(about.get("corpus") or "")
    return ("olist" if corpus.startswith("olist")
            else SHIPPED_DEFAULTS[("schema_retriever", "target_schema")])


def _llm_provenance() -> dict[str, object]:
    """provider + the model per role, straight out of flags.toml."""
    text = FLAGS_PATH.read_text()
    m = re.search(r'^\s*provider\s*=\s*"([^"]+)"', text, re.M)
    provider = m.group(1) if m else None
    models: dict[str, str] = {}
    if provider:
        block = re.search(rf"\[llm\.models\.{re.escape(provider)}\]\n((?:.*\n)*?)(?=\[|\Z)", text)
        if block:
            for line in block.group(1).splitlines():
                kv = re.match(r'\s*(\w+)\s*=\s*"([^"]+)"', line)
                if kv:
                    models[kv.group(1)] = kv.group(2)
    return {"provider": provider, "models": models}


def require_shipped_flags(script: str, allow_nonshipped: bool = False,
                          check_live: bool = True,
                          fixture: Path | str | None = None) -> dict[str, object]:
    """
    Assert the shipped flag state, or refuse to run.

    Returns a provenance record for embedding in the caller's own report, so a
    published number carries the flag state it was measured under instead of
    requiring timestamp archaeology to reconstruct it.

    `fixture`, if given, is checked against `schema_retriever.target_schema`
    UNCONDITIONALLY (audit 002 F-002-3) -- this is the 80d35c8 failure mode:
    the SQL agent shown one corpus while scored against another's ground
    truth, silently, because a fixture/corpus pairing was never a flag
    DEVIATION in the ordinary sense. `--allow-nonshipped-flags` does not
    bypass it; a fixture/corpus mismatch is never the point of an experiment,
    unlike (say) deliberately disabling the normalizer.
    """
    state = read_flag_state()
    deviations = disk_deviations(state)
    stale = live_staleness() if check_live else None

    fixture_mismatch: dict[str, str] | None = None
    if fixture is not None:
        wanted = fixture_corpus(fixture)
        got = (state.get("schema_retriever.target_schema")
               or SHIPPED_DEFAULTS[("schema_retriever", "target_schema")])
        if wanted != got:
            fixture_mismatch = {
                "fixture": str(fixture),
                "fixture_corpus": wanted,
                "target_schema": got,
            }

    # The provider and per-role model names belong in provenance for the same
    # reason the flag state does: a number that cannot name the model that
    # produced it is not reproducible. Read from the same flags file, so this
    # cannot disagree with what the orchestrator loaded.
    record: dict[str, object] = {
        "llm": _llm_provenance(),
        "flag_state": state,
        "shipped": not deviations and not stale,
        "disk_deviations": deviations,
        "live_staleness": stale,
        "allow_nonshipped": bool(allow_nonshipped),
        "fixture_corpus_check": fixture_mismatch,
    }

    if fixture_mismatch:
        raise FlagStateError(
            f"[{script}] REFUSING TO RUN: fixture/corpus mismatch.\n"
            f"  fixture:                          {fixture_mismatch['fixture']}\n"
            f"  fixture's corpus (_about.corpus):  {fixture_mismatch['fixture_corpus']!r}\n"
            f"  schema_retriever.target_schema:    {fixture_mismatch['target_schema']!r}\n"
            f"  This is the 80d35c8 failure mode: the SQL agent would be shown one\n"
            f"  corpus while scored against ground truth from another, SILENTLY --\n"
            f"  the score alone looks like an ordinary bad run.\n"
            f"  This refusal is UNCONDITIONAL: --allow-nonshipped-flags does not\n"
            f"  bypass it. Fix schema_retriever.target_schema in config/flags.toml\n"
            f"  to match the fixture's corpus, then re-run.")

    if not deviations and not stale:
        return record

    lines = [f"[{script}] REFUSING TO RUN: flag state is not the shipped state."]
    for name, d in deviations.items():
        lines.append(f"  config/flags.toml: {name} = {d['found']!r}, shipped is {d['expected']!r}")
    if stale:
        lines.append(f"  live: {stale['reason']}")
        for k in ("flags_mtime", "orchestrator_started", "remedy"):
            if k in stale:
                lines.append(f"    {k}: {stale[k]}")
    lines.append("  A number measured here is not comparable to any number on record.")
    lines.append("  Fix the state, or pass --allow-nonshipped-flags if the deviation is "
                 "the point of the experiment (it will be recorded in the report).")
    message = "\n".join(lines)

    if allow_nonshipped:
        print("=" * 72, file=sys.stderr)
        print(message.replace("REFUSING TO RUN", "PROCEEDING UNDER A NON-SHIPPED STATE"),
              file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        return record

    raise FlagStateError(message)


def add_flag_guard_argument(parser) -> None:
    """Shared argparse switch, so the opt-out is spelled the same everywhere."""
    parser.add_argument(
        "--allow-nonshipped-flags", action="store_true",
        help="Run even though the flag state is not the shipped default. The "
             "deviation is printed and recorded in the report.",
    )


# ── Self-test: prove the guard can FAIL, not just pass ────────────────────────
def _self_test() -> int:
    """A guard that has only ever been seen to pass is not evidence of anything.
    This exercises both directions on synthetic inputs; the real file is never
    written."""
    global FLAGS_PATH
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  ✓ PASS: {label}" + (f" — {detail}" if detail else ""))
        else:
            failed += 1
            print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))

    real = FLAGS_PATH.read_text()
    check("the committed flags.toml parses to a complete state",
          all(v is not None for v in read_flag_state(real).values()),
          json.dumps(read_flag_state(real)))
    check("the committed flags.toml IS the shipped state",
          not disk_deviations(read_flag_state(real)))

    flipped = real.replace(
        "[normalization]\nenabled                         = true",
        "[normalization]\nenabled                         = false")
    check("flipping normalization.enabled is detected",
          flipped != real and "normalization.enabled" in disk_deviations(read_flag_state(flipped)),
          str(disk_deviations(read_flag_state(flipped))))

    flipped_mem = real.replace(
        "[memory]\nenabled                         = false",
        "[memory]\nenabled                         = true")
    check("flipping memory.enabled is detected (the flag that was committed wrong once)",
          flipped_mem != real and "memory.enabled" in disk_deviations(read_flag_state(flipped_mem)))

    # audit 001 F-001-1: schema_retriever.target_schema is a STRING flag, not a
    # boolean -- exercised separately because it is the value alternation and
    # the multi-line comment skip (both added for this flag) that could regress
    # silently while the four boolean flags above kept passing.
    check("the shipped flags.toml resolves schema_retriever.target_schema (string flag, "
          "behind 5 comment lines -- the multi-line skip)",
          read_flag_state(real)["schema_retriever.target_schema"] == "public")
    flipped_schema = real.replace(
        'target_schema                   = "public"',
        'target_schema                   = "olist"')
    check("flipping schema_retriever.target_schema to olist is detected "
          "(the flag whose wrong value produced 80d35c8's silent wrong-corpus run)",
          flipped_schema != real
          and "schema_retriever.target_schema" in disk_deviations(read_flag_state(flipped_schema)),
          str(disk_deviations(read_flag_state(flipped_schema))))

    check("an unparseable flags file fails rather than passing vacuously",
          bool(disk_deviations(read_flag_state("[memory]\n# nothing here\n"))))

    # audit 002 F-002-3: A1 (above) guards the FLAG in isolation, so a fixture
    # pointed at olist while target_schema="public" -- 80d35c8's EXACT
    # configuration, since "public" IS the shipped default -- passed clean.
    # fixture_corpus() + require_shipped_flags(fixture=...) close that. Both
    # directions exercised against REAL committed fixtures, not synthetic
    # ones -- the mismatch case needs no flag-flipping at all, because the
    # shipped state itself (target_schema="public") already reproduces
    # 80d35c8 the moment an olist-corpus fixture is named.
    olist_easy = REPO_ROOT / "fixtures" / "golden" / "olist.json"
    olist_hard = REPO_ROOT / "fixtures" / "golden" / "olist_hard.json"
    public_fixture = REPO_ROOT / "fixtures" / "golden" / "phase_c2.json"
    check("fixture_corpus() reads olist.json as 'olist'",
          fixture_corpus(olist_easy) == "olist")
    check("fixture_corpus() reads olist_hard.json as 'olist'",
          fixture_corpus(olist_hard) == "olist")
    check("fixture_corpus() reads a fixture with no _about.corpus as 'public' "
          "(the field's ABSENCE is the public claim, not a gap)",
          fixture_corpus(public_fixture) == "public")

    def _raises_fixture_mismatch(fixture_path: Path, allow: bool) -> bool:
        try:
            require_shipped_flags("self-test", allow_nonshipped=allow,
                                   check_live=False, fixture=fixture_path)
        except FlagStateError as exc:
            return "fixture/corpus mismatch" in str(exc)
        return False

    check("REFUSES in 80d35c8's exact configuration: shipped target_schema="
          "'public' + the olist_hard fixture named",
          _raises_fixture_mismatch(olist_hard, allow=False),
          "the score alone would have looked like an ordinary bad run")
    check("the fixture/corpus refusal is UNCONDITIONAL -- "
          "--allow-nonshipped-flags does NOT bypass it",
          _raises_fixture_mismatch(olist_hard, allow=True))
    check("does NOT refuse when the fixture matches the shipped corpus "
          "(phase_c2.json, public, against the real shipped state)",
          not _raises_fixture_mismatch(public_fixture, allow=False))

    # The legitimate case: target_schema DELIBERATELY set to olist (a real
    # B4-style experiment) paired with an olist fixture must NOT trip the
    # fixture/corpus refusal -- only the FLAG's own deviation from
    # SHIPPED_DEFAULTS applies there, and that one IS bypassable via
    # --allow-nonshipped-flags, unlike the fixture mismatch above. Uses the
    # same FLAGS_PATH-swap technique as the liveness test below: never
    # touches config/flags.toml itself.
    import tempfile as _tempfile
    _original_path = FLAGS_PATH
    try:
        with _tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as tmp:
            tmp.write(flipped_schema)
            tmp_schema_path = Path(tmp.name)
        FLAGS_PATH = tmp_schema_path
        try:
            require_shipped_flags("self-test", allow_nonshipped=True,
                                   check_live=False, fixture=olist_hard)
            paired_ok = True
        except FlagStateError as exc:
            paired_ok = "fixture/corpus mismatch" not in str(exc)
        check("a fixture correctly PAIRED with target_schema=olist does not "
              "trip the fixture/corpus refusal (only the ordinary, "
              "bypassable flag-deviation banner applies)", paired_ok)
        tmp_schema_path.unlink(missing_ok=True)
    finally:
        FLAGS_PATH = _original_path

    # The liveness half. Exercised in BOTH directions against the real running
    # stack, because this is the half that would have caught today's incident
    # and a check only ever seen to pass proves nothing.
    #
    # The failing direction is induced by pointing FLAGS_PATH at a temp file
    # whose mtime is in the future -- never by touching config/flags.toml,
    # which would either disturb a measurement in flight or require an
    # orchestrator restart to undo. Testing a guard must not need the outage
    # the guard exists to prevent.
    import tempfile
    original_path = FLAGS_PATH
    try:
        stale = live_staleness()
        running = _container_started_at() is not None
        check("liveness: in sync with the running stack (the passing direction)",
              stale is None if running else isinstance(stale, dict),
              "in sync" if stale is None else str(stale.get("reason"))[:70])

        if not running:
            # No stack here (CI, or a laptop with docker down). The induced
            # failure below compares against a container start time that does
            # not exist, so it cannot be exercised. Say so; do NOT count it as
            # a pass. A self-test that silently shrinks in the environment
            # where it is run most often is worse than no self-test.
            print("  ⊘ SKIP: liveness: induced-failure direction — no orchestrator "
                  "container running; this is NOT a pass. Re-run against a live stack.")
        else:
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as tmp:
                tmp.write(real)
                tmp_path = Path(tmp.name)
            os.utime(tmp_path, (time.time() + 3600, time.time() + 3600))
            FLAGS_PATH = tmp_path
            induced = live_staleness()
            check("liveness: a flags file newer than the orchestrator is REFUSED "
                  "(the failing direction — today's exact incident)",
                  isinstance(induced, dict) and "AFTER the orchestrator started" in str(induced.get("reason")),
                  str(induced)[:90] if induced else "returned None — guard is vacuous")
            tmp_path.unlink(missing_ok=True)
    finally:
        FLAGS_PATH = original_path

    print(f"\n  PASSED: {passed}\n  FAILED: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    try:
        rec = require_shipped_flags("shipped_flags.py --check")
    except FlagStateError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(json.dumps(rec, indent=2))
