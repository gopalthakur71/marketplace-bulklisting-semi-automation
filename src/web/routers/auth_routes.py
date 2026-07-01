import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.web import oauth
from src.web.auth import AuthError, _get_jwks, verify_jwt

router = APIRouter()

STATE_COOKIE = "oauth_state"
TOKEN_COOKIE = "id_token"


def _settings(request):
    return request.app.state.settings


@router.get("/login")
def login(request: Request):
    settings = _settings(request)
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(oauth.authorize_url(settings, state), status_code=302)
    resp.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True,
                    samesite="lax", secure=settings.cookie_secure, path="/")
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    settings = _settings(request)
    if not state or state != request.cookies.get(STATE_COOKIE):
        return JSONResponse({"detail": "invalid state"}, status_code=400)
    try:
        tokens = oauth.exchange_code(settings, code)
        verify_jwt(tokens["id_token"], settings, _get_jwks(settings))
    except AuthError:
        return RedirectResponse("/login", status_code=302)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(TOKEN_COOKIE, tokens["id_token"], httponly=True,
                    samesite="lax", secure=settings.cookie_secure, path="/")
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/logout")
def logout(request: Request):
    settings = _settings(request)
    resp = RedirectResponse(oauth.logout_url(settings), status_code=302)
    resp.delete_cookie(TOKEN_COOKIE, path="/")
    return resp
