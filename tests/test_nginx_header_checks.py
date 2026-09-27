"""
tests/test_nginx_header_checks.py
Pins the nginx add_header inheritance invariant that `56a23bf` broke
(docs/audits/005.md F-005-1): a `location` block that declares any
`add_header` of its own inherits NONE of the server-level ones, so it must
repeat all four security headers or it silently drops them.

WHY THIS EXISTS. `scripts/verify_e2.py`'s E2-V4 checks read
`frontend/nginx.conf` as text and asserted the CSP *directive* was spelled
correctly. It was, and still is -- the bug was never in what the directive
says, only in whether nginx delivers it, which depends on inheritance, not
content. Those checks passed at every commit from `56a23bf` onward while the
live server sent zero of four security headers on every path. This test pins
the mechanism directly against the two conf files that bracket the bug, with
no server, no container, no network call -- so a future location block added
the same way is caught here rather than only by a live check nothing runs on
a frontend-only commit (the exact gap audit 004 F-004-1/F-004-2 named).

Run from the repo root: pytest tests/test_nginx_header_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from nginx_header_checks import (      # noqa: E402
    REQUIRED_SECURITY_HEADERS,
    locations_declaring_add_header,
    locations_missing_security_headers,
)

# The web app's config (docs/V3_PLAN.md Phase 2). The fixtures below are the
# PREVIOUS frontend's confs, kept verbatim: they pin the mechanism, which does
# not depend on which app is served.
NGINX_CONF = REPO_ROOT / "web" / "nginx.conf"

# The exact `frontend/nginx.conf` committed at 56a23bf -- the commit that
# introduced `location = /index.html` / `location = /app.js` with only
# Cache-Control, breaking the inheritance of the four server-level security
# headers for both files. Kept verbatim (not re-derived from git at test
# time) so this test does not depend on git history remaining reachable.
PRE_FIX_56A23BF = """\
server {
    listen 80;
    server_name _;

    add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com; style-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; connect-src 'self' http://localhost:8090 http://orchestrator:8090; img-src 'self' data:; object-src 'none'; frame-ancestors 'none';" always;

    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;

    root /usr/share/nginx/html;
    index index.html;

    location = /index.html {
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }
    location = /app.js {
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /nginx-health {
        return 200 'ok';
        add_header Content-Type text/plain;
    }

    gzip on;
    gzip_types text/html text/css application/javascript application/json;
    gzip_min_length 1024;
}
"""

# The conf as it stood immediately before 56a23bf: no location blocks declare
# add_header at all, so every response inherits the server-level security
# headers normally. Included so the test suite also pins the KNOWN-GOOD case,
# not only the known-bad one.
PRE_56A23BF_PARENT = """\
server {
    listen 80;
    server_name _;

    add_header Content-Security-Policy "default-src 'self'; object-src 'none'; frame-ancestors 'none';" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;

    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    gzip on;
}
"""


def test_56a23bf_is_caught():
    """The exact commit that broke this, reproduced as a fixture string."""
    violations = locations_missing_security_headers(PRE_FIX_56A23BF)
    assert "= /index.html" in violations
    assert "= /app.js" in violations
    assert "/nginx-health" in violations
    for pattern in ("= /index.html", "= /app.js", "/nginx-health"):
        assert set(violations[pattern]) == set(REQUIRED_SECURITY_HEADERS), (
            f"{pattern}: expected all four headers reported missing, got {violations[pattern]}"
        )


def test_location_with_no_add_header_is_not_flagged():
    """A location declaring no add_header of its own inherits normally."""
    violations = locations_missing_security_headers(PRE_56A23BF_PARENT)
    assert violations == {}


def test_partial_repeat_still_flagged():
    """Repeating SOME but not all four is still a violation -- the missing
    ones are silently dropped exactly as if none had been repeated."""
    conf = """\
server {
    add_header Content-Security-Policy "default-src 'self';" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;

    location = /partial.html {
        add_header Content-Security-Policy "default-src 'self';" always;
        add_header Cache-Control "no-cache" always;
        try_files $uri =404;
    }
}
"""
    violations = locations_missing_security_headers(conf)
    assert list(violations.keys()) == ["= /partial.html"]
    assert set(violations["= /partial.html"]) == {
        "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy",
    }


def test_real_committed_nginx_conf_passes():
    """The actual shipped file."""
    violations = locations_missing_security_headers(NGINX_CONF.read_text())
    assert violations == {}, violations


def test_web_conf_keeps_every_add_header_at_server_level():
    """The web app's design rule: no location declares add_header (caching uses
    `expires`), so a new location cannot re-enter the inheritance trap."""
    assert locations_declaring_add_header(NGINX_CONF.read_text()) == []


def test_web_conf_csp_is_strict():
    conf = NGINX_CONF.read_text()
    csp_lines = [ln.strip() for ln in conf.splitlines()
                 if ln.strip().startswith("add_header Content-Security-Policy")]
    assert len(csp_lines) == 1, csp_lines
    csp = csp_lines[0]
    directives = {
        part.strip().split()[0]: part.strip().split()[1:]
        for part in csp.split('"')[1].split(";") if part.strip()
    }
    assert "unsafe-eval" not in csp
    assert directives["script-src"] == ["'self'"]          # no CDN, no inline, no eval
    assert directives["object-src"] == ["'none'"]
    assert directives["base-uri"] == ["'none'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert directives["font-src"] == ["'self'"]            # fonts are self-hosted
    # entrypoint.sh fills this in from ORCHESTRATOR_URL at start-up
    assert directives["connect-src"] == ["'self'", "__ORCHESTRATOR_ORIGIN__"]
    for name in REQUIRED_SECURITY_HEADERS[1:]:
        assert f"add_header {name}" in conf


def test_header_name_matching_is_case_insensitive():
    conf = """\
server {
    location = /x {
        add_header content-security-policy "default-src 'self';" always;
        add_header x-frame-options DENY always;
        add_header x-content-type-options nosniff always;
        add_header referrer-policy no-referrer always;
        add_header Cache-Control "no-cache" always;
    }
}
"""
    assert locations_missing_security_headers(conf) == {}
