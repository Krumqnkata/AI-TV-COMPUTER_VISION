import base64
import html
import io
import secrets
import threading
import time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqladmin import Admin, BaseView, ModelView, action, expose
from sqladmin.authentication import AuthenticationBackend
from wtforms import SelectField

from engine.auth import get_password_hash, verify_password
from engine.db import Badge, Camera, Event, InteractionPoint, Message, Person, SystemEvent, Timetable, hash_token, now_bg
from utils.config import Config
from web.database import SessionLocal, db_engine


def _format_datetime(model, attribute):
    name = attribute if isinstance(attribute, str) else attribute.key
    value = getattr(model, name, None)
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def _format_person_id(model, attribute):
    name = attribute if isinstance(attribute, str) else attribute.key
    person_id = getattr(model, name, None)
    if not person_id:
        return "-"
    db = SessionLocal()
    try:
        person = db.get(Person, person_id)
        return f"{person.full_name} ({person.role})" if person else f"ID: {person_id}"
    finally:
        db.close()


def _admin_only(request: Request) -> bool:
    return request.session.get("role") == "admin"


def _staff(request: Request) -> bool:
    return request.session.get("role") in ("admin", "teacher")


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        db = SessionLocal()
        try:
            user = db.query(Person).filter(Person.full_name == username).first()
            if (
                user
                and user.active
                and user.role in ("admin", "teacher")
                and user.password_hash
                and verify_password(password, user.password_hash)
            ):
                request.session.clear()
                request.session.update({"person_id": user.id, "role": user.role})
                return True
            return False
        finally:
            db.close()

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        person_id = request.session.get("person_id")
        if not person_id:
            return False
        db = SessionLocal()
        try:
            user = db.get(Person, person_id)
            if not user or not user.active or user.role not in ("admin", "teacher"):
                request.session.clear()
                return False
            request.session["role"] = user.role
            return True
        finally:
            db.close()


class PersonAdmin(ModelView, model=Person):
    name = "Потребител"
    name_plural = "Потребители"
    category = "Управление на хора"
    icon = "fa-solid fa-users"
    can_export = True
    page_size = 50
    column_list = [Person.id, Person.full_name, Person.role, Person.class_name, Person.active]
    column_searchable_list = [Person.full_name]
    column_sortable_list = [Person.role, Person.active]
    column_details_exclude_list = [Person.password_hash]
    form_excluded_list = [Person.created_at]
    column_labels = {
        Person.full_name: "Пълно име",
        Person.role: "Роля",
        Person.class_name: "Клас",
        Person.active: "Активен",
        Person.password_hash: "Парола или съществуващ Argon2 хеш",
    }
    column_formatters = {
        Person.active: lambda model, _attribute: "✅ Да" if model.active else "❌ Не",
        Person.role: lambda model, _attribute: {
            "student": "🎓 Ученик",
            "teacher": "👨‍🏫 Учител",
            "admin": "🛠 Админ",
            "guest": "👤 Гост",
        }.get(model.role, model.role),
    }
    form_overrides = {"role": SelectField}
    form_args = {"role": {"choices": [
        ("student", "Ученик"),
        ("teacher", "Учител"),
        ("admin", "Администратор"),
        ("guest", "Гост"),
    ]}}

    async def on_model_change(self, data, model, is_created, request):
        value = data.get("password_hash")
        if value and not str(value).startswith("$argon2"):
            data["password_hash"] = get_password_hash(str(value))

    def is_accessible(self, request: Request) -> bool:
        return _admin_only(request)


_reveal_store: dict[str, tuple[list[dict], float]] = {}
_reveal_lock = threading.Lock()
_REVEAL_TTL = 300


def _store_reveal(results: list[dict]) -> str:
    ticket = secrets.token_urlsafe(24)
    now = time.time()
    with _reveal_lock:
        for key, (_results, created) in list(_reveal_store.items()):
            if now - created > _REVEAL_TTL:
                _reveal_store.pop(key, None)
        _reveal_store[ticket] = (results, now)
    return ticket


def _pop_reveal(ticket: str):
    with _reveal_lock:
        entry = _reveal_store.pop(ticket, None)
    if not entry or time.time() - entry[1] > _REVEAL_TTL:
        return None
    return entry[0]


def _render_badges(results: list[dict]) -> str:
    import qrcode

    blocks = []
    for result in results:
        image = qrcode.make(result["token"])
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data_uri = base64.b64encode(buffer.getvalue()).decode("ascii")
        blocks.append(
            '<div style="page-break-inside:avoid;border:1px solid #ccc;padding:16px;margin:12px;text-align:center;display:inline-block">'
            f'<img src="data:image/png;base64,{data_uri}" width="220" height="220">'
            f'<p><b>{html.escape(result["person_name"])}</b></p>'
            f'<p style="font-family:monospace">{html.escape(result["token"])}</p>'
            f'<small>Бадж №{result["badge_id"]}</small></div>'
        )
    return (
        '<html><body style="font-family:sans-serif;padding:24px">'
        '<h2>Нови QR токени — разпечатайте сега</h2>'
        '<p style="color:#b00">Токените се показват само веднъж.</p>'
        + "".join(blocks)
        + '<p><a href="/admin/badge/list">← Обратно</a></p></body></html>'
    )


class BadgeAdmin(ModelView, model=Badge):
    name = "QR Бадж"
    name_plural = "QR Баджове"
    category = "Управление на хора"
    icon = "fa-solid fa-id-badge"
    can_export = True
    can_create = False
    page_size = 50
    column_list = [Badge.id, Badge.person_id, Badge.status, Badge.created_at]
    form_excluded_list = [Badge.token_hash, Badge.created_at]
    column_details_exclude_list = [Badge.token_hash]
    column_labels = {Badge.person_id: "Собственик", Badge.status: "Статус", Badge.created_at: "Създаден"}
    column_formatters = {
        Badge.person_id: _format_person_id,
        Badge.created_at: _format_datetime,
        Badge.status: lambda model, _attribute: {"active": "🟢 Активен", "lost": "🔴 Изгубен", "disabled": "⚫ Спрян"}.get(model.status, model.status),
    }
    form_overrides = {"status": SelectField}
    form_args = {"status": {"choices": [("active", "Активен"), ("lost", "Изгубен"), ("disabled", "Деактивиран")]}}

    def is_accessible(self, request: Request) -> bool:
        return _admin_only(request)

    @action(name="regenerate_token", label="Генерирай нов QR токен", confirmation_message="Старият QR код ще спре да работи. Продължи?")
    async def regenerate_token(self, request: Request):
        ids = request.query_params.get("pks", "").replace("?pks=", "").replace("pks=", "")
        db = SessionLocal()
        results = []
        try:
            for value in ids.split(","):
                if not value.strip().isdigit():
                    continue
                badge = db.get(Badge, int(value))
                if not badge:
                    continue
                token = f"SCH-{secrets.token_hex(16).upper()}"
                badge.token_hash = hash_token(token)
                badge.status = "active"
                results.append({"badge_id": badge.id, "person_name": badge.person.full_name, "token": token})
            db.commit()
        finally:
            db.close()
        if not results:
            raise HTTPException(status_code=400, detail="Няма валидни избрани баджове")
        return RedirectResponse(f"/admin/badge/reveal/{_store_reveal(results)}", status_code=302)


class EventAdmin(ModelView, model=Event):
    name = "Събитие"
    name_plural = "Събития"
    category = "Училищен живот"
    icon = "fa-solid fa-calendar-days"
    column_list = [Event.id, Event.title, Event.start_time, Event.target_group, Event.room]
    column_searchable_list = [Event.title]
    column_formatters = {Event.start_time: _format_datetime, Event.end_time: _format_datetime}
    def is_accessible(self, request: Request) -> bool: return _staff(request)


class TimetableAdmin(ModelView, model=Timetable):
    name = "Учебен час"
    name_plural = "Разписание"
    category = "Училищен живот"
    icon = "fa-solid fa-clock"
    page_size = 50
    can_export = True
    column_list = [Timetable.id, Timetable.person_id, Timetable.date, Timetable.period, Timetable.subject, Timetable.room]
    column_formatters = {Timetable.person_id: _format_person_id}
    def is_accessible(self, request: Request) -> bool: return _staff(request)


class MessageAdmin(ModelView, model=Message):
    name = "Съобщение"
    name_plural = "Виртуална поща"
    category = "Система"
    icon = "fa-solid fa-envelope"
    column_list = [Message.id, Message.sender_id, Message.recipient_id, Message.status, Message.valid_until]
    column_formatters = {
        Message.sender_id: _format_person_id,
        Message.recipient_id: _format_person_id,
        Message.valid_until: _format_datetime,
    }
    def is_accessible(self, request: Request) -> bool: return _staff(request)


class InteractionPointAdmin(ModelView, model=InteractionPoint):
    name = "Интерактивна точка"
    name_plural = "Интерактивни точки"
    category = "Инфраструктура"
    icon = "fa-solid fa-display"
    column_list = [InteractionPoint.id, InteractionPoint.name, InteractionPoint.zone_id, InteractionPoint.type, InteractionPoint.screen_id, InteractionPoint.active]
    def is_accessible(self, request: Request) -> bool: return _admin_only(request)


class CameraAdmin(ModelView, model=Camera):
    name = "Камера"
    name_plural = "Камери"
    category = "Инфраструктура"
    icon = "fa-solid fa-video"
    column_list = [Camera.id, Camera.name, Camera.zone_id, Camera.interaction_point_id, Camera.active]
    def is_accessible(self, request: Request) -> bool: return _admin_only(request)


class SystemEventAdmin(ModelView, model=SystemEvent):
    name = "Системно събитие"
    name_plural = "Логове"
    category = "Система"
    icon = "fa-solid fa-list-check"
    page_size = 100
    can_create = can_edit = can_delete = False
    column_list = [SystemEvent.id, SystemEvent.event_type, SystemEvent.person_id, SystemEvent.timestamp]
    column_default_sort = [(SystemEvent.timestamp, True)]
    column_formatters = {SystemEvent.timestamp: _format_datetime, SystemEvent.person_id: _format_person_id}
    def is_accessible(self, request: Request) -> bool: return _admin_only(request)


class BadgeCreateView(BaseView):
    name = "Създай нов бадж"
    category = "Управление на хора"
    icon = "fa-solid fa-plus"

    def is_accessible(self, request: Request) -> bool:
        return _admin_only(request)

    @expose("/badge/create-custom", methods=["GET", "POST"])
    async def create_custom_badge(self, request: Request):
        if not _admin_only(request):
            raise HTTPException(status_code=403, detail="Само за администратори")
        db = SessionLocal()
        try:
            if request.method == "GET":
                people = db.query(Person).filter(Person.active.is_(True)).order_by(Person.full_name).all()
                options = "".join(
                    f'<option value="{person.id}">{html.escape(person.full_name)} ({html.escape(person.role)})</option>'
                    for person in people
                )
                return HTMLResponse(
                    '<html><body style="font-family:sans-serif;padding:24px"><h2>Нов QR бадж</h2>'
                    '<form method="post"><label>Собственик:</label><br><select name="person_id" required>'
                    + options
                    + '</select><br><br><button type="submit">Създай</button></form></body></html>'
                )
            form = await request.form()
            person = db.get(Person, int(form.get("person_id", 0)))
            if not person:
                raise HTTPException(status_code=404, detail="Потребителят не е намерен")
            token = f"SCH-{secrets.token_hex(16).upper()}"
            badge = Badge(person_id=person.id, token_hash=hash_token(token), status="active", created_at=now_bg())
            db.add(badge)
            db.commit()
            db.refresh(badge)
            ticket = _store_reveal([{"badge_id": badge.id, "person_name": person.full_name, "token": token}])
            return RedirectResponse(f"/admin/badge/reveal/{ticket}", status_code=302)
        finally:
            db.close()

    @expose("/badge/reveal/{ticket}", methods=["GET"])
    async def badge_reveal(self, request: Request):
        if not _admin_only(request):
            raise HTTPException(status_code=403, detail="Само за администратори")
        results = _pop_reveal(request.path_params["ticket"])
        if not results:
            return HTMLResponse("<h2>Токенът вече не е наличен.</h2>", status_code=410)
        return HTMLResponse(_render_badges(results))


def setup_admin(app) -> Admin:
    backend = AdminAuth(secret_key=Config.ADMIN_SECRET_KEY)
    panel = Admin(app, db_engine, authentication_backend=backend, title="🏫 AI Асистент - Училищен панел")
    for view in [
        PersonAdmin,
        BadgeAdmin,
        EventAdmin,
        TimetableAdmin,
        MessageAdmin,
        InteractionPointAdmin,
        CameraAdmin,
        SystemEventAdmin,
        BadgeCreateView,
    ]:
        panel.add_view(view)
    return panel
