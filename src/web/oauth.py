import base64
import json
import urllib.parse
import urllib.request

from src.web.auth import AuthError


def _base(settings):
    return (f"https://{settings.cognito_domain}.auth."
            f"{settings.s3_region}.amazoncognito.com")


def authorize_url(settings, state):
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": settings.cognito_client_id,
        "redirect_uri": settings.cognito_redirect_uri,
        "scope": "openid email",
        "state": state,
    })
    return f"{_base(settings)}/oauth2/authorize?{q}"


def logout_url(settings):
    parts = urllib.parse.urlsplit(settings.cognito_redirect_uri)
    logout_uri = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    q = urllib.parse.urlencode({
        "client_id": settings.cognito_client_id,
        "logout_uri": logout_uri,
    })
    return f"{_base(settings)}/logout?{q}"


def _post(url, data, headers):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def exchange_code(settings, code, http=_post):
    basic = base64.b64encode(
        f"{settings.cognito_client_id}:{settings.cognito_client_secret}".encode()
    ).decode()
    try:
        tokens = http(
            f"{_base(settings)}/oauth2/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.cognito_redirect_uri,
            },
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except Exception as exc:
        raise AuthError(f"token exchange failed: {exc}")
    if not isinstance(tokens, dict) or "id_token" not in tokens:
        raise AuthError("token response missing id_token")
    return tokens
