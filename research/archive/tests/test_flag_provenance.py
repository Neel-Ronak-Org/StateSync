"""
tests/test_flag_provenance.py
Pins that every provenance record `require_shipped_flags()` returns names its
model, so a report cannot be published without the thing that produced it.

WHY THIS EXISTS. Audit 001 (docs/audits/001.md), F-001-4: `V2_STRATEGY.md` §6
rule 3 states "`flag_provenance` already records provider, per-role models and
flag state. Do not add a report that lacks it." True of the function today --
`_llm_provenance()` was added to `require_shipped_flags()`'s return value at
some point after the multilingual reports (docs/bench_multilingual_report*.json)
were first written, and nobody regenerated them, so three committed artifacts
holding a workstream's baseline numbers carry `flag_state` and no `llm` block.

That is a documentation gap in already-committed reports (fixed by annotating
them directly, see the commit that adds this test). What this test protects
against is the same gap recurring in every REPORT WRITTEN FROM HERE ON: a
harness that calls `require_shipped_flags()` and gets back a record with an
empty or missing `llm` block, and ships it anyway, because nothing asserts the
block is populated.

`_llm_provenance()` parses `config/flags.toml` with two regexes (the active
provider, then that provider's `[llm.models.<provider>]` block). If either
regex fails to match -- a TOML reformat, a renamed section, a provider with no
models block -- the function returns an empty dict silently rather than
raising. That silent-empty behaviour is deliberate (a provenance function
should not crash a benchmark run over a documentation field), which is exactly
why a test has to be the thing that catches it instead.

Run from the repo root: pytest tests/test_flag_provenance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from shipped_flags import _llm_provenance, require_shipped_flags  # noqa: E402


# ── against the real, committed config/flags.toml ────────────────────────────

def test_require_shipped_flags_names_its_model():
    """
    The end-to-end assertion: whatever require_shipped_flags() returns for a
    caller carries a non-empty provider and a non-empty per-role model map.

    check_live=False: this runs in CI, where no orchestrator container exists
    to compare a liveness timestamp against -- the same reason
    scripts/verify_seams.py runs with --no-live there. That flag controls only
    the LIVENESS half (protocol §2's second check); it does not touch the disk
    state or the llm block this test is about.
    """
    record = require_shipped_flags(
        "test_flag_provenance.py", allow_nonshipped=True, check_live=False)
    assert "llm" in record, "provenance record has no 'llm' key at all"
    llm = record["llm"]
    assert llm.get("provider"), (
        f"llm.provider is empty/missing: {llm!r} -- a report built from this "
        f"record cannot name the model that produced it")
    assert llm.get("models"), (
        f"llm.models is empty/missing: {llm!r} -- provider is named but no "
        f"per-role model is, which is the same gap in a different field")


def test_committed_flags_toml_llm_provenance_is_complete():
    """
    Narrower and independent of require_shipped_flags's own plumbing: parse
    the file directly and check every role sql_agent.py actually dispatches on
    is present, not just that the dict is non-empty.
    """
    prov = _llm_provenance()
    assert prov["provider"], "no active [llm] provider resolved from flags.toml"
    expected_roles = {
        "supervisor", "sql", "synthesizer", "memory_summarizer",
        "normalizer", "decomposer", "judge",
    }
    missing = expected_roles - prov["models"].keys()
    assert not missing, f"llm.models is missing role(s): {missing}"
    assert all(prov["models"][r] for r in expected_roles), (
        f"a role resolved to an empty model name: {prov['models']}")


# ── generically, so this is not pinned to "local" ever resolving ─────────────

@pytest.mark.parametrize("provider,models_block,expect_role,expect_model", [
    ("local", '[llm.models.local]\nsql = "qwen2.5:7b"\n', "sql", "qwen2.5:7b"),
    ("anthropic", '[llm.models.anthropic]\nsql = "claude-sonnet-4-5"\n',
     "sql", "claude-sonnet-4-5"),
    ("gemini", '[llm.models.gemini]\nsql = "gemini-3.5-flash-lite"\n',
     "sql", "gemini-3.5-flash-lite"),
])
def test_llm_provenance_resolves_any_configured_provider(
    monkeypatch, tmp_path, provider, models_block, expect_role, expect_model,
):
    """
    _llm_provenance() must not be silently correct only for whichever provider
    happens to be active in the committed file. Each provider's block is
    synthesized and checked in isolation, so a future edit that breaks parsing
    for (say) gemini specifically cannot hide behind "local still passes".
    """
    import shipped_flags as sf

    synthetic = f'[llm]\nprovider = "{provider}"\n\n{models_block}'
    tmp_flags = tmp_path / "flags.toml"
    tmp_flags.write_text(synthetic)
    monkeypatch.setattr(sf, "FLAGS_PATH", tmp_flags)

    prov = sf._llm_provenance()
    assert prov["provider"] == provider
    assert prov["models"].get(expect_role) == expect_model
