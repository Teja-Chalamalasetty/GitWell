import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import VERSION
from app.main import create_app
from tests.conftest import make_settings


def test_health_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": VERSION, "db": "ok"}
    assert r.headers["X-Request-ID"]


def test_health_db_down_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Session, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(Session, "execute", boom)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["db"] == "error"


def test_config_defaults(client: TestClient) -> None:
    r = client.get("/api/v1/config")
    assert r.status_code == 200
    assert r.json() == {
        "allow_registration": False,
        "github_token_configured": False,
        "version": VERSION,
    }


def test_config_reflects_settings() -> None:
    app = create_app(make_settings(allow_registration=True, github_issues_token="t"))
    with TestClient(app) as c:
        body = c.get("/api/v1/config").json()
    assert body["allow_registration"] is True
    assert body["github_token_configured"] is True


def test_docs_only_in_development() -> None:
    with TestClient(create_app(make_settings(env="test"))) as c:
        assert c.get("/docs").status_code == 404
    dev = make_settings(env="development", jwt_secret="x" * 32)
    with TestClient(create_app(dev)) as c:
        assert c.get("/docs").status_code == 200
