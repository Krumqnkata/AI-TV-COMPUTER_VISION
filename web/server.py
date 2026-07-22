"""FastAPI application composition.

Business logic lives in ``web.services`` and HTTP/WebSocket contracts in
``web.routers``. This module intentionally contains only application wiring.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from utils.config import Config
from web.admin_panel import setup_admin
from web.database import SessionLocal, db_engine, get_db, init_schema
from web.routers.admin_api import router as admin_api_router
from web.routers.device_api import router as device_api_router
from web.routers.system import router as system_router
from web.security import ensure_runtime_secrets, install_security_middleware
from web.services.runtime import runtime_registry


ensure_runtime_secrets()
init_schema()

app = FastAPI(title="School AI Control Panel", version="2.0.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=Config.ADMIN_SECRET_KEY,
    same_site="strict",
    https_only=Config.COOKIE_SECURE,
    max_age=8 * 60 * 60,
)
install_security_middleware(app)

audio_cache = Config.PROJECT_ROOT / "data" / "audio_cache"
audio_cache.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(audio_cache)), name="audio")

app.include_router(system_router)
app.include_router(device_api_router)
app.include_router(admin_api_router)
admin_panel = setup_admin(app)


# Compatibility exports used by utilities and existing integrations.
__all__ = [
    "app",
    "admin_panel",
    "db_engine",
    "SessionLocal",
    "get_db",
    "runtime_registry",
]


def run_server(host=None, port=None):
    import uvicorn

    uvicorn.run(
        app,
        host=host or Config.SERVER_HOST,
        port=port or Config.SERVER_PORT,
        log_level="info",
    )
