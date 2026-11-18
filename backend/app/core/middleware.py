"""Request-id + access-log middleware."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import API_PREFIX, internal_error_response
from app.core.logging import request_id_var
from app.core.metrics import get_metrics

logger = logging.getLogger("gitwell.access")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        request.state.request_id = rid
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unhandled_exception")
                response = internal_error_response()
            route = request.scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            # Nested routers report their path without the prefix; restore it so templates are
            # unambiguous ("/api/v1/config", not "/config"). Still a template, never a real path.
            if template != "unmatched" and request.url.path.startswith(API_PREFIX):
                if not template.startswith(API_PREFIX):
                    template = API_PREFIX + template
            response.headers["X-Request-ID"] = rid
            elapsed = time.perf_counter() - start
            metrics = get_metrics()
            metrics.http_requests.labels(request.method, template, str(response.status_code)).inc()
            metrics.http_duration.labels(request.method, template).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "route": template,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)
