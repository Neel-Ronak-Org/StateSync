#!/usr/bin/env python3
"""
scripts/verify_e2.py
Executes the MODEL-INDEPENDENT Phase E2 verification steps.

Covered here (deterministic, no model call, no generation quality involved):
  E2-V2  recall weighting is real            -> delegated to score_specs.py --self-test
  E2-V3  malicious specs rejected            -> orchestrator-side validator, all 10
  E2-V4  CSP forbids unsafe-eval             -> header inspection (the render half,
                                                a real chart drawn under the served
                                                CSP, is web/e2e/live)
  E2-V5  no silent repair                    -> unsupported mark yields chart:null,
                                                answer_text preserved, no substitute
  E2-V6  dual validation is independent      -> orchestrator half (the browser half
                                                is web/e2e/mocked/chart-safety.spec.ts)

NOT covered, by design: E2-V1's M7 quality number, which depends on generation
quality. It is now runnable (scripts/run_e2_v1.py, once Task 10 made the
Synthesizer emit real specs) but stays out of THIS script, which is the
model-independent suite -- mixing a model-sensitive number in would make a
deterministic pass/fail suite non-deterministic. The local M7 is provisional;
the graded one comes from the re-baseline (docs/rebaseline_runbook.md).

The browser validator (web/src/charts/a7.ts) is deliberately NOT re-implemented
here. Checking it from Python would test a Python copy of it, which is exactly
the thing A7's "two independent validations" rule exists to prevent. It is
exercised in a real browser by web/e2e/mocked/chart-safety.spec.ts, which feeds
it the same fixtures/malicious_specs.json through the app's render path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from schemas.synthesizer_output import (                      # noqa: E402
    validate_synthesizer_output,
    safe_synthesizer_output,
)
from frontend_asset_checks import scripts_are_same_origin     # noqa: E402

MALICIOUS = REPO_ROOT / "fixtures" / "malicious_specs.json"
E2_FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_e2.json"
# The web app (docs/V3_PLAN.md Phase 2) replaced frontend/; the E2-V4
# properties are unchanged and are checked against its files.
NGINX_CONF = REPO_ROOT / "web" / "nginx.conf"
INDEX_HTML = REPO_ROOT / "web" / "index.html"
CHART_RENDERER = REPO_ROOT / "web" / "src" / "charts" / "VegaChart.tsx"
WEB_SRC = REPO_ROOT / "web" / "src"

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# Audit 005 F-005-1: these are the four headers e2_v4_csp's config-text checks
# describe. Asserted here against a LIVE response, because nginx's add_header
# inheritance rule silently dropped all four for seven months of commits while
# every existing check kept reading the config file, where they were (and are)
# correctly spelled.
LIVE_SECURITY_HEADERS = [
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
]

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


def load_malicious() -> list[dict]:
    """
    Loads the 10 malicious specs, applying ms_09's synthetic_data_size — the
    fixture stores one row plus an instruction to inflate it, because a literal
    5001-row array in a tracked fixture is unreviewable.
    """
    specs = json.loads(MALICIOUS.read_text())["specs"]
    for entry in specs:
        size = entry.get("synthetic_data_size")
        if size:
            template = entry["spec"]["data"]["values"][0]
            entry["spec"]["data"]["values"] = [dict(template) for _ in range(size)]
    return specs


# ── E2-V3 ─────────────────────────────────────────────────────────────────────
def e2_v3() -> None:
    section("E2-V3 — Malicious specs rejected (orchestrator validator)")
    specs = load_malicious()
    check("fixture holds 10 malicious specs", len(specs) == 10, f"{len(specs)} found")

    rejected = 0
    for entry in specs:
        payload = {"answer_text": "x", "chart": entry["spec"], "citations": []}
        ok, err = validate_synthesizer_output(payload)
        if not ok:
            rejected += 1
            reason = (err or "").split(";")[0][:88]
            print(f"      {entry['id']} rejected: {reason}")
        else:
            print(f"      {entry['id']} ACCEPTED — {entry['violation']}")
    check("10/10 rejected by the orchestrator validator", rejected == 10, f"{rejected}/10")

    # E2-V3's worked example must report all THREE independent grounds, not one
    # generic rejection — "a single generic rejection means the other two paths
    # are untested".
    worked = {
        "mark": {"type": "bar", "tooltip": {"signal": "utcFormat(now(),'%Y')"}},
        "data": {"url": "https://attacker.example/x.json"},
        "encoding": {"x": {"field": "a", "type": "nominal",
                           "value": {"expr": "window.location.href"}}},
    }
    ok, err = validate_synthesizer_output({"answer_text": "x", "chart": worked, "citations": []})
    check("worked example rejected", not ok)
    grounds = [g.strip() for g in (err or "").split(";")]
    for g in grounds:
        print(f"      ground: {g[:100]}")
    # Each ground must be its own reported reason anchored at its own path —
    # not one generic oneOf failure that merely embeds the offending spec.
    check("ground 1 reported at chart/mark", any(g.startswith("[chart/mark]") for g in grounds))
    check("ground 2 reported at chart/data", any(g.startswith("[chart/data]") for g in grounds))
    check("ground 3 reported at chart/encoding/x",
          any(g.startswith("[chart/encoding/x]") for g in grounds))
    check("three distinct grounds, not one generic rejection", len(grounds) >= 3, f"{len(grounds)} grounds")


# ── E2-V4 (header half) ───────────────────────────────────────────────────────
def e2_v4_csp() -> None:
    # Audit 005 F-005-1: everything in this function reads the nginx config
    # as TEXT. It asserts what the config file SAYS, not what a browser
    # RECEIVES -- those diverged silently from 56a23bf until this cycle,
    # because nginx does not inherit add_header into a location block that
    # declares one of its own, and none of these checks can see that. The
    # property this function's name promises ("CSP forbids unsafe-eval") is
    # only actually verified by e2_v4_csp_live(), below, which issues a real
    # request and reads a real response.
    section("E2-V4 — nginx.conf TEXT contains the intended CSP (config assertion, not a live check)")
    conf = NGINX_CONF.read_text()
    csp_lines = [ln for ln in conf.splitlines() if "Content-Security-Policy" in ln]
    # Audit 005 A1: the directive is now repeated in every `location` block
    # that declares its own add_header (server-level plus one per location
    # that needs it -- see scripts/nginx_header_checks.py for why), so "the
    # config states one CSP" is now "every repeat states the SAME CSP",
    # not "there is exactly one line".
    check("at least one CSP header directive IN THE CONFIG FILE", len(csp_lines) >= 1, f"{len(csp_lines)} found")
    if not csp_lines:
        return
    stripped = [ln.strip() for ln in csp_lines]
    check("every repeated CSP directive is identical (ignoring indentation)", len(set(stripped)) == 1,
          f"{len(set(stripped))} distinct variants across {len(csp_lines)} lines")
    csp = stripped[0]
    check("CSP directive text is a single line", "\n" not in csp)
    check("CSP directive text contains no 'unsafe-eval'", "unsafe-eval" not in csp)
    check("CSP directive text sets object-src 'none'", "object-src 'none'" in csp)
    check("CSP directive text sets frame-ancestors 'none'", "frame-ancestors 'none'" in csp)

    # The render path. The web app has exactly one chart renderer, and it
    # imports vega-interpreter statically -- there is no runtime probing, so
    # there is no path on which the eval-based compiler could be reached.
    renderer = CHART_RENDERER.read_text()
    check("renderer imports the expression interpreter statically",
          re.search(r"import\s*\{\s*expressionInterpreter\s*\}\s*from\s*'vega-interpreter'", renderer) is not None)
    check("render path requests the AST parser", re.search(r"\bast:\s*true", renderer) is not None)
    check("render path passes the expression interpreter",
          re.search(r"\bexpr:\s*expressionInterpreter\b", renderer) is not None)
    others = sorted(
        str(f.relative_to(REPO_ROOT)) for f in WEB_SRC.rglob("*.ts*")
        if f != CHART_RENDERER and re.search(r"from\s*'vega(-embed|-lite)?'", f.read_text())
    )
    check("no other module imports vega / vega-embed / vega-lite", not others, str(others))

    # script-src permits no inline script, so a future DOM-injection bug cannot
    # reach script execution even if a sink is reintroduced.
    check("CSP directive text contains no script-src 'unsafe-inline'",
          "unsafe-inline" not in csp.split("script-src")[1].split(";")[0])
    ok, detail = scripts_are_same_origin(INDEX_HTML.read_text())
    check("every script the page loads is same-origin, none inline (config assertion)", ok, detail)


def _fetch_response_headers(url: str, timeout: float = 5.0) -> dict[str, str] | None:
    """Lower-cased response header map for a live GET, or None if unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return {k.lower(): v for k, v in resp.getheaders()}
    except (urllib.error.URLError, OSError):
        return None


def e2_v4_csp_live() -> None:
    """
    Audit 005 F-005-1. This is the check e2_v4_csp() is not: it issues a real
    HTTP request to the running frontend and reads the actual response headers,
    rather than reading the nginx config as text. The config-text checks
    above passed throughout the period this would have failed, because nginx
    silently drops server-level add_header directives inside any location
    block that declares an add_header of its own -- 56a23bf added
    `location = /index.html` and `location = /app.js` blocks with only
    Cache-Control, and every response since then carried zero of the four
    security headers, on every path. FRONTEND_URL overrides the default
    (http://localhost:3000, the documented dev/compose address).
    """
    section("E2-V4 — CSP forbids unsafe-eval (LIVE response headers — the actual check)")
    base = FRONTEND_URL.rstrip("/")
    paths = ["/", "/index.html", "/config.js"]
    try:
        with urllib.request.urlopen(base + "/", timeout=5.0) as resp:
            shell = resp.read().decode("utf-8", "replace")
        asset = re.search(r'/assets/[A-Za-z0-9_.-]+\.js', shell)
        if asset:
            paths.append(asset.group(0))
        ok, detail = scripts_are_same_origin(shell)
        check("served shell: every script same-origin, none inline", ok, detail)
    except (urllib.error.URLError, OSError):
        pass
    for path in paths:
        url = base + path
        headers = _fetch_response_headers(url)
        if headers is None:
            check(f"frontend reachable at {path}", False,
                  f"could not GET {url} — is `docker compose up -d` running?")
            continue
        for name in LIVE_SECURITY_HEADERS:
            check(f"{path}: live response carries {name}", name in headers,
                  headers.get(name, "ABSENT")[:100])
        # The one property config-text can genuinely stand in for: whether the
        # DIRECTIVE the file declares is what the browser actually got, once
        # we already know a header of that name arrived at all.
        if "content-security-policy" in headers:
            csp_live = headers["content-security-policy"]
            check(f"{path}: live CSP contains no 'unsafe-eval'", "unsafe-eval" not in csp_live)
            check(f"{path}: live CSP script-src is 'self' only",
                  "script-src 'self';" in csp_live, csp_live[:120])
            check(f"{path}: live CSP connect-src was filled in at start-up",
                  "__ORCHESTRATOR_ORIGIN__" not in csp_live)


# ── E2-V5 ─────────────────────────────────────────────────────────────────────
def e2_v5() -> None:
    section("E2-V5 — No silent repair")
    bad = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": "geoshape",                      # outside the A7 enum
        "data": {"values": [{"x": 1, "y": 2}]},
        "encoding": {"x": {"field": "x", "type": "quantitative"}},
    }
    answer = "Thirty sellers breached their contracted SLA last quarter."
    citations = [{"kind": "sql", "ref": "SELECT 1"}]
    out = safe_synthesizer_output({"answer_text": answer, "chart": bad, "citations": citations})

    check("chart is null after validation failure", out["chart"] is None)
    check("answer_text preserved verbatim", out["answer_text"] == answer)
    check("citations preserved", out["citations"] == citations)
    check("no substituted or corrected chart returned",
          out["chart"] is None and "mark" not in json.dumps(out["chart"]))
    ok, err = validate_synthesizer_output({"answer_text": answer, "chart": bad, "citations": citations})
    check("a validation-failure reason is produced for logging", (not ok) and bool(err),
          (err or "")[:70])


# ── E2-V6 (orchestrator half) ─────────────────────────────────────────────────
def e2_v6_orchestrator() -> None:
    section("E2-V6 — Dual validation independent (orchestrator half)")
    # With the frontend removed from the picture entirely, the orchestrator
    # must still reject every malicious spec on its own.
    specs = load_malicious()
    rejected = sum(
        1 for e in specs
        if not validate_synthesizer_output(
            {"answer_text": "x", "chart": e["spec"], "citations": []})[0]
    )
    check("orchestrator alone rejects all 10 (frontend not involved)", rejected == 10, f"{rejected}/10")

    valid = json.loads(E2_FIXTURE.read_text())["queries"][0]["reference_spec"]
    ok, _ = validate_synthesizer_output({"answer_text": "x", "chart": valid, "citations": []})
    check("a valid chart still passes the orchestrator validator", ok)


# ── Reference-spec sanity (supports E2-V4's render half) ──────────────────────
def reference_specs_valid() -> None:
    section("Reference specs (phase_e2.json) are A7-conformant")
    queries = json.loads(E2_FIXTURE.read_text())["queries"]
    check("30 chart-warranting queries present", len(queries) == 30, f"{len(queries)} found")
    invalid = [
        q["id"] for q in queries
        if not validate_synthesizer_output(
            {"answer_text": "x", "chart": q["reference_spec"], "citations": []})[0]
    ]
    check("all reference specs validate under A7", not invalid, f"invalid: {invalid}")
    marks = sorted({q["reference_spec"]["mark"] for q in queries})
    check("every A7 mark in the enum is exercised", len(marks) == 7, f"{marks}")


# ── E2-V2 (delegated) ─────────────────────────────────────────────────────────
def e2_v2() -> None:
    section("E2-V2 — Recall weighting is real (via score_specs.py)")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "score_specs.py"), "--self-test"],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        print(f"      {line}")
    check("score_specs.py --self-test exits 0", result.returncode == 0)


def main() -> int:
    print("=" * 72)
    print("Phase E2 — model-independent verification")
    print("=" * 72)

    reference_specs_valid()
    e2_v2()
    e2_v3()
    e2_v4_csp()
    e2_v4_csp_live()
    e2_v5()
    e2_v6_orchestrator()

    print("\n" + "─" * 20 + " Summary " + "─" * 20)
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    print("\n  NOT RUN HERE (by design):")
    print("    E2-V1 M7 quality score — model-dependent. Run it with")
    print("      python3 scripts/run_e2_v1.py --out <path> && "
          "python3 scripts/score_specs.py --score <path>")
    print("    The local number is provisional; the graded one comes from the")
    print("    re-baseline (docs/rebaseline_runbook.md).")
    print("    E2-V3/V6 browser half — the same 10 specs through the web app's validator:")
    print("      cd web && npx playwright test e2e/mocked/chart-safety.spec.ts")
    print("    E2-V4 render half — a real chart drawn under the served CSP:")
    print("      cd web && STATESYNC_LIVE=1 npx playwright test")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
