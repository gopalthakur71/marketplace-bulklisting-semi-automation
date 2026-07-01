import pytest

from src.web.auth import AuthError
from src.web.oauth import authorize_url, exchange_code, logout_url
from src.web.settings import Settings

S = Settings(
    cognito_domain="ijor-marketplace",
    s3_region="ap-south-1",
    cognito_client_id="cid",
    cognito_client_secret="sec",
    cognito_redirect_uri="http://localhost:8000/auth/callback",
)


def test_authorize_url_has_expected_params():
    url = authorize_url(S, "xyz")
    assert url.startswith(
        "https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/oauth2/authorize?")
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert "state=xyz" in url
    assert "scope=openid+email" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fauth%2Fcallback" in url


def test_exchange_code_sends_basic_auth_and_parses_tokens():
    captured = {}

    def fake_http(url, data, headers):
        captured.update(url=url, data=data, headers=headers)
        return {"id_token": "tok", "access_token": "a", "refresh_token": "r"}

    tokens = exchange_code(S, "the-code", http=fake_http)
    assert tokens["id_token"] == "tok"
    assert captured["url"].endswith("/oauth2/token")
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "the-code"
    assert captured["data"]["redirect_uri"] == S.cognito_redirect_uri
    assert captured["headers"]["Authorization"].startswith("Basic ")


def test_exchange_code_raises_without_id_token():
    with pytest.raises(AuthError):
        exchange_code(S, "c", http=lambda url, data, headers: {"error": "invalid_grant"})


def test_exchange_code_wraps_http_errors():
    def boom(url, data, headers):
        raise RuntimeError("500 from cognito")

    with pytest.raises(AuthError):
        exchange_code(S, "c", http=boom)


def test_logout_url_points_to_site_root():
    url = logout_url(S)
    assert url.startswith(
        "https://ijor-marketplace.auth.ap-south-1.amazoncognito.com/logout?")
    assert "client_id=cid" in url
    assert "logout_uri=http%3A%2F%2Flocalhost%3A8000%2F" in url
