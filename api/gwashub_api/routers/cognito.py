"""Sign-in with Amazon Cognito (GWASHUB_AUTH=cognito).

The browser is sent to Cognito's hosted pages (password + authenticator app). Cognito redirects
back to /api/auth/callback with a one-time code, which the API exchanges, server to server, for an
ID token. The API checks the token's signature and claims, finds the GWASHub user with that email,
and sets the usual session cookie. Tokens never reach the browser's JavaScript.

Only people already added to GWASHub can sign in: Cognito self sign-up is off, and an admin
invites people with `python -m gwashub_api.admin invite`, which creates both the GWASHub user and
the Cognito account.
"""
import base64
import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from gwashub_api.auth import set_session
from gwashub_api.config import settings
from gwashub_api.db import get_db
from gwashub_api.models import Membership, User

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger(__name__)
STATE_COOKIE = "gh_oauth"
_jwks = {}


def _enabled():
    if settings().auth_mode != "cognito":
        raise HTTPException(404, "Not Found")


def redirect_uri() -> str:
    return f"{settings().public_url}/api/auth/callback"


def logout_url() -> str:
    s = settings()
    return f"{s.cognito_domain}/logout?" + urlencode({"client_id": s.cognito_client_id, "logout_uri": f"{s.public_url}/login"})


def issuer() -> str:
    s = settings()
    region = s.cognito_user_pool_id.split("_", 1)[0]
    return f"https://cognito-idp.{region}.amazonaws.com/{s.cognito_user_pool_id}"


def _state_mac(state: str, verifier: str, expires: int) -> str:
    return hmac.new(settings().secret.encode(), f"oauth.{state}.{verifier}.{expires}".encode(), hashlib.sha256).hexdigest()


@router.get("/login")
def login():
    """Start sign-in: redirect to Cognito with a fresh state and PKCE challenge (kept in a short-lived signed cookie)."""
    _enabled()
    s = settings()
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    expires = int(time.time()) + 600
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = f"{s.cognito_domain}/oauth2/authorize?" + urlencode({
        "response_type": "code", "client_id": s.cognito_client_id, "redirect_uri": redirect_uri(),
        "scope": "openid email profile", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    r = RedirectResponse(url, status_code=302)
    r.set_cookie(STATE_COOKIE, f"{state}.{verifier}.{expires}.{_state_mac(state, verifier, expires)}",
                 httponly=True, secure=True, samesite="lax", max_age=600, path="/api/auth")
    return r


def _to_login(error: str) -> RedirectResponse:
    r = RedirectResponse(f"/login?{urlencode({'error': error})}", status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/api/auth")
    return r


def verify_id_token(token: str) -> dict:
    """Check the ID token's signature (Cognito's published keys), issuer, audience, expiry and use."""
    s = settings()
    if "client" not in _jwks:
        _jwks["client"] = jwt.PyJWKClient(f"{issuer()}/.well-known/jwks.json", cache_keys=True)
    key = _jwks["client"].get_signing_key_from_jwt(token)
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=s.cognito_client_id, issuer=issuer(),
                        options={"require": ["exp", "iat", "sub", "token_use"]})
    if claims.get("token_use") != "id":
        raise jwt.InvalidTokenError("not an ID token")
    return claims


def exchange_code(code: str, verifier: str) -> dict:
    s = settings()
    r = httpx.post(f"{s.cognito_domain}/oauth2/token", timeout=10, auth=(s.cognito_client_id, s.cognito_client_secret),
                   data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
                         "client_id": s.cognito_client_id, "code_verifier": verifier})
    if r.status_code != 200:
        raise RuntimeError(f"token endpoint said {r.status_code}: {r.text[:200]}")
    return r.json()


@router.get("/callback")
def callback(code: str = "", state: str = "", error: str = "", gh_oauth: str | None = Cookie(default=None),
             db: Session = Depends(get_db)):
    _enabled()
    if error:
        return _to_login("cognito")
    parts = (gh_oauth or "").split(".")
    if len(parts) != 4 or not parts[2].isdigit():
        return _to_login("expired")
    c_state, verifier, expires, mac = parts
    if int(expires) < time.time() or not hmac.compare_digest(_state_mac(c_state, verifier, int(expires)), mac) \
            or not hmac.compare_digest(c_state, state) or not code:
        return _to_login("expired")
    try:
        claims = verify_id_token(exchange_code(code, verifier)["id_token"])
    except Exception as e:
        log.warning("Cognito sign-in failed: %s", e)
        return _to_login("failed")
    email = (claims.get("email") or "").strip().lower()
    user = db.scalar(select(User).where(User.cognito_sub == claims["sub"]))
    if not user and email and claims.get("email_verified") in (True, "true"):
        user = db.scalar(select(User).where(User.email == email, User.cognito_sub.is_(None)))
        if user:
            user.cognito_sub = claims["sub"]
            db.commit()
    if not user:
        log.warning("Cognito user %s (%s) has no GWASHub account", claims["sub"], email)
        return _to_login("not_registered")
    projects = [m.project_id for m in db.scalars(select(Membership).where(Membership.user_id == user.id))]
    r = RedirectResponse(f"/projects/{projects[0]}" if len(projects) == 1 else "/projects", status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/api/auth")
    set_session(r, user.id)
    log.info("%s signed in", user.email)
    return r
