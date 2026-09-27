#!/usr/bin/env python3
"""
scripts/nginx_header_checks.py
Pure, dependency-free check on an nginx config's TEXT (web/nginx.conf): the inheritance
invariant that 56a23bf broke and that no check in this project noticed
(docs/audits/005.md F-005-1).

THE RULE THIS PINS. nginx does not inherit a server-level `add_header` into a
`location` block that declares any `add_header` of its own -- inheritance is
all-or-nothing per response, decided per location, not per header. `56a23bf`
added `location = /index.html` and `location = /app.js` blocks whose only
`add_header` was `Cache-Control`, which silently discarded the four
server-level security headers (CSP, X-Frame-Options, X-Content-Type-Options,
Referrer-Policy) for exactly the two paths that matter, on every response,
since that commit. `scripts/verify_e2.py`'s E2-V4 checks read this same file
as text and passed throughout, because the CSP *directive* was and is spelled
correctly -- the checks were never looking at inheritance, only at content.

This module checks inheritance directly: for every `location` block in the
file, if it declares ANY `add_header`, it must declare all four required
security headers too -- because if it declares any, it gets none of the
server-level ones for free. A location with NO `add_header` of its own is
fine as-is; it inherits normally.

This does not replace `verify_e2.py`'s live-response check
(`e2_v4_csp_live`), which is the actual property that matters and is
verified against a real request. This module pins the mechanism so a future
location block added the same way `56a23bf`'s were is caught by a test with
no server/container dependency, the same reasoning
`scripts/frontend_asset_checks.py` documents for its own check.
"""

from __future__ import annotations

import re

REQUIRED_SECURITY_HEADERS = [
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
]

_LOCATION_RE = re.compile(r'location\s+([^\{]+?)\s*\{', re.IGNORECASE)
_ADD_HEADER_RE = re.compile(r'add_header\s+([A-Za-z0-9\-]+)\b', re.IGNORECASE)


def _extract_location_blocks(conf_text: str) -> list[tuple[str, str]]:
    """[(location_pattern, block_body)] for every top-level `location` block,
    matched by brace-depth counting (no nested location blocks exist in this
    file, so a simple counter is sufficient -- not a general nginx parser)."""
    blocks: list[tuple[str, str]] = []
    # Strip comments first: prose mentioning "location ... {" in a comment
    # (web/nginx.conf explains its location rules at length) was being parsed
    # as a block. No directive in these files contains a literal '#'.
    conf_text = re.sub(r"#[^\n]*", "", conf_text)
    for m in _LOCATION_RE.finditer(conf_text):
        pattern = m.group(1).strip()
        start = m.end()
        depth = 1
        i = start
        while i < len(conf_text) and depth > 0:
            if conf_text[i] == "{":
                depth += 1
            elif conf_text[i] == "}":
                depth -= 1
            i += 1
        blocks.append((pattern, conf_text[start:i - 1]))
    return blocks


def locations_missing_security_headers(conf_text: str) -> dict[str, list[str]]:
    """
    {location_pattern: [missing_header_names]} for every location block that
    declares at least one add_header (and therefore inherits NONE of the
    server-level ones) but is missing one or more of the four required
    security headers. A location declaring zero add_header directives is
    absent from the result -- it inherits normally and needs no repeats.
    """
    violations: dict[str, list[str]] = {}
    for pattern, body in _extract_location_blocks(conf_text):
        declared = {h.lower() for h in _ADD_HEADER_RE.findall(body)}
        if not declared:
            continue  # inherits server-level headers normally
        missing = [h for h in REQUIRED_SECURITY_HEADERS if h.lower() not in declared]
        if missing:
            violations[pattern] = missing
    return violations


def locations_declaring_add_header(conf_text: str) -> list[str]:
    """
    Location patterns that declare any add_header of their own. The web app's
    nginx.conf (web/nginx.conf) keeps this list EMPTY by design -- security
    headers only at server level, caching via `expires` -- so the inheritance
    trap above cannot be re-entered by adding a location.
    """
    return [pattern for pattern, body in _extract_location_blocks(conf_text) if _ADD_HEADER_RE.search(body)]
