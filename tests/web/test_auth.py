import base64
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from src.web.auth import current_user, verify_jwt, AuthError, User
from src.web.settings import Settings


def test_dev_bypass_returns_fixed_user():
    s = Settings(auth_disabled=True)
    u = current_user(s, token=None)
    assert isinstance(u, User)
    assert u.email == "dev@local"


def test_missing_token_rejected_when_auth_on():
    s = Settings(auth_disabled=False, cognito_pool_id="p", cognito_client_id="c",
                 s3_region="ap-south-1")
    with pytest.raises(AuthError):
        current_user(s, token=None)


def _b64u(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _signed_cognito_id_token():
    """Build an RS256 id_token that mirrors a real Cognito id_token — crucially
    including an `at_hash` claim — plus the matching JWKS. Regression guard for
    the login loop: python-jose rejects a token that has at_hash unless we pass
    access_token or disable the check (see verify_jwt options=verify_at_hash)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    nums = key.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA", "kid": "testkid", "use": "sig", "alg": "RS256",
        "n": _b64u(nums.n), "e": _b64u(nums.e),
    }]}
    settings = Settings(cognito_client_id="cid", cognito_pool_id="pool",
                        s3_region="ap-south-1")
    claims = {
        "aud": "cid",
        "iss": "https://cognito-idp.ap-south-1.amazonaws.com/pool",
        "email": "gopalthakur71@gmail.com",
        "token_use": "id",
        "at_hash": "0Yr9C0eBiPzWg8H5wJq7rw",  # present on real Cognito id_tokens
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "testkid"})
    return token, settings, jwks


def test_verify_jwt_accepts_cognito_token_with_at_hash():
    token, settings, jwks = _signed_cognito_id_token()
    claims = verify_jwt(token, settings, jwks)
    assert claims["email"] == "gopalthakur71@gmail.com"
