"""SQLAdmin composition root for the Bulgarian school control centre."""

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqladmin import Admin
from sqladmin.authentication import login_required

from utils.config import Config
from web.admin.auth import AdminAuth
from web.admin.control_views import CONTROL_VIEWS
from web.admin.model_views import MODEL_VIEWS
from web.database import db_engine


class SchoolControlAdmin(Admin):
    @login_required
    async def index(self, request: Request):
        return RedirectResponse("/admin/dashboard", status_code=302)


def setup_admin(app) -> Admin:
    panel = SchoolControlAdmin(
        app,
        db_engine,
        authentication_backend=AdminAuth(secret_key=Config.ADMIN_SECRET_KEY),
        title="Училищен AI контролен център",
        templates_dir=str(Config.PROJECT_ROOT / "web" / "templates"),
    )
    for view in CONTROL_VIEWS:
        panel.add_view(view)
    for view in MODEL_VIEWS:
        panel.add_view(view)
    return panel
