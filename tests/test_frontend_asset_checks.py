"""
tests/test_frontend_asset_checks.py
Pins the property E2-V4 names -- the page's scripts come only from its own
origin, with no inline script -- against the web app's index.html
(docs/V3_PLAN.md Phase 2).

WHY THIS EXISTS. The CSP's script-src is 'self' only, which is what makes an
HTML-injection bug unable to reach script execution. That guarantee is only as
good as the page itself: a CDN <script> tag, a protocol-relative URL or an
inline <script> body would either break under the CSP or, worse, be "fixed" by
loosening it. The previous frontend loaded its chart libraries from
cdn.jsdelivr.net and kept that host in its CSP; the web app bundles them, so
no external script host is acceptable at all. These checks run in CI with no
stack (audit 004 F-004-2: a property only a live script checks is a property
nothing re-checks on a frontend-only change).

Run from the repo root: pytest tests/test_frontend_asset_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from frontend_asset_checks import is_same_origin, script_srcs, scripts_are_same_origin  # noqa: E402

INDEX_HTML = REPO_ROOT / "web" / "index.html"


def test_real_committed_index_html_passes():
    ok, detail = scripts_are_same_origin(INDEX_HTML.read_text())
    assert ok, detail
    assert "/config.js" in detail           # runtime config: a same-origin file, not inline


def test_a_cdn_script_fails():
    html = '<script src="/config.js"></script><script src="https://cdn.jsdelivr.net/npm/vega@5"></script>'
    ok, detail = scripts_are_same_origin(html)
    assert not ok and "cdn.jsdelivr.net" in detail


def test_a_protocol_relative_script_fails():
    ok, _ = scripts_are_same_origin('<script src="//evil.example/app.js"></script>')
    assert not ok


def test_an_inline_script_body_fails():
    html = '<script src="/config.js"></script><script>window.x = 1</script>'
    ok, detail = scripts_are_same_origin(html)
    assert not ok and "inline" in detail


def test_an_empty_inline_script_tag_is_not_a_body():
    html = '<script src="/config.js"></script><script></script>'
    assert scripts_are_same_origin(html)[0]


def test_a_page_with_no_scripts_fails():
    assert not scripts_are_same_origin("<html><body></body></html>")[0]


@pytest.mark.parametrize("src, expected", [
    ("/config.js", True),
    ("/assets/index-BLCelgPL.js", True),
    ("config.js?v=2", True),
    ("https://cdn.example.com/x.js", False),
    ("//cdn.example.com/x.js", False),
    ("HTTP://cdn.example.com/x.js", False),
])
def test_is_same_origin(src, expected):
    assert is_same_origin(src) is expected


def test_script_srcs_extracts_all_tags_in_order():
    html = '<script type="module" src="/a.js"></script><SCRIPT SRC=\'/b.js\'></SCRIPT>'
    assert script_srcs(html) == ["/a.js", "/b.js"]
