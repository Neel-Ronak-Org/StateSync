#!/usr/bin/env python3
"""
scripts/frontend_asset_checks.py
Pure, dependency-free checks on the web app's index.html script tags
(web/index.html in the repo; the built shell as served).

Split out of verify_e2.py (audit 004 F-004-2) so the property can be pinned by
tests/ -- which CI runs without the stack -- rather than living only inside a
script that requires the full stack and that nothing re-runs on a frontend-only
change (the same gap that let M8 go seven-plus commits without a re-run
trigger firing; audit 004's amendment).

The predecessor check was `'<script src="app.js"' in html`, named "app.js is
served same-origin, not from a CDN". It tested neither direction of that name
correctly: it FAILED on a same-origin app.js carrying a harmless cache-busting
query string (`56a23bf`'s `app.js?v=20260820`), and it could never have caught
an actually CDN-hosted app.js, since a literal-PRESENCE substring match says
nothing about the ABSENCE of a second, remote-origin script also named app.js.
This module asserts the real property in both directions.
"""

from __future__ import annotations

import re

_SCRIPT_SRC_RE = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
_SCHEME_OR_PROTOCOL_RELATIVE_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.\-]*:)?//')


def script_srcs(html: str) -> list[str]:
    """Every <script src="..."> attribute value in document order."""
    return _SCRIPT_SRC_RE.findall(html)


def is_same_origin(src: str) -> bool:
    """
    True for a relative reference. False for an absolute URL (a scheme, e.g.
    https://cdn...) or a protocol-relative one (//cdn.example.com/app.js),
    which reaches a remote origin just as surely without stating a scheme.
    """
    return _SCHEME_OR_PROTOCOL_RELATIVE_RE.match(src) is None


# A <script> with no src whose body has any non-space character; an empty
# <script></script> is not a body (the lookahead stops "</script>" counting).
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>\s*(?!</script>)\S", re.IGNORECASE)


def scripts_are_same_origin(html: str) -> tuple[bool, str]:
    """
    The property E2-V4 names, for the web app (docs/V3_PLAN.md Phase 2): the page
    loads at least one script, EVERY script is loaded same-origin (no CDN, no
    protocol-relative URL), and there is no inline script body. The previous
    frontend's CSP permitted cdn.jsdelivr.net for its chart libraries; the web
    app bundles them, so no external script host is acceptable at all.
    Returns (ok, detail).
    """
    srcs = script_srcs(html)
    if not srcs:
        return False, "no <script src> tags found"
    remote = [src for src in srcs if not is_same_origin(src)]
    if remote:
        return False, f"scripts loaded from another origin: {remote}"
    if _INLINE_SCRIPT_RE.search(html):
        return False, "an inline <script> body is present"
    return True, ", ".join(srcs)
