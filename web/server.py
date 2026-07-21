import uvicorn
import os
import json
import asyncio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from engine.csrf import generate_csrf_token, verify_csrf_token, CSRF_COOKIE_NAME
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from engine.auth import verify_password
import threading
import time
from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel
import re

# Импортиране на базата данни и моделите
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from engine.db import (
    Person, Badge, InteractionPoint, Camera, Message, Timetable, Event, SystemEvent,
    hash_token, now_bg, today_bg
)
from engine.llm_manager import LLMManager

# Хеширане на пароли за директно създаване на потребители през админ панела
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="School AI Control Panel")


# ─── Security Headers middleware ───
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

# ─── CSRF Protection middleware ───
@app.middleware("http")
async def csrf_protect(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # SQLAdmin (пътища под /admin) си има собствена сесийна автентикация
        if not request.url.path.startswith("/admin"):
            if not verify_csrf_token(request):
                raise HTTPException(status_code=403, detail="Invalid CSRF token")
    response = await call_next(request)
    if request.method in ("GET", "HEAD"):
        token = request.cookies.get(CSRF_COOKIE_NAME)
        if not token:
            token = generate_csrf_token()
        response.set_cookie(key=CSRF_COOKIE_NAME, value=token, httponly=False, samesite="strict")
    return response

# Инициализиране на LLM
llm_manager = LLMManager()

# ─── Database setup ───
DATABASE_URL = "sqlite:///data/school_ai.db"
db_engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _require_admin_session(request: Request):
    if request.session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Само за администратори")

# ==========================================
# ИНТЕГРАЦИЯ НА SQLADMIN
# ==========================================
from sqladmin import Admin, ModelView, action, BaseView, expose
from sqladmin.authentication import AuthenticationBackend
from starlette.middleware.sessions import SessionMiddleware
from wtforms import SelectField

ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "fallback-secret-key-for-dev")
app.add_middleware(SessionMiddleware, secret_key=ADMIN_SECRET_KEY)

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        db = SessionLocal()
        user = db.query(Person).filter(Person.full_name == username).first()
        
        if user and user.password_hash and verify_password(password, user.password_hash):
            if user.role in ("admin", "teacher") and user.active:
                request.session.update({"admin_token": user.full_name, "role": user.role})
                db.close()
                return True
        db.close()
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("admin_token")
        return token is not None

authentication_backend = AdminAuth(secret_key=ADMIN_SECRET_KEY)
# ==========================================
# ИНТЕГРАЦИЯ НА SQLADMIN (ОБНОВЕН ДИЗАЙН И ЛОГИКА)
# ==========================================

# 1. Задаване на заглавие на панела
admin_panel = Admin(
    app, 
    db_engine, 
    authentication_backend=authentication_backend,
    title="🏫 AI Асистент - Училищен Панел",
)

# --- ПОМОЩНИ ФУНКЦИИ ЗА ФОРМАТИРАНЕ ---

def format_datetime(model, attribute):
    # Проверяваме дали SQLAdmin подава текст (str) или обект с .key
    attr_name = attribute if isinstance(attribute, str) else attribute.key
    val = getattr(model, attr_name, None)
    return val.strftime("%d.%m.%Y %H:%M") if val else ""


def format_person_id(model, attribute):
    # Проверяваме дали SQLAdmin подава текст (str) или обект с .key
    attr_name = attribute if isinstance(attribute, str) else attribute.key
    person_id = getattr(model, attr_name, None)
    
    if not person_id:
        return "-"
    
    # Отваряме кратка сесия специално за форматъра
    db = SessionLocal()
    try:
        person = db.get(Person, person_id)
        if person:
            return f"{person.full_name} ({person.role})"
        return f"ID: {person_id}"
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
    
    
    column_labels = {
        Person.full_name: "Пълно име",
        Person.role: "Роля",
        Person.class_name: "Клас",
        Person.active: "Активен",
        Person.password_hash: "Парола"
    }
    
    # 🌟 НОВО: Красиви формати за роли и статуси
    column_formatters = {
        Person.active: lambda m, a: "✅ Да" if m.active else "❌ Не",
        Person.role: lambda m, a: {
            "student": "🎓 Ученик", 
            "teacher": "👨‍🏫 Учител", 
            "admin": "🛠 Админ", 
            "guest": "👤 Гост"
        }.get(m.role, m.role)
    }
    
    form_overrides = {"role": SelectField}
    form_args = {
        "role": {
            "choices": [
                ("student", "Ученик"),
                ("teacher", "Учител"),
                ("admin", "Администратор"),
                ("guest", "Гост")
            ]
        }
    }

    form_excluded_list = [Person.created_at]
    column_details_exclude_list = [Person.password_hash]

    async def on_model_change(self, data, model, is_created, request):
        if 'password_hash' in data and data['password_hash']:
            if not data['password_hash'].startswith('$2b$'):
                data['password_hash'] = pwd_context.hash(data['password_hash'])

    def is_accessible(self, request: Request) -> bool:
        return request.session.get("role") == "admin"


class BadgeAdmin(ModelView, model=Badge):
    name = "QR Бадж"
    name_plural = "QR Баджове"
    category = "Управление на хора"
    icon = "fa-solid fa-id-badge"
    
    can_export = True
    page_size = 50
    
    column_list = [Badge.id, Badge.person_id, Badge.status, Badge.created_at]

    
    column_labels = {
        Badge.person_id: "Собственик",
        Badge.status: "Статус на баджа",
        Badge.created_at: "Създаден на"
    }

    # 🌟 НОВО: Показваме името на човека, а не само ID, и форматираме датата
    column_formatters = {
        Badge.person_id: format_person_id,
        Badge.created_at: format_datetime,
        Badge.status: lambda m, a: {
            "active": "🟢 Активен", 
            "lost": "🔴 Изгубен", 
            "disabled": "⚫ Спрян"
        }.get(m.status, m.status)
    }

    form_excluded_list = [Badge.token_hash, Badge.created_at]
    column_details_exclude_list = [Badge.token_hash]
    can_create = False

    form_overrides = {"status": SelectField}
    form_args = {
        "status": {
            "choices": [
                ("active", "Активен"),
                ("lost", "Изгубен"),
                ("disabled", "Деактивиран")
            ]
        }
    }

    def is_accessible(self, request: Request) -> bool:
        return request.session.get("role") == "admin"

    @action(
        name="regenerate_token",
        label="Генерирай нов QR токен",
        confirmation_message="Старият QR код на баджа ще спре да работи веднага. Продължи?",
    )
    async def regenerate_token(self, request: Request):
        import secrets
        pks_raw = request.query_params.get("pks", "")
        cleaned = pks_raw.replace("?pks=", "").replace("pks=", "")
        
        db = SessionLocal()
        results = []
        try:
            for pk in cleaned.split(","):
                pk = pk.strip()
                if not pk.isdigit(): continue
                badge = db.get(Badge, int(pk))
                if not badge: continue
                
                new_token = f"SCH-{secrets.token_hex(4).upper()}"
                badge.token_hash = hash_token(new_token)
                person = db.get(Person, badge.person_id) if badge.person_id else None
                results.append({
                    "badge_id": badge.id,
                    "person_name": person.full_name if person else "(без собственик)",
                    "token": new_token,
                })
            db.commit()
        finally:
            db.close()

        if not results:
            raise HTTPException(status_code=400, detail="Няма избрани валидни баджове")

        ticket = _store_qr_reveal(results)
        return RedirectResponse(url=f"/admin/badge/reveal/{ticket}", status_code=302)


class EventAdmin(ModelView, model=Event):
    name = "Събитие"
    name_plural = "Събития"
    category = "Училищен живот"
    icon = "fa-solid fa-calendar-days"
    
    column_list = [Event.id, Event.title, Event.start_time, Event.target_group, Event.room]
    column_searchable_list = [Event.title]

    
    column_labels = {
        Event.title: "Заглавие",
        Event.description: "Описание",
        Event.start_time: "Начало",
        Event.end_time: "Край",
        Event.target_group: "За кого",
        Event.room: "Зала/Кабинет"
    }
    
    column_formatters = {
        Event.start_time: format_datetime,
        Event.end_time: format_datetime
    }

class TimetableAdmin(ModelView, model=Timetable):
    name = "Учебен час"
    name_plural = "Разписание"
    category = "Училищен живот"
    icon = "fa-solid fa-clock"
    page_size = 50
    can_export = True
    
    column_list = [Timetable.id, Timetable.person_id, Timetable.date, Timetable.period, Timetable.subject, Timetable.room]
    
    column_labels = {
        Timetable.person_id: "Учител/Ученик",
        Timetable.date: "Дата",
        Timetable.period: "Час (1-8)",
        Timetable.start_time: "Начало",
        Timetable.end_time: "Край",
        Timetable.subject: "Предмет",
        Timetable.class_name: "Клас",
        Timetable.room: "Кабинет"
    }

    column_formatters = {
        Timetable.person_id: format_person_id
    }


class MessageAdmin(ModelView, model=Message):
    name = "Съобщение"
    name_plural = "Виртуална поща"
    category = "Система"
    icon = "fa-solid fa-envelope"
    
    column_list = [Message.id, Message.sender_id, Message.recipient_id, Message.status, Message.valid_until]
    
    
    column_labels = {
        Message.sender_id: "Подател",
        Message.recipient_id: "Получател",
        Message.status: "Статус",
        Message.valid_until: "Валидно до"
    }

    column_formatters = {
        Message.sender_id: lambda m, a: m.sender.full_name if getattr(m, "sender", None) else m.sender_id,
        Message.recipient_id: lambda m, a: m.recipient.full_name if getattr(m, "recipient", None) else m.recipient_id,
        Message.valid_until: format_datetime,
        Message.status: lambda m, a: {
            "active": "📩 Чакащо", 
            "delivered": "✅ Доставено", 
            "expired": "⏳ Изтекло", 
            "deleted": "🗑 Изтрито"
        }.get(m.status, m.status)
    }
    
    form_overrides = {"status": SelectField}
    form_args = {
        "status": {
            "choices": [
                ("active", "Чакащо (Активно)"),
                ("delivered", "Доставено"),
                ("expired", "Изтекло"),
                ("deleted", "Изтрито")
            ]
        }
    }


class InteractionPointAdmin(ModelView, model=InteractionPoint):
    name = "Интерактивна точка"
    name_plural = "Интерактивни точки"
    category = "Инфраструктура"
    icon = "fa-solid fa-display"

    column_list = [InteractionPoint.id, InteractionPoint.name, InteractionPoint.zone_id,
                   InteractionPoint.type, InteractionPoint.active]
    column_labels = {
        InteractionPoint.name: "Име",
        InteractionPoint.zone_id: "Зона",
        InteractionPoint.type: "Тип",
        InteractionPoint.screen_id: "Свързан екран",
        InteractionPoint.active: "Активна"
    }
    
    column_formatters = {
        InteractionPoint.active: lambda m, a: "✅ Онлайн" if m.active else "❌ Офлайн"
    }

    def is_accessible(self, request: Request) -> bool:
        return request.session.get("role") == "admin"


class CameraAdmin(ModelView, model=Camera):
    name = "Камера"
    name_plural = "Камери"
    category = "Инфраструктура"
    icon = "fa-solid fa-video"

    column_list = [Camera.id, Camera.name, Camera.zone_id, Camera.active]
    column_labels = {
        Camera.name: "Име",
        Camera.zone_id: "Зона",
        Camera.interaction_point_id: "Свързана точка",
        Camera.stream_url: "Локален адрес",
        Camera.active: "Активна"
    }

    column_formatters = {
        Camera.active: lambda m, a: "🎥 Активна" if m.active else "❌ Изключена"
    }

    def is_accessible(self, request: Request) -> bool:
        return request.session.get("role") == "admin"


class SystemEventAdmin(ModelView, model=SystemEvent):
    name = "Системно събитие"
    name_plural = "Логове"
    category = "Система"
    icon = "fa-solid fa-list-check"
    page_size = 100

    column_list = [SystemEvent.id, SystemEvent.event_type, SystemEvent.person_id, SystemEvent.timestamp]
    
    
    column_labels = {
        SystemEvent.event_type: "Тип събитие",
        SystemEvent.person_id: "Потребител",
        SystemEvent.camera_id: "Камера",
        SystemEvent.interaction_point_id: "Точка",
        SystemEvent.timestamp: "Време",
        SystemEvent.metadata_json: "Технически данни"
    }
    
    column_default_sort = [(SystemEvent.timestamp, True)]
    column_searchable_list = [SystemEvent.event_type]

    column_formatters = {
        SystemEvent.timestamp: format_datetime,
        SystemEvent.person_id: format_person_id
    }

    can_create = False
    can_edit = False
    can_delete = False

    def is_accessible(self, request: Request) -> bool:
        return request.session.get("role") == "admin"

import secrets as _secrets_module
_qr_reveal_store: dict = {}
_qr_reveal_lock = threading.Lock()
_QR_REVEAL_TTL_SECONDS = 300  

def _store_qr_reveal(results: list) -> str:
    ticket = _secrets_module.token_urlsafe(24)
    now_ts = time.time()
    with _qr_reveal_lock:
        expired = [t for t, (_, ts) in _qr_reveal_store.items() if now_ts - ts > _QR_REVEAL_TTL_SECONDS]
        for t in expired:
            _qr_reveal_store.pop(t, None)
        _qr_reveal_store[ticket] = (results, now_ts)
    return ticket

def _pop_qr_reveal(ticket: str):
    with _qr_reveal_lock:
        entry = _qr_reveal_store.pop(ticket, None)
    if entry is None:
        return None
    results, ts = entry
    if time.time() - ts > _QR_REVEAL_TTL_SECONDS:
        return None
    return results

def _render_badge_qr_page(results: list) -> str:
    qr_blocks = ""
    try:
        import qrcode
        import io
        import base64
        for r in results:
            img = qrcode.make(r["token"])
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data_uri = base64.b64encode(buf.getvalue()).decode("ascii")
            qr_blocks += f"""
            <div style="page-break-inside: avoid; border: 1px solid #ccc; padding: 16px; margin-bottom: 16px; display: inline-block; text-align: center;">
                <img src="data:image/png;base64,{data_uri}" width="220" height="220" />
                <p style="font-family: sans-serif; margin: 4px 0;"><b>{r['person_name']}</b></p>
                <p style="font-family: monospace; margin: 0;">{r['token']}</p>
                <p style="font-family: sans-serif; font-size: 12px; color: #888;">Бадж №{r['badge_id']}</p>
            </div>"""
    except ImportError:
        for r in results:
            qr_blocks += f"""
            <div style="font-family: sans-serif; padding: 8px; border-bottom: 1px solid #eee;">
                <b>{r['person_name']}</b> (бадж №{r['badge_id']}) — токен: <code>{r['token']}</code>
            </div>"""

    return f"""
    <html>
    <head><title>Нови QR баджове</title></head>
    <body style="font-family: sans-serif; padding: 24px;">
        <h2>Нови QR токени — разпечатайте сега</h2>
        <p style="color: #b00;">Тези токени се показват само веднъж. След затваряне на страницата не могат да бъдат възстановени от системата.</p>
        {qr_blocks}
        <p><a href="/admin/badge/list">← Обратно към баджовете</a></p>
    </body>
    </html>
    """
# ТУК ДЕФИНИРАМЕ НОВИЯ КЛАС ЗА СЪЗДАВАНЕ НА БАДЖ
class BadgeCreateView(BaseView):
    name = "Създай нов Бадж"
    category = "Управление на хора"
    icon = "fa-solid fa-plus"

    @expose("/badge/create-custom", methods=["GET", "POST"])
    async def create_custom_badge(self, request: Request):
        _require_admin_session(request)

        if request.method == "GET":
            db = SessionLocal()
            try:
                people = db.query(Person).filter(Person.active == True).order_by(Person.full_name).all()
            finally:
                db.close()

            options = "".join(
                f'<option value="{p.id}">{p.full_name} ({p.role})</option>' for p in people
            )
            html = f"""
            <html>
            <head><title>Нов QR бадж</title></head>
            <body style="font-family: sans-serif; padding: 24px;">
                <h2>Нов QR бадж</h2>
                <form method="post" action="/admin/badge/create-custom">
                    <label>Собственик:</label><br/>
                    <select name="person_id" required style="padding: 8px; min-width: 300px;">
                        {options}
                    </select><br/><br/>
                    <button type="submit" style="padding: 8px 16px;">Създай и генерирай токен</button>
                </form>
                <p><a href="/admin/badge/list">← Отказ, обратно към баджовете</a></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html)

        elif request.method == "POST":
            form = await request.form()
            person_id = form.get("person_id")
            if not person_id:
                raise HTTPException(status_code=422, detail="Не е избран собственик")

            import secrets
            db = SessionLocal()
            try:
                person = db.get(Person, int(person_id))
                if not person:
                    raise HTTPException(status_code=404, detail="Потребителят не е намерен")

                new_token = f"SCH-{secrets.token_hex(4).upper()}"
                badge = Badge(
                    person_id=person.id,
                    token_hash=hash_token(new_token),
                    status="active",
                    created_at=now_bg(),
                )
                db.add(badge)
                db.commit()
                db.refresh(badge)

                results = [{"badge_id": badge.id, "person_name": person.full_name, "token": new_token}]
            finally:
                db.close()

            ticket = _store_qr_reveal(results)
            return RedirectResponse(url=f"/admin/badge/reveal/{ticket}", status_code=302)

    # -------------------------------------------------------------
    # ПРЕМЕСТЕНА ЛОГИКА ЗА ПОКАЗВАНЕ НА БАДЖА (вече част от SQLAdmin)
    # -------------------------------------------------------------
    @expose("/badge/reveal/{ticket}", methods=["GET"])
    async def badge_reveal(self, request: Request):
        _require_admin_session(request)
        
        # Взимаме билета от URL-а
        ticket = request.path_params.get("ticket")
        results = _pop_qr_reveal(ticket)
        
        if results is None:
            return HTMLResponse(content="""
            <html><body style="font-family: sans-serif; padding: 24px;">
                <h2>Тези данни вече не са налични</h2>
                <p>Токенът е бил показан вече (или билетът е изтекъл). 
                Генерирайте нов от списъка с баджове.</p>
                <p><a href="/admin/badge/list">← Обратно към баджовете</a></p>
            </body></html>
            """, status_code=410)
            
        return HTMLResponse(content=_render_badge_qr_page(results))
    

# РЕГИСТРАЦИЯ НА ВСИЧКИ ИЗГЛЕДИ
admin_panel.add_view(PersonAdmin)
admin_panel.add_view(BadgeAdmin)
admin_panel.add_view(EventAdmin)
admin_panel.add_view(TimetableAdmin)
admin_panel.add_view(MessageAdmin)
admin_panel.add_view(InteractionPointAdmin)
admin_panel.add_view(CameraAdmin)
admin_panel.add_view(SystemEventAdmin)
admin_panel.add_view(BadgeCreateView) # Нашата форма за баджове се регистрира последна


# ==========================================
# ОСНОВНО API И KIOSK СИСТЕМА
# ==========================================

class QRDetectionRequest(BaseModel):
    camera_id: str
    zone_id: str
    badge_token: str
    timestamp: Optional[datetime] = None
    confidence: Optional[float] = 1.0

class CloseSessionRequest(BaseModel):
    zone_id: Optional[str] = None
    interaction_point_id: Optional[int] = None

class MessageCreateRequest(BaseModel):
    sender_id: int
    recipient_id: int
    text: str
    valid_hours: Optional[int] = 24

class VoiceCommandRequest(BaseModel):
    person_id: Optional[int] = None
    text_query: str

os.makedirs("data/audio_cache", exist_ok=True)
os.makedirs("data/history_cache", exist_ok=True)

app.mount("/audio", StaticFiles(directory="data/audio_cache"), name="audio")
app.mount("/history", StaticFiles(directory="data/history_cache"), name="history")
templates = Jinja2Templates(directory="web/templates")

state_manager = None
face_manager = None
recent_detections = {}
active_sessions = {}

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        if state_manager:
            await websocket.send_text(json.dumps({
                "type": "initial_state",
                "data": state_manager.get_stats()
            }))
        
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/history")
async def get_visual_history():
    history_dir = "data/history_cache"
    if not os.path.exists(history_dir):
        return []
    
    files = []
    for f in os.listdir(history_dir):
        if f.endswith(".jpg"):
            path = os.path.join(history_dir, f)
            files.append({
                "filename": f,
                "url": f"/history/{f}",
                "time": os.path.getmtime(path)
            })
    
    files.sort(key=lambda x: x["time"], reverse=True)
    return files[:40]

@app.get("/api/stats")
async def get_stats():
    if state_manager:
        return state_manager.get_stats()
    return {"total": 0, "is_processing": True, "status": "Offline"}

@app.post("/api/toggle")
async def toggle_processing():
    if state_manager:
        current = state_manager.is_processing()
        state_manager.set_processing(not current)
        return {"success": True, "is_processing": not current}
    return {"success": False}


@app.post("/api/detect_qr")
async def detect_qr(request: QRDetectionRequest, db: Session = Depends(get_db)):
    token = request.badge_token
    hashed = hash_token(token)
    now = now_bg()

    expired_tokens = [t for t, info in recent_detections.items() if (now - info["timestamp"]).total_seconds() > 30]
    for t in expired_tokens:
        recent_detections.pop(t, None)

    prev_det = recent_detections.get(token)
    if prev_det:
        time_diff = (now - prev_det["timestamp"]).total_seconds()
        if prev_det["camera_id"] == request.camera_id:
            if time_diff < 10.0:
                return {"status": "ignored", "reason": "duplicate_same_camera"}
        else:
            if time_diff < 5.0 and (request.confidence or 1.0) <= prev_det["confidence"]:
                return {"status": "ignored", "reason": "duplicate_other_camera_lower_confidence"}
    
    badge = db.query(Badge).filter(Badge.token_hash == hashed, Badge.status == "active").first()
    
    if not badge:
        sys_event = SystemEvent(
            event_type="unknown_badge_detected",
            timestamp=now_bg(),
            metadata_json=json.dumps({
                "camera_id": request.camera_id,
                "zone_id": request.zone_id,
                "badge_token": token,
            }, ensure_ascii=False)
        )
        db.add(sys_event)
        db.commit()
        
        await manager.broadcast(json.dumps({
            "type": "unknown_badge",
            "data": {"camera_id": request.camera_id, "zone_id": request.zone_id}
        }, ensure_ascii=False))
        
        return {"status": "error", "message": "Неразпознат или неактивен бадж"}
        
    person = badge.person
    if not person.active:
        raise HTTPException(status_code=400, detail="Профилът е деактивиран")
        
    camera = db.query(Camera).filter(Camera.name == request.camera_id).first()
    camera_id_db = camera.id if camera else None
    ip_point_id = camera.interaction_point_id if camera else None

    session_key = ip_point_id or request.zone_id
    active_session = active_sessions.get(session_key)
    if active_session:
        session_person_id = active_session["person_id"]
        last_act = active_session["last_activity"]
        if (now - last_act).total_seconds() < 60.0:
            if session_person_id != person.id:
                return {"status": "ignored", "reason": "kiosk_busy"}
            else:
                active_session["last_activity"] = now
        else:
            active_sessions[session_key] = {"person_id": person.id, "last_activity": now}
    else:
        active_sessions[session_key] = {"person_id": person.id, "last_activity": now}

    recent_detections[token] = {
        "camera_id": request.camera_id,
        "timestamp": now,
        "confidence": request.confidence or 1.0
    }

    pending_messages = db.query(Message).filter(
        Message.recipient_id == person.id,
        Message.status == "active",
        Message.valid_until > now
    ).all()
    
    delivered_texts = [msg.text for msg in pending_messages]
    for msg in pending_messages:
        msg.status = "delivered"
        msg.delivered_at = now_bg()
    
    today = today_bg()
    current_time = now_bg().time()
    
    next_class = db.query(Timetable).filter(
        Timetable.person_id == person.id,
        Timetable.date == today,
        Timetable.start_time > current_time
    ).order_by(Timetable.start_time).first()
    
    next_class_info = None
    next_class_str = ""
    if next_class:
        next_class_info = {
            "subject": next_class.subject,
            "room": next_class.room,
            "start_time": next_class.start_time.strftime("%H:%M"),
            "end_time": next_class.end_time.strftime("%H:%M"),
            "class_name": next_class.class_name
        }
        if person.role == "teacher":
            next_class_str = f"Следващият Ви час е {next_class.subject} с {next_class.class_name} в {next_class.room} от {next_class_info['start_time']} ч."
        else:
            next_class_str = f"Следващият ти час е {next_class.subject} в {next_class.room} от {next_class_info['start_time']} ч."
            
    if person.role == "teacher":
        greeting = f"Здравейте, г-жо/г-н {person.full_name}!"
    elif person.role == "admin":
        greeting = f"Здравейте, Администратор {person.full_name}!"
    elif person.role == "student":
        greeting = f"Здравей, {person.full_name.split()[0]}!"
    else:
        greeting = f"Здравейте, {person.full_name}!"
        
    messages_str = ""
    if delivered_texts:
        count = len(delivered_texts)
        if person.role == "teacher":
            messages_str = f"Имате {count} нови съобщения: " + " | ".join(delivered_texts)
        else:
            messages_str = f"Имаш {count} нови съобщения: " + " | ".join(delivered_texts)
            
    welcome_msg = f"{greeting} {messages_str} {next_class_str}".strip()
    
    sys_event = SystemEvent(
        event_type="badge_detected",
        camera_id=camera_id_db,
        interaction_point_id=ip_point_id,
        person_id=person.id,
        timestamp=now_bg(),
        metadata_json=json.dumps({
            "camera_id": request.camera_id,
            "zone_id": request.zone_id,
            "confidence": request.confidence,
            "welcome_message": welcome_msg
        }, ensure_ascii=False)
    )
    db.add(sys_event)
    db.commit()
    
    ws_payload = {
        "type": "badge_detected",
        "data": {
            "person_id": person.id,
            "name": person.full_name,
            "role": person.role,
            "class_name": person.class_name,
            "message": welcome_msg,
            "next_class": next_class_info,
            "pending_messages_count": len(delivered_texts),
            "zone_id": request.zone_id
        }
    }
    await manager.broadcast(json.dumps(ws_payload, ensure_ascii=False))
    
    return {
        "status": "success",
        "person": {"id": person.id, "name": person.full_name, "role": person.role},
        "message": welcome_msg,
        "messages_delivered": delivered_texts,
        "next_class": next_class_info
    }

@app.post("/api/sessions/close")
async def close_session(request: CloseSessionRequest):
    session_key = request.interaction_point_id or request.zone_id
    closed = False
    
    if session_key in active_sessions:
        active_sessions.pop(session_key)
        closed = True
    else:
        for k in list(active_sessions.keys()):
            if str(k) == str(session_key):
                active_sessions.pop(k)
                closed = True
                break
                
    if closed:
        await manager.broadcast(json.dumps({
            "type": "session_closed",
            "data": {"zone_id": request.zone_id}
        }, ensure_ascii=False))
        return {"success": True}
    return {"success": False}

@app.post("/api/messages")
async def create_message(request: MessageCreateRequest, db: Session = Depends(get_db)):
    sender = db.query(Person).filter(Person.id == request.sender_id).first()
    recipient = db.query(Person).filter(Person.id == request.recipient_id).first()
    if not sender or not recipient:
        raise HTTPException(status_code=404, detail="Изпращачът или получателят не съществува")
        
    from datetime import timedelta
    valid_until = now_bg() + timedelta(hours=request.valid_hours)
        
    msg = Message(
        sender_id=request.sender_id,
        recipient_id=request.recipient_id,
        text=request.text,
        valid_until=valid_until,
        status="active"
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    
    return {"success": True, "message_id": msg.id}

@app.get("/api/messages/pending")
async def get_pending_messages(person_id: int, db: Session = Depends(get_db)):
    msgs = db.query(Message).filter(
        Message.recipient_id == person_id,
        Message.status == "active",
        Message.valid_until > now_bg()
    ).all()
    return [{"id": m.id, "sender_name": m.sender.full_name, "text": m.text} for m in msgs]


def parse_intent_rule_based(query: str) -> dict:
    import re
    query = query.lower().strip()
    
    if any(x in query for x in ["съобщение", "съобщения", "писма", "писмо", "имам ли нещо"]):
        if not any(x in query for x in ["остави", "кажи на", "предай"]):
            return {"intent": "check_messages", "recipient_name": None, "message_text": None, "room_number": None, "date": "today"}
            
    if any(x in query for x in ["остави съобщение", "кажи на", "предай на", "напиши на"]):
        recipient_name, message_text = None, None
        for marker in ["остави съобщение за", "остави съобщение на", "кажи на", "предай на", "напиши на"]:
            if marker in query:
                parts = query.split(marker, 1)
                after = parts[1].strip()
                for split_word in [", че ", " че ", ", да ", " да "]:
                    if split_word in after:
                        recipient_name, message_text = after.split(split_word, 1)
                        break
                if not recipient_name:
                    words = after.split()
                    if len(words) >= 2:
                        recipient_name, message_text = " ".join(words[:2]), " ".join(words[2:])
                    else:
                        recipient_name, message_text = after, ""
                break
        return {"intent": "leave_message", "recipient_name": recipient_name.strip() if recipient_name else None, "message_text": message_text.strip() if message_text else None, "room_number": None, "date": None}

    if any(x in query for x in ["свободен час", "дупка", "прозорец"]):
        return {"intent": "check_free_periods", "recipient_name": None, "message_text": None, "room_number": None, "date": "tomorrow" if "утре" in query else "today"}

    if any(x in query for x in ["час", "клас", "програма", "разписание"]):
        return {"intent": "check_timetable", "recipient_name": None, "message_text": None, "room_number": None, "date": "tomorrow" if "утре" in query else "today"}

    if any(x in query for x in ["кабинет", "стая", "къде е", "намира се", "салон", "библиотека", "директор", "учителска"]):
        room_num = re.findall(r'\d+', query)
        room_num = room_num[0] if room_num else None
        if not room_num:
            if "салон" in query: room_num = "физкултурен салон"
            elif "библиотека" in query: room_num = "библиотека"
            elif "учителска" in query: room_num = "учителска стая"
            elif "директор" in query: room_num = "директор"
        return {"intent": "check_room", "recipient_name": None, "message_text": None, "room_number": room_num, "date": None}

    if any(x in query for x in ["събитие", "събития", "концерт", "клуб", "сбирка", "празник"]):
        return {"intent": "show_events", "recipient_name": None, "message_text": None, "room_number": None, "date": "today"}

    return {"intent": "unknown", "recipient_name": None, "message_text": None, "room_number": None, "date": None}


def find_person_by_name(name_str: str, db: Session) -> tuple:
    clean_name = name_str.lower().strip()
    for title in ["г-н", "г-за", "г-жа", "господин", "госпожа", "учител", "учителка"]:
        clean_name = clean_name.replace(title, "").strip()
        
    query_words = [w for w in re.split(r'\s+', clean_name) if len(w) > 0]
    if not query_words:
        return None, "none", "Моля посочете валидно име на получател."

    candidates = db.query(Person).filter(Person.active == True).all()
    matches = []
    
    for cand in candidates:
        cand_words = [w for w in re.split(r'\s+', cand.full_name.lower()) if len(w) > 0]
        
        all_words_matched = True
        for qw in query_words:
            word_matched = False
            for cw in cand_words:
                qw_clean = qw.rstrip('.')
                cw_clean = cw.rstrip('.')
                if qw_clean == cw_clean or (len(qw_clean) >= 2 and cw_clean.startswith(qw_clean)):
                    word_matched = True
                    break
            if not word_matched:
                all_words_matched = False
                break
                
        if all_words_matched:
            matches.append(cand)
            
    if not matches:
        for cand in candidates:
            if clean_name in cand.full_name.lower():
                matches.append(cand)
                
    if not matches:
        return None, "none", f"Не успях да намеря потребител с име '{name_str}' в базата данни."
        
    if len(matches) > 1:
        names_str = " или ".join([m.full_name for m in matches])
        return None, "multiple", f"Намерих няколко съвпадения: {names_str}. За кой от тях се отнася?"
        
    return matches[0], "exact", ""

@app.post("/api/voice_command")
async def voice_command(request: VoiceCommandRequest, db: Session = Depends(get_db)):
    query = request.text_query.lower().strip()
    
    if request.person_id:
        now = now_bg()
        for session_key, session_info in active_sessions.items():
            if session_info["person_id"] == request.person_id:
                if (now - session_info["last_activity"]).total_seconds() < 60.0:
                    session_info["last_activity"] = now
    
    inappropriate_keywords = ["тъп", "глупав", "скапан", "урод", "педераст", "курва", "шибан"]
    if any(w in query for w in inappropriate_keywords):
        return {
            "intent": "blocked",
            "query": request.text_query,
            "response": "Моля, поддържайте учтив тон и задавайте въпроси, свързани само с училището."
        }

    parsed = parse_intent_rule_based(query)
    intent = parsed.get("intent", "unknown")
    response_text = "Не успях да разбера въпроса Ви. Опитайте отново с други думи."
    
    if intent == "leave_message":
        if not request.person_id:
            response_text = "Моля първо се идентифицирайте чрез бадж."
        else:
            sender = db.query(Person).filter(Person.id == request.person_id).first()
            recipient_name = parsed.get("recipient_name")
            message_text = parsed.get("message_text")
            
            if not recipient_name: response_text = "За кого е съобщението?"
            elif not message_text: response_text = f"Какво съобщение искате да оставите за {recipient_name}?"
            else:
                recipient, _, err_msg = find_person_by_name(recipient_name, db)
                if recipient:
                    from datetime import timedelta
                    msg = Message(sender_id=sender.id, recipient_id=recipient.id, text=message_text, valid_until=now_bg() + timedelta(hours=24), status="active")
                    db.add(msg)
                    db.commit()
                    response_text = f"Записах съобщението за {recipient.full_name}."
                else:
                    response_text = err_msg

    elif intent == "check_messages":
        if not request.person_id: response_text = "Моля първо се идентифицирайте чрез бадж."
        else:
            msgs = db.query(Message).filter(Message.recipient_id == request.person_id, Message.status == "active", Message.valid_until > now_bg()).all()
            if msgs:
                msg_list = [f"от {m.sender.full_name}: '{m.text}'" for m in msgs]
                for m in msgs:
                    m.status = "delivered"
                    m.delivered_at = now_bg()
                db.commit()
                response_text = f"Имате {len(msgs)} нови съобщения. " + ". ".join(msg_list)
            else:
                response_text = "Нямате нови съобщения."

    elif intent == "check_timetable":
        if not request.person_id: response_text = "Моля сканирайте баджа си."
        else:
            date_param = parsed.get("date") or "today"
            from datetime import timedelta
            target_date = today_bg()
            if date_param == "tomorrow": target_date += timedelta(days=1)
                
            if "следващ" in query and target_date == today_bg():
                next_class = db.query(Timetable).filter(Timetable.person_id == request.person_id, Timetable.date == target_date, Timetable.start_time > now_bg().time()).order_by(Timetable.start_time).first()
                if next_class: response_text = f"Следващият Ви час е {next_class.subject} в {next_class.room} от {next_class.start_time.strftime('%H:%M')} ч."
                else: response_text = "Нямате повече часове за днес."
            else:
                records = db.query(Timetable).filter(Timetable.person_id == request.person_id, Timetable.date == target_date).order_by(Timetable.period).all()
                date_word = "утре" if date_param == "tomorrow" else "днес"
                if records:
                    class_list = [f"{r.period}-ти час: {r.subject} в {r.room}" for r in records]
                    response_text = f"Програмата Ви за {date_word} е: " + ", ".join(class_list)
                else:
                    response_text = f"Нямате часове за {date_word}."

    elif intent == "check_free_periods":
        if not request.person_id: response_text = "Моля сканирайте баджа си."
        else:
            date_param = parsed.get("date") or "today"
            from datetime import timedelta
            target_date = today_bg()
            if date_param == "tomorrow": target_date += timedelta(days=1)
                
            records = db.query(Timetable).filter(Timetable.person_id == request.person_id, Timetable.date == target_date).order_by(Timetable.period).all()
            date_word = "утре" if date_param == "tomorrow" else "днес"
            
            if not records: response_text = f"Нямате часове за {date_word}."
            else:
                periods = [r.period for r in records]
                gaps = [p for p in range(min(periods) + 1, max(periods)) if p not in periods]
                if gaps: response_text = f"Имате свободен час ({date_word}) на: " + ", ".join([f"{g}-ти час" for g in gaps]) + "."
                else: response_text = f"Нямате свободни часове (дупки) за {date_word}."

    elif intent == "check_room":
        room_number = parsed.get("room_number")
        if not room_number: response_text = "Кой кабинет или зала търсите?"
        else:
            room_clean = str(room_number).lower().strip()
            rooms_dict = {
                "304": "Кабинет 304 се намира на третия етаж, дясно крило.",
                "302": "Кабинет 302 се намира на третия етаж, ляво крило.",
                "201": "Кабинет 201 се намира на втория етаж, ляво крило.",
                "104": "Кабинет 104 се намира на първия етаж, дясно крило.",
                "физкултурен салон": "Физкултурният салон се намира в двора на училището.",
                "библиотека": "Библиотеката се намира на първия етаж, срещу главния вход.",
                "учителска стая": "Учителската стая е на втория етаж.",
                "директор": "Кабинетът на директора се намира на втория етаж."
            }
            response_text = next((v for k, v in rooms_dict.items() if k in room_clean or room_clean in k), f"Не намерих кабинет '{room_number}'.")

    elif intent == "show_events":
        today = today_bg()
        from datetime import datetime as dt
        events = db.query(Event).filter(Event.start_time >= dt.combine(today, dt.min.time()), Event.start_time <= dt.combine(today, dt.max.time())).order_by(Event.start_time).all()
        if events: response_text = "Днес има: " + ", ".join([f"'{e.title}' в {e.room}" for e in events]) + "."
        else: response_text = "Няма планирани събития за днес."

    sys_event = SystemEvent(event_type="question_asked", person_id=request.person_id, timestamp=now_bg(), metadata_json=json.dumps({"query": request.text_query, "intent": intent, "response": response_text}, ensure_ascii=False))
    db.add(sys_event)
    db.commit()
    
    return {"intent": intent, "query": request.text_query, "response": response_text}

def run_server(manager_ref, fm, host="0.0.0.0", port=5000):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm
    uvicorn.run(app, host=host, port=port, log_level="error")

def start_web_server(manager_ref, fm):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm
    server_thread = threading.Thread(target=run_server_thread, args=(manager_ref, fm), daemon=True)
    server_thread.start()
    return server_thread

def run_server_thread(manager_ref, fm):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm
    state_manager.set_event_callback(lambda t, d: broadcast_from_thread(t, d))
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="error")

_main_loop = None

@app.on_event("startup")
async def startup_event():
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    app.loop = _main_loop

def broadcast_from_thread(event_type, data):
    if _main_loop and manager.active_connections:
        payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
        for ws in manager.active_connections:
            _main_loop.call_soon_threadsafe(asyncio.create_task, ws.send_text(payload))