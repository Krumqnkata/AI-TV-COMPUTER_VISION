"""Process liveness and dependency readiness probes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from engine.db import now_bg
from web.database import get_db
from fastapi import Depends
from web.services.metrics import render_prometheus_metrics
from web.services.operations import readiness_report


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", include_in_schema=False)
def live():
    return JSONResponse(
        {
            "status": "alive",
            "alive": True,
            "checked_at": now_bg().isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ready", include_in_schema=False)
def ready():
    report = readiness_report()
    return JSONResponse(
        report,
        status_code=200 if report["ready"] else 503,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/metrics", include_in_schema=False)
def metrics(db: Session = Depends(get_db)):
    return PlainTextResponse(
        render_prometheus_metrics(db),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
