"""
tests/test_frontend_asset_checks.py
Pins app_js_is_same_origin (scripts/frontend_asset_checks.py) so the E2-V4
"app.js is served same-origin, not from a CDN" property is asserted by
something CI runs, not only by verify_e2.py -- which needs the full stack and
was not re-run for seven-plus commits after cycle 003's B1 (audit 004 F-004-1,
F-004-2).

WHY THIS EXISTS. The predecessor check, `'<script src="app.js"' in html`,
FAILED on the real, correct file the moment 56a23bf added a cache-busting
query string (`app.js?v=20260820`), and would have PASSED a page carrying an
injected, CDN-hosted second script also named app.js -- it only ever checked
for the literal substring's PRESENCE, never for the real property: exactly one
app.js, and it same-origin. Both directions are pinned below directly against
the real, committed frontend/index.html, so a future edit to that file is
caught here -- by a test with no LLM/MCP/container dependency -- rather than
only by a script nothing runs on a frontend-only commit.

Run from the repo root: pytest tests/test_frontend_asset_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from frontend_asset_checks import (        # noqa: E402
    app_js_is_same_origin,
    is_app_js_src,
    is_same_origin,
    script_srcs,
)

INDEX_HTML = REPO_ROOT / "frontend" / "index.html"


def test_real_committed_index_html_passes():
    """The actual shipped file, cache-buster query string and all."""
    ok, detail = app_js_is_same_origin(INDEX_HTML.read_text())
    assert ok, detail
    assert detail == "app.js?v=20260820"


def test_cache_busting_query_string_is_tolerated():
    html = '<script src="app.js?v=20260820" defer></script>'
    ok, detail = app_js_is_same_origin(html)
    assert ok
    assert detail == "app.js?v=20260820"


def test_bare_relative_app_js_passes():
    html = '<script src="app.js" defer></script>'
    ok, _ = app_js_is_same_origin(html)
    assert ok


def test_cdn_hosted_app_js_fails():
    """The case the predecessor check could never have caught."""
    html = '<script src="https://cdn.jsdelivr.net/npm/statesync-app@1/app.js" defer></script>'
    ok, detail = app_js_is_same_origin(html)
    assert not ok
    assert "cdn.jsdelivr.net" in detail


def test_protocol_relative_app_js_fails():
    """No scheme, but still a remote origin -- the same-origin check must
    not be fooled by the absence of an explicit https:// prefix."""
    html = '<script src="//cdn.jsdelivr.net/app.js" defer></script>'
    ok, _ = app_js_is_same_origin(html)
    assert not ok


def test_injected_second_app_js_fails_even_with_a_legit_tag_present():
    """
    The predecessor check's real blind spot: it asserted PRESENCE of a
    substring, never EXCLUSIVITY. A page with the legitimate same-origin tag
    AND an injected CDN-hosted second script also named app.js passed the
    old check outright -- it never looked past the first match.
    """
    html = (
        '<script src="app.js" defer></script>\n'
        '<script src="https://evil.cdn.example/app.js"></script>'
    )
    old_check_would_pass = '<script src="app.js"' in html
    assert old_check_would_pass, "sanity: the predecessor substring is indeed present"

    ok, detail = app_js_is_same_origin(html)
    assert not ok, "the new check must reject the injected copy the old one missed"
    assert "found 2" in detail


def test_missing_app_js_fails():
    html = '<script src="vega.min.js"></script>'
    ok, detail = app_js_is_same_origin(html)
    assert not ok
    assert "found 0" in detail


@pytest.mark.parametrize("src,expected", [
    ("app.js", True),
    ("app.js?v=20260820", True),
    ("app.js#frag", True),
    ("/static/app.js", True),
    ("vega.min.js", False),
    ("app.js.map", False),
])
def test_is_app_js_src(src, expected):
    assert is_app_js_src(src) is expected


@pytest.mark.parametrize("src,expected", [
    ("app.js", True),
    ("app.js?v=1", True),
    ("/static/app.js", True),
    ("https://cdn.jsdelivr.net/app.js", False),
    ("//cdn.jsdelivr.net/app.js", False),
    ("http://cdn.jsdelivr.net/app.js", False),
])
def test_is_same_origin(src, expected):
    assert is_same_origin(src) is expected


def test_script_srcs_extracts_all_tags_in_order():
    html = (
        '<script src="a.js"></script>\n'
        '<script defer src="b.js" type="module"></script>\n'
        "<script>inline()</script>\n"
    )
    assert script_srcs(html) == ["a.js", "b.js"]
