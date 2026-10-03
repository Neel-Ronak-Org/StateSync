"""
tests/test_cors_methods.py
Every HTTP method the orchestrator routes use is allowed by its CORS policy.

WHY THIS EXISTS. The web app runs on another origin (localhost:3000 against
the API on 8090), so the browser sends a CORS preflight before any PUT, PATCH
or DELETE. A method missing from CORSMiddleware's allow_methods is refused
there (400 "Disallowed CORS method") and the call never happens -- but only
in a real browser: the server-side tests and the mocked Playwright suite never
send a preflight. That is how saving a dashboard layout (PUT, Phase 2b) shipped
broken while every test passed; found in Phase 3 when the workspace routes
added more PUTs. This test reads the route decorators and the CORS list from
the source, so a new route method cannot be forgotten again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parent.parent / "orchestrator"
ROUTE = re.compile(r"@app\.(get|post|put|patch|delete)\(")


def _allowed_methods() -> set[str]:
    tree = ast.parse((ORCHESTRATOR / "main.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "allow_methods":
            return {elt.value for elt in node.value.elts}  # type: ignore[attr-defined]
    raise AssertionError("CORSMiddleware allow_methods not found in orchestrator/main.py")


def test_every_route_method_passes_the_preflight():
    used: dict[str, set[str]] = {}
    for path in ORCHESTRATOR.glob("*.py"):
        for method in ROUTE.findall(path.read_text()):
            used.setdefault(method.upper(), set()).add(path.name)
    assert {"GET", "POST", "PUT", "PATCH", "DELETE"} <= set(used), f"route scan found only {sorted(used)}"
    missing = {m: sorted(files) for m, files in used.items() if m not in _allowed_methods()}
    assert not missing, f"methods used by routes but refused by the CORS preflight: {missing}"
