import pytest

from src.web.auth import current_user, AuthError, User
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
