"""Error framework (PRD §11.1): every non-2xx from /api/v1 uses one JSON shape."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger("gitwell.errors")

API_PREFIX = "/api/v1"

_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "BAD_REQUEST",
    422: "VALIDATION_ERROR",
}


class AppError(Exception):
    """An error with a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(code, message, details), status_code, headers=headers)


def internal_error_response() -> JSONResponse:
    return error_response(
        500,
        "INTERNAL_ERROR",
        "Internal server error.",
        {"request_id": request_id_var.get()},
    )


def _is_api(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIX)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "loc": [str(p) for p in err.get("loc", ())],
                "message": str(err.get("msg", "")),
                "type": str(err.get("type", "")),
            }
            for err in exc.errors()
        ]
        return error_response(
            422, "VALIDATION_ERROR", "Request validation failed.", {"fields": fields}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers) if exc.headers else None
        if not _is_api(request):
            return JSONResponse({"detail": exc.detail}, exc.status_code, headers=headers)
        code = _STATUS_CODES.get(exc.status_code, "BAD_REQUEST")
        message = str(exc.detail) if exc.detail else code.replace("_", " ").capitalize()
        return error_response(exc.status_code, code, message, headers=headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Stack trace goes to the logs only (S11).
        logger.error("unhandled_exception", exc_info=exc)
        return internal_error_response()
