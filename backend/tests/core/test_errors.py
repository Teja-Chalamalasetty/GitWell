import json
import logging

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import API_PREFIX, AppError
from app.main import create_app
from tests.conftest import make_settings


class Body(BaseModel):
    email: str


def build() -> TestClient:
    app = create_app(make_settings())
    r = APIRouter(prefix=API_PREFIX)

    @r.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret-internal-detail")

    @r.get("/conflict")
    def conflict() -> None:
        raise AppError(409, "REPO_ALREADY_EXISTS", "Already tracked.", {"id": 3})

    @r.post("/validate")
    def validate(body: Body) -> Body:
        return body

    app.include_router(r)
    return TestClient(app, raise_server_exceptions=False)


def test_app_error_shape() -> None:
    with build() as c:
        r = c.get("/api/v1/conflict")
    assert r.status_code == 409
    assert r.json() == {
        "error": {
            "code": "REPO_ALREADY_EXISTS",
            "message": "Already tracked.",
            "details": {"id": 3},
        }
    }


def test_validation_error_shape_has_fields() -> None:
    with build() as c:
        r = c.post("/api/v1/validate", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["fields"][0]["loc"] == ["body", "email"]


def test_unknown_api_route_is_not_found_in_error_shape() -> None:
    with build() as c:
        r = c.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_unhandled_exception_hides_details_and_has_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with build() as c:
        r = c.get("/api/v1/boom")
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "INTERNAL_ERROR"
    assert "secret-internal-detail" not in r.text
    assert err["details"]["request_id"] == r.headers["X-Request-ID"]
    # the stack trace is logged, not returned
    logged = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    assert any("RuntimeError: secret-internal-detail" in e.get("exc_info", "") for e in logged)


def test_request_log_is_json_with_route_template(capsys: pytest.CaptureFixture[str]) -> None:
    with build() as c:
        c.get("/api/v1/conflict")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    entries = [json.loads(ln) for ln in lines]
    entry = next(e for e in entries if e["message"] == "request")
    assert entry["route"] == "/api/v1/conflict"
    assert entry["method"] == "GET" and entry["status"] == 409
    assert entry["request_id"] and "duration_ms" in entry


def test_httpx_loggers_are_quiet() -> None:
    build()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
