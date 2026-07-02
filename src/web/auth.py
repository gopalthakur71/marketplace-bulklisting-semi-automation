from dataclasses import dataclass

from jose import jwt
from jose.utils import base64url_decode  # noqa: F401  (ensures crypto backend present)

_JWKS_CACHE = {}


class AuthError(Exception):
    pass


@dataclass
class User:
    email: str


def _jwks_url(settings):
    return (f"https://cognito-idp.{settings.s3_region}.amazonaws.com/"
            f"{settings.cognito_pool_id}/.well-known/jwks.json")


def _get_jwks(settings):
    url = _jwks_url(settings)
    if url not in _JWKS_CACHE:
        import urllib.request
        import json
        with urllib.request.urlopen(url, timeout=5) as r:
            _JWKS_CACHE[url] = json.loads(r.read())
    return _JWKS_CACHE[url]


def verify_jwt(token, settings, jwks):
    """Verify a Cognito JWT against a JWKS dict. Returns claims or raises AuthError."""
    try:
        headers = jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k["kid"] == headers["kid"]), None)
        if key is None:
            raise AuthError("unknown signing key")
        claims = jwt.decode(
            token, key, algorithms=["RS256"],
            audience=settings.cognito_client_id,
            issuer=(f"https://cognito-idp.{settings.s3_region}.amazonaws.com/"
                    f"{settings.cognito_pool_id}"),
            # Cognito id_tokens carry an at_hash claim. We verify the id_token on
            # its own (the cookie holds no access_token), so skip at_hash — jose
            # otherwise raises JWTClaimsError("No access_token ... at_hash").
            options={"verify_at_hash": False},
        )
        return claims
    except AuthError:
        raise
    except Exception as exc:  # jose raises various subclasses
        raise AuthError(str(exc))


def current_user(settings, token):
    if settings.auth_disabled:
        return User(email="dev@local")
    if not token:
        raise AuthError("no token")
    claims = verify_jwt(token, settings, _get_jwks(settings))
    return User(email=claims.get("email") or claims.get("username") or "unknown")
