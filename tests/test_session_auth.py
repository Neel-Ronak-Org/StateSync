"""
tests/test_session_auth.py

WHY THIS EXISTS (orchestrator/session_api.py, auth.py "Browser sessions"; 2026-10-02). The web app
kept the API token in localStorage, readable by any script in the page. It now holds an HttpOnly,
SameSite=Strict cookie instead. Pinned here:
- a bearer header still works (scripts) and wins over a cookie;
- a cookie counts only with the X-StateSync header (CSRF guard);
- sign-in sets an HttpOnly, SameSite=Strict cookie and never echoes the token; a bad token is 401;
- whoami works from the cookie + header, not from the cookie alone; sign-out clears the cookie.
"""


import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import auth  # noqa: E402

TOKEN = "t" * 43


def test_bearer_wins_and_a_cookie_needs_the_csrf_header():
    assert auth.credential_header("Bearer abc", TOKEN, None) == "Bearer abc"
    assert auth.credential_header(None, TOKEN, "1") == f"Bearer {TOKEN}"
    with pytest.raises(auth.AuthError):
        auth.credential_header(None, TOKEN, None)
    with pytest.raises(auth.AuthError):
        auth.credential_header(None, TOKEN, "yes")
    assert auth.credential_header(None, None, "1") is None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.testclient import TestClient

    import session_api
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps([{"principal_id": "p1", "tenant_id": "t1",
                                       "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()}]))
    monkeypatch.setenv(auth.PRINCIPALS_FILE_ENV, str(principals))
    auth.init_auth()
    audit_log = []

    async def audit(*args, **kwargs):
        audit_log.append((args, kwargs))

    async def require_principal(request: Request):
        try:
            return auth.authenticate(auth.credential_header(request.headers.get("Authorization"),
                                                            request.cookies.get(auth.SESSION_COOKIE),
                                                            request.headers.get(auth.CSRF_HEADER)))
        except auth.AuthError:
            raise HTTPException(status_code=401) from None

    app = FastAPI()
    session_api.register_session_routes(app, require_principal=require_principal, audit=audit)
    with TestClient(app) as c:
        c.audit_log = audit_log
        yield c


def test_sign_in_and_out_require_the_csrf_header(client):
    # Without it another site could sign a visitor into a different account, or out.
    assert client.post("/auth/session", json={"token": TOKEN}).status_code == 403
    assert client.delete("/auth/session").status_code == 403


def test_sign_in_sets_an_httponly_strict_cookie_and_never_echoes_the_token(client):
    bad = client.post("/auth/session", json={"token": "nope"}, headers={auth.CSRF_HEADER: "1"})
    assert bad.status_code == 401 and auth.SESSION_COOKIE not in bad.cookies
    ok = client.post("/auth/session", json={"token": TOKEN}, headers={auth.CSRF_HEADER: "1"})
    assert ok.status_code == 200 and ok.json() == {"principal_id": "p1"}
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=" in cookie
    assert TOKEN not in ok.text
    assert all(TOKEN not in json.dumps(kwargs) + json.dumps(args) for args, kwargs in client.audit_log)


def test_whoami_needs_the_csrf_header_with_the_cookie_and_sign_out_clears_it(client):
    client.post("/auth/session", json={"token": TOKEN}, headers={auth.CSRF_HEADER: "1"})
    assert client.get("/auth/session").status_code == 401                       # cookie alone
    assert client.get("/auth/session", headers={auth.CSRF_HEADER: "1"}).json() == {"principal_id": "p1"}
    out = client.delete("/auth/session", headers={auth.CSRF_HEADER: "1"})
    assert out.status_code == 200 and 'max-age=0' in out.headers["set-cookie"].lower()
    assert client.get("/auth/session", headers={auth.CSRF_HEADER: "1"}).status_code == 401
