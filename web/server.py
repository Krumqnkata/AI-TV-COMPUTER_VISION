"""FastAPI application composition.

Business logic lives in ``web.services`` and HTTP/WebSocket contracts in
``web.routers``. This module intentionally contains only application wiring.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from utils.config import Config
from web.admin_panel import setup_admin
from web.database import SessionLocal, assert_schema_current, db_engine, get_db
from web.routers.admin_api import router as admin_api_router
from web.routers.device_api import router as device_api_router
from web.routers.device_control import router as device_control_router
from web.routers.system import router as system_router
from web.security import ensure_runtime_secrets, install_security_middleware
from web.services.admin_control import ensure_admin_foundation
from web.services.runtime import runtime_registry


ensure_runtime_secrets()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate migrations and install idempotent admin defaults at startup."""
    assert_schema_current()
    with SessionLocal() as startup_db:
        ensure_admin_foundation(startup_db)
    yield


app = FastAPI(title="School AI Control Panel", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=Config.ADMIN_SECRET_KEY,
    same_site="strict",
    https_only=Config.COOKIE_SECURE,
    max_age=Config.ADMIN_SESSION_SECONDS,
)
install_security_middleware(app)

static_dir = Config.PROJECT_ROOT / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

admin_static = Config.PROJECT_ROOT / "web" / "static" / "admin"
app.mount("/static/admin", StaticFiles(directory=str(admin_static)), name="admin-static")

app.include_router(system_router)
app.include_router(device_api_router)
app.include_router(device_control_router)
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
