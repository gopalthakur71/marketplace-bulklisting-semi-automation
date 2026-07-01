from fastapi.testclient import TestClient

from src.web import oauth
from src.web.main import create_app
from src.web.routers import auth_routes
from src.web.settings import Settings


def _settings():
    return Settings(
        auth_disabled=False,
        cognito_domain="ijor-marketplace",
        s3_region="ap-south-1",
        cognito_pool_id="pool",
        cognito_client_id="cid",
        cognito_client_secret="sec",
        cognito_redirect_uri="http://localhost:8000/auth/callback",
    )


def _client():
    return TestClient(create_app(_settings()), follow_redirects=False)


def test_login_redirects_and_sets_state_cookie():
    r = _client().get("/login")
    assert r.status_code == 302
    assert "oauth2/authorize" in r.headers["location"]
    assert r.cookies.get("oauth_state")


def test_callback_rejects_state_mismatch():
    c = _client()
    c.cookies.set("oauth_state", "expected")
    r = c.get("/auth/callback?code=x&state=WRONG")
    assert r.status_code == 400


def test_callback_happy_path_sets_token_cookie(monkeypatch):
    monkeypatch.setattr(oauth, "exchange_code",
                        lambda settings, code: {"id_token": "TOK"})
    monkeypatch.setattr(auth_routes, "verify_jwt",
                        lambda token, settings, jwks: {"email": "u@x"})
    monkeypatch.setattr(auth_routes, "_get_jwks", lambda settings: {})
    c = _client()
    c.cookies.set("oauth_state", "s1")
    r = c.get("/auth/callback?code=abc&state=s1")
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert r.cookies.get("id_token") == "TOK"


def test_callback_redirects_to_login_on_exchange_failure(monkeypatch):
    from src.web.auth import AuthError

    def boom(settings, code):
        raise AuthError("bad code")

    monkeypatch.setattr(oauth, "exchange_code", boom)
    c = _client()
    c.cookies.set("oauth_state", "s1")
    r = c.get("/auth/callback?code=abc&state=s1")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_logout_clears_cookie_and_redirects_to_cognito():
    c = _client()
    r = c.get("/logout")
    assert r.status_code == 302
    assert "/logout?" in r.headers["location"]
    set_cookie = r.headers.get("set-cookie", "")
    assert "id_token=" in set_cookie and ("Max-Age=0" in set_cookie or 'id_token=""' in set_cookie)


def test_callback_without_code_redirects_to_login_without_exchange(monkeypatch):
    def must_not_call(settings, code):
        raise AssertionError("exchange_code should not be called when code is missing")

    monkeypatch.setattr(oauth, "exchange_code", must_not_call)
    c = _client()
    c.cookies.set("oauth_state", "s1")
    r = c.get("/auth/callback?state=s1")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
