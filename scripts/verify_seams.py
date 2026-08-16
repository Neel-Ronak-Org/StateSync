#!/usr/bin/env python3
"""
scripts/verify_seams.py
Phase F — F-V4 (seams exist, A0.5/Appendix 4), F-V5 (vision scorer seam),
F-V6 (config discipline). All three are model-independent and structural;
none needs a live LLM call.

F-V4 checks every row of Appendix 4: the named interface exists in source,
resolves from config at a REAL dispatch point (not a comment), and lives in
the Part assigned to it. The final Appendix-4 row (write capability) is
checked in the opposite direction: confirming no such interface exists.

F-V5 checks VisionScorer specifically: registered (so get_scorer("vision")
resolves) but its content raises NotImplementedError when actually invoked
-- "failing on a missing implementation, not on a missing registration."

F-V6 just re-runs scripts/lint-no-literals.sh and reports its exit code.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ PASS: {label}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(4, 60 - len(title)))


def grep(pattern: str, path: Path) -> bool:
    if not path.exists():
        return False
    return re.search(pattern, path.read_text()) is not None


# ── F-V4 ──────────────────────────────────────────────────────────────────────
def fv4() -> None:
    section("F-V4 — Seams exist (A0.5, Appendix 4)")

    # 1. GRPO fine-tuned multilingual model -> LanguageNormalizer, D
    p = REPO_ROOT / "orchestrator" / "language_normalizer.py"
    check("LanguageNormalizer Protocol exists", grep(r"class LanguageNormalizer\(Protocol\)", p))
    check("normalization.provider resolved at a real dispatch point",
          grep(r'flags\.normalization\.provider', p) and grep(r"def get_language_normalizer", p))

    # 2. Vision Score -> Scorer registry entry "vision", F
    from scorers import get_scorer, registered_scorer_names
    check("'vision' registered in the Scorer registry", "vision" in registered_scorer_names())
    try:
        get_scorer("vision")
        check("get_scorer('vision') resolves without raising", True)
    except Exception as exc:
        check("get_scorer('vision') resolves without raising", False, str(exc))

    # 3. CSR-RAG hypergraph retrieval -> SchemaRetriever, C1
    p = REPO_ROOT / "orchestrator" / "schema_retriever.py"
    check("SchemaRetriever Protocol exists", grep(r"class SchemaRetriever\(Protocol\)", p))
    check("schema_retriever.provider resolved at a real dispatch point",
          grep(r"flags\.schema_retriever\.provider", p) and grep(r"def get_schema_retriever", p))

    # 4. Multi-tenancy enforcement -> MCP session policy layer, A/B + F
    p = REPO_ROOT / "mcp" / "session.py"
    check("tenancy.enforced read at a real dispatch point (not just present in config)",
          grep(r"cfg\.tenancy\.enforced", p))
    check("enforce_tenancy_policy wired into the RPC handler",
          grep(r"enforce_tenancy_policy", REPO_ROOT / "mcp" / "main.py"))

    # 5. HEAR root-cause node -> SpecialistNode registry, B (registry) + E1 (real routes)
    p = REPO_ROOT / "orchestrator" / "routing" / "registry.py"
    check("SpecialistNode routing table registry exists", p.exists() and grep(r"def register", p))
    check("routing.table validated at process start (not hardcoded branches)",
          grep(r"flags\.routing\.table", p) and grep(r"def validate_routing_table", p))

    # 6. Richer spatial resolution -> SpatialResolver, D
    p = REPO_ROOT / "orchestrator" / "spatial_resolver.py"
    check("SpatialResolver Protocol exists", grep(r"class SpatialResolver\(Protocol\)", p))
    check("spatial.provider resolved at a real dispatch point",
          grep(r"flags\.spatial\.provider", p) and grep(r"def get_spatial_resolver", p))

    # 7. Live network ingestion -> IngestionSource, E1 nominally / F actual
    p = REPO_ROOT / "scripts" / "ingestion_source.py"
    check("IngestionSource Protocol exists", grep(r"class IngestionSource\(Protocol\)", p))
    check("ingestion.source resolved at a real dispatch point (scripts/ingest.py)",
          grep(r"ingestion\.source|_flags_ingestion_source", p)
          and grep(r"get_ingestion_source", REPO_ROOT / "scripts" / "ingest.py"))

    # 8. Write capability -> deliberate absence
    hits = []
    for d in ("orchestrator", "mcp"):
        for f in (REPO_ROOT / d).rglob("*.py"):
            if re.search(r"class\s+\w*Writ(e|ing)Capability\w*", f.read_text(errors="ignore")):
                hits.append(str(f))
    check("no 'write capability' interface exists anywhere (deliberate non-hook)", not hits, str(hits))


# ── F-V5 ──────────────────────────────────────────────────────────────────────
def fv5() -> None:
    section("F-V5 — Vision scorer seam")
    from scorers import get_scorer

    vision = get_scorer("vision")

    # The seam is no longer wholly unbuilt: D11's RENDERING half is
    # implemented (vl-convert, browser-free), while the VLM judging half is
    # not. F-V5 therefore checks the two halves SEPARATELY -- a single
    # "does it raise" assertion can no longer tell them apart, and would
    # report a half-built feature as unbuilt.
    null_result = vision.score(None, None)
    check("a null chart scores 0.0 rather than crashing the pipeline",
          isinstance(null_result, dict) and null_result.get("score") == 0.0,
          str(null_result)[:70])

    real_spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "bar",
        "data": {"values": [{"k": "A", "v": 3}, {"k": "B", "v": 5}]},
        "encoding": {"x": {"field": "k", "type": "nominal"},
                     "y": {"field": "v", "type": "quantitative"}},
    }
    try:
        vision.score(real_spec, real_spec)
        check("VisionScorer.score() fails on the missing VLM half", False, "did not raise")
    except NotImplementedError as exc:
        # Rendering ran (the message reports PNG byte counts) and the failure
        # is specifically the absent vision transport, not a missing
        # registration and not a missing renderer.
        msg = str(exc)
        check("VisionScorer.score() fails on missing IMPLEMENTATION (not missing registration)", True)
        check("failure is the VLM half specifically, with rendering already done",
              "PNG bytes" in msg or "vision" in msg.lower(), msg[:70])
    except Exception as exc:
        check("VisionScorer.score() fails on missing IMPLEMENTATION", False, f"wrong exception type: {exc!r}")

    from config_loader import flags
    check("evaluation.vision_scorer.enabled defaults to false (shipped default)",
          bool(flags.evaluation.vision_scorer.enabled) is False)


# ── F-V6 ──────────────────────────────────────────────────────────────────────
def fv6() -> None:
    section("F-V6 — Config discipline at freeze")
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "lint-no-literals.sh")], capture_output=True, text=True,
    )
    for line in result.stdout.splitlines()[-6:]:
        print(f"  {line}")
    check("lint-no-literals.sh exits 0", result.returncode == 0)

    # Shipped-default guard. Several bench scripts toggle flags as part of
    # their own control-vs-treatment procedure and some deliberately exit with
    # a flag left flipped (check_c2v8.py leaves memory.enabled=true for its
    # warm pass). That contaminated flags.toml has now been COMMITTED once by
    # accident, silently changing the shipped default a fresh clone would run
    # with. Caught by inspection, not by any check -- so it becomes a check.
    flags_text = (REPO_ROOT / "config" / "flags.toml").read_text()
    shipped = {
        # (section, key): expected shipped default
        ("memory", "enabled"): "false",          # NOTE-1 flip, docs/spec-gaps.md
        ("normalization", "enabled"): "true",    # D13 control path for M3
        ("tenancy", "enforced"): "false",        # D6 single-tenant as shipped
        ("evaluation.vision_scorer", "enabled"): "false",   # D11 stretch, off
    }
    for (toml_section, key), expected in shipped.items():
        pattern = rf"\[{re.escape(toml_section)}\]\s*\n(?:[^\[]*?\n)?\s*{re.escape(key)}\s*=\s*(true|false)"
        m = re.search(pattern, flags_text)
        actual = m.group(1) if m else None
        check(f"shipped default {toml_section}.{key} == {expected}",
              actual == expected,
              f"found {actual!r}" if actual != expected else "")


def main() -> int:
    print("=" * 72)
    print("Phase F — F-V4 / F-V5 / F-V6 (model-independent, structural)")
    print("=" * 72)

    fv4()
    fv5()
    fv6()

    print("\n" + "─" * 20 + " Summary " + "─" * 20)
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
