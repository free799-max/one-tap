"""集成密码查看授权的期限、用途与用户隔离测试。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.exceptions import APIException
from app.core.security import create_access_token, hash_password
from app.services.integrations import secret_reveal
from app.services.integrations.secret_reveal import authorize_secret_reveal


def _user(user_id: int = 1):
    return SimpleNamespace(id=user_id, hashed_password=hash_password("pass123456"))


def test_secret_reveal_grant_is_valid_for_three_hours():
    user = _user()

    grant = authorize_secret_reveal(user, "pass123456")

    assert secret_reveal.SECRET_REVEAL_TTL_SECONDS == 3 * 60 * 60
    assert 3 * 60 * 60 - 1 <= (
        grant.expires_at - datetime.now(timezone.utc)
    ).total_seconds() <= 3 * 60 * 60

    cached = authorize_secret_reveal(user, reveal_token=grant.token)
    assert cached.token == grant.token
    assert cached.expires_at == grant.expires_at


def test_secret_reveal_rejects_missing_wrong_and_foreign_credentials():
    user = _user()
    grant = authorize_secret_reveal(user, "pass123456")

    with pytest.raises(APIException) as missing:
        authorize_secret_reveal(user)
    assert missing.value.status_code == 400

    with pytest.raises(APIException) as wrong:
        authorize_secret_reveal(user, "wrong-pass")
    assert wrong.value.status_code == 403

    with pytest.raises(APIException) as foreign:
        authorize_secret_reveal(_user(2), reveal_token=grant.token)
    assert foreign.value.status_code == 403


def test_secret_reveal_rejects_expired_invalid_and_login_tokens(monkeypatch):
    user = _user()

    with pytest.raises(APIException) as invalid:
        authorize_secret_reveal(user, reveal_token="invalid-token")
    assert invalid.value.status_code == 403

    login_token = create_access_token({"sub": str(user.id)})
    with pytest.raises(APIException) as wrong_purpose:
        authorize_secret_reveal(user, reveal_token=login_token)
    assert wrong_purpose.value.status_code == 403

    monkeypatch.setattr(secret_reveal, "SECRET_REVEAL_TTL_SECONDS", -1)
    expired = authorize_secret_reveal(user, "pass123456")
    with pytest.raises(APIException) as expired_error:
        authorize_secret_reveal(user, reveal_token=expired.token)
    assert expired_error.value.status_code == 403
