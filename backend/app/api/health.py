from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import VERSION
from app.db import get_db

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "version": VERSION, "db": "error"}, 503)
    return JSONResponse({"status": "ok", "version": VERSION, "db": "ok"})
