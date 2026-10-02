"""
orchestrator/session_api.py
Browser sign-in for the web app (auth.py, "Browser sessions").

  POST   /auth/session {token}   check the token; on success set the HttpOnly session cookie
  GET    /auth/session           who the session belongs to (cookie + X-StateSync, or a bearer header)
  DELETE /auth/session           clear the cookie (sign out)

WHY. The token used to live in localStorage, where any script running in the page (an XSS) could
read it and use it from anywhere, for as long as the token lives. In an HttpOnly cookie the page
can still USE the session but no script can READ it. The cookie is SameSite=Strict (never sent on
a cross-site request), Secure when the API is served over HTTPS, scoped to the API's origin, and
expires after [api] session_cookie_max_age_s. Sign-in attempts are rate-limited per client
address, and every outcome is audited without the token. Signing in and out also require the
X-StateSync header, so another site cannot sign a visitor into a different account or out of
theirs (a custom header forces a CORS preflight that only the configured origins pass).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from auth import CSRF_HEADER, CSRF_VALUE, SESSION_COOKIE, AuthError, Principal, RateLimited, authenticate, read_rate_limiter
from config_loader import cfg


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(cfg.api.session_cookie_max_age_s), path="/",
        httponly=True, samesite="strict", secure=request.url.scheme == "https",
    )


def _require_csrf(request: Request) -> None:
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status_code=403, detail=f"The {CSRF_HEADER} header is required.")


def register_session_routes(app: Any, require_principal: Any, audit: Any) -> None:

    @app.post("/auth/session", tags=["auth"])
    async def sign_in(body: SessionRequest, request: Request, response: Response) -> dict[str, Any]:
        _require_csrf(request)
        client_ip = request.client.host if request.client else "unknown"
        try:
            read_rate_limiter.check(f"signin:{client_ip}")
        except RateLimited as exc:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts.",
                                headers={"Retry-After": str(exc.retry_after)}) from None
        try:
            principal = authenticate(f"Bearer {body.token.strip()}")
        except AuthError as exc:
            await audit("anonymous", cfg.tenancy.default_tenant, "/auth/session", "denied_auth",
                        client_ip=client_ip, detail=str(exc))
            raise HTTPException(status_code=401, detail="That token is not valid.") from None
        _set_cookie(response, request, body.token.strip())
        await audit(principal.principal_id, principal.tenant_id, "/auth/session", "allowed",
                    client_ip=client_ip, detail="auth:sign_in")
        return {"principal_id": principal.principal_id}

    @app.get("/auth/session", tags=["auth"])
    async def whoami(principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        return {"principal_id": principal.principal_id}

    @app.delete("/auth/session", tags=["auth"])
    async def sign_out(request: Request, response: Response) -> dict[str, Any]:
        _require_csrf(request)
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return {"ok": True}
