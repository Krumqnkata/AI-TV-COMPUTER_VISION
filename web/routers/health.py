"""Process liveness and dependency readiness probes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from engine.db import now_bg
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
