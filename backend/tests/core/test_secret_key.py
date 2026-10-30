import pytest

from app.config import DEFAULT_SECRET_KEY, ensure_secret_key, settings


@pytest.mark.parametrize("configured_key", [DEFAULT_SECRET_KEY, ""])
def test_secret_key_is_generated_and_reused(tmp_path, monkeypatch, configured_key):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SECRET_KEY", configured_key)

    ensure_secret_key()
    generated_key = settings.SECRET_KEY
    secret_path = tmp_path / ".secret_key"

    assert generated_key not in {DEFAULT_SECRET_KEY, ""}
    assert secret_path.read_text(encoding="utf-8") == generated_key

    monkeypatch.setattr(settings, "SECRET_KEY", DEFAULT_SECRET_KEY)
    ensure_secret_key()

    assert settings.SECRET_KEY == generated_key


def test_explicit_secret_key_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(settings, "SECRET_KEY", "explicitly-configured-key")

    ensure_secret_key()

    assert settings.SECRET_KEY == "explicitly-configured-key"
    assert not (tmp_path / ".secret_key").exists()
