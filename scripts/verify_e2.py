#!/usr/bin/env python3
"""
scripts/verify_e2.py
Executes the MODEL-INDEPENDENT Phase E2 verification steps.

Covered here (deterministic, no model call, no generation quality involved):
  E2-V2  recall weighting is real            -> delegated to score_specs.py --self-test
  E2-V3  malicious specs rejected            -> orchestrator-side validator, all 10
  E2-V4  CSP forbids unsafe-eval             -> header inspection (the in-browser
                                                render half is run separately; see
                                                docs/phase_e2_log.md)
  E2-V5  no silent repair                    -> unsupported mark yields chart:null,
                                                answer_text preserved, no substitute
  E2-V6  dual validation is independent      -> orchestrator half (frontend half is
                                                driven through window.validateA7Chart)

NOT covered, by design: E2-V1's M7 quality number, which depends on generation
quality. It is now runnable (scripts/run_e2_v1.py, once Task 10 made the
Synthesizer emit real specs) but stays out of THIS script, which is the
model-independent suite -- mixing a model-sensitive number in would make a
deterministic pass/fail suite non-deterministic. The local M7 is provisional;
the graded one comes from the re-baseline (docs/rebaseline_runbook.md).

The frontend validator is deliberately NOT re-implemented here. Checking it
from Python would test a Python copy of it, which is exactly the thing A7's
"two independent validations" rule exists to prevent. It is exercised in the
browser against window.validateA7Chart, recorded in the phase log.
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
from frontend_asset_checks import app_js_is_same_origin       # noqa: E402

MALICIOUS = REPO_ROOT / "fixtures" / "malicious_specs.json"
E2_FIXTURE = REPO_ROOT / "fixtures" / "golden" / "phase_e2.json"
NGINX_CONF = REPO_ROOT / "frontend" / "nginx.conf"
INDEX_HTML = REPO_ROOT / "frontend" / "index.html"
APP_JS = REPO_ROOT / "frontend" / "app.js"

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
    # Audit 005 F-005-1: everything in this function reads frontend/nginx.conf
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

    # The app moved out of index.html into app.js so the CSP could drop
    # 'unsafe-inline' from script-src. These checks follow the code: the
    # property being verified (the render path never reaches the eval-based
    # compiler) is unchanged, only its file changed.
    html = INDEX_HTML.read_text()
    app = APP_JS.read_text()
    both = html + "\n" + app

    check("frontend loads vega-interpreter", "vega-interpreter" in both)
    check("render path requests the AST parser", re.search(r"\bast:\s*true", both) is not None)
    check("render path passes an expression interpreter",
          re.search(r"\bexpr:\s*interp\b", both) is not None)
    check("render refuses to fall back to the eval compiler",
          "refusing to render via the eval-based compiler" in both)

    # Hardening added after the trace-renderer XSS sink was found: script-src
    # no longer permits inline script, so a future DOM-injection bug cannot
    # reach script execution even if a sink is reintroduced.
    check("CSP directive text contains no script-src 'unsafe-inline'",
          "unsafe-inline" not in csp.split("script-src")[1].split(";")[0])
    check("index.html carries no inline <script> body",
          re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html) is None)

    # Audit 004 F-004-2: the previous version of this check, a literal
    # substring match, FAILED on a same-origin app.js carrying a
    # cache-busting query string (56a23bf) and could never have caught an
    # actually CDN-hosted app.js. app_js_is_same_origin asserts the named
    # property in both directions -- see scripts/frontend_asset_checks.py.
    ok, detail = app_js_is_same_origin(html)
    check("app.js is served same-origin, not from a CDN (config assertion)", ok, detail)


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
    rather than reading frontend/nginx.conf as text. The config-text checks
    above passed throughout the period this would have failed, because nginx
    silently drops server-level add_header directives inside any location
    block that declares an add_header of its own -- 56a23bf added
    `location = /index.html` and `location = /app.js` blocks with only
    Cache-Control, and every response since then carried zero of the four
    security headers, on every path. FRONTEND_URL overrides the default
    (http://localhost:3000, the documented dev/compose address).
    """
    section("E2-V4 — CSP forbids unsafe-eval (LIVE response headers — the actual check)")
    for path in ("/", "/index.html", "/app.js"):
        url = FRONTEND_URL.rstrip("/") + path
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
            check(f"{path}: live CSP contains no 'unsafe-eval'",
                  "unsafe-eval" not in headers["content-security-policy"])


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
    print("    E2-V3/V6 frontend half and E2-V4 render half — run in-browser against")
    print("      window.validateA7Chart; results recorded in docs/phase_e2_log.md.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
