import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_jwt_secret_required_outside_test() -> None:
    with pytest.raises(ValidationError):
        make_settings(env="production", jwt_secret="short")


def test_jwt_secret_32_chars_ok_in_production() -> None:
    assert make_settings(env="production", jwt_secret="x" * 32).env == "production"


def test_jwt_secret_missing_in_test_env_fails() -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_secret="")


def test_admin_password_too_short_fails_startup() -> None:
    with pytest.raises(ValidationError):
        make_settings(admin_email="a@b.com", admin_password="short")


def test_empty_optional_values_become_none() -> None:
    s = make_settings(github_token="", admin_email="")
    assert s.github_token is None and s.admin_email is None


def test_metrics_enabled_requires_token() -> None:
    with pytest.raises(ValidationError):
        make_settings(metrics_enabled=True)
    assert make_settings(metrics_enabled=True, metrics_token="t").metrics_token == "t"


def test_cors_origin_list() -> None:
    assert make_settings(cors_origins="http://a, http://b").cors_origin_list == [
        "http://a",
        "http://b",
    ]
