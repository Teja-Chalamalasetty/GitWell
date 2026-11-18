"""GET /metrics (PRD 14). Registered only when METRICS_ENABLED=true; otherwise it is a plain 404."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.core.metrics import CONTENT_TYPE, get_metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, settings: Settings = Depends(get_settings)) -> Response:
    header = request.headers.get("authorization", "")
    scheme, _, supplied = header.partition(" ")
    expected = settings.metrics_token or ""
    # Constant-time comparison; an empty configured token can never match.
    ok = (
        scheme.lower() == "bearer"
        and bool(expected)
        and hmac.compare_digest(supplied.strip().encode(), expected.encode())
    )
    if not ok:
        return JSONResponse(
            {"detail": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    return Response(get_metrics().render(), media_type=CONTENT_TYPE)
