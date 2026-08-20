#!/usr/bin/env python3
"""
scripts/frontend_asset_checks.py
Pure, dependency-free checks on frontend/index.html's script tags.

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


def is_app_js_src(src: str) -> bool:
    """True if this src names app.js, ignoring any query string or fragment."""
    path = src.split("?", 1)[0].split("#", 1)[0]
    return path.rsplit("/", 1)[-1] == "app.js"


def is_same_origin(src: str) -> bool:
    """
    True for a relative reference. False for an absolute URL (a scheme, e.g.
    https://cdn...) or a protocol-relative one (//cdn.example.com/app.js),
    which reaches a remote origin just as surely without stating a scheme.
    """
    return _SCHEME_OR_PROTOCOL_RELATIVE_RE.match(src) is None


def app_js_is_same_origin(html: str) -> tuple[bool, str]:
    """
    The property E2-V4 names: exactly one app.js is loaded, and it is loaded
    same-origin, not from a CDN. Returns (ok, detail). A query string
    (cache-buster) is tolerated; a scheme or protocol-relative src is not,
    regardless of host -- including cdn.jsdelivr.net, which this page's own
    CSP permits for OTHER scripts (vega/vega-lite/vega-embed/vega-interpreter)
    but never for app.js itself.
    """
    app_js_tags = [s for s in script_srcs(html) if is_app_js_src(s)]
    if len(app_js_tags) != 1:
        return False, f"expected exactly one app.js script tag, found {len(app_js_tags)}: {app_js_tags}"
    src = app_js_tags[0]
    if not is_same_origin(src):
        return False, f"app.js loaded from a non-same-origin src: {src!r}"
    return True, src
