import uvicorn
import os
import json
import asyncio
from fastapi import FastAPI, Response, Request, UploadFile, File, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import threading
import time
from typing import List, Optional
from datetime import datetime, date, time as time_type
from pydantic import BaseModel

# Импортиране на базата данни и моделите
import re
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from engine.db import (
    Person, Badge, InteractionPoint, Camera, Message, Timetable, Event, SystemEvent, hash_token
)
from engine.llm_manager import LLMManager

app = FastAPI(title="School AI Control Panel")

# Инициализиране на LLM
llm_manager = LLMManager()

# Настройка на връзката с базата данни
DATABASE_URL = "sqlite:///data/school_ai.db"
db_engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic схеми за API заявки и отговори
class QRDetectionRequest(BaseModel):
    camera_id: str
    zone_id: str
    badge_token: str
    timestamp: Optional[datetime] = None
    confidence: Optional[float] = 1.0

class MessageCreateRequest(BaseModel):
    sender_id: int
    recipient_id: int
    text: str
    valid_hours: Optional[int] = 24  # Валидност в часове от момента на изпращане

class PersonCreateRequest(BaseModel):
    full_name: str
    role: str  # student / teacher / admin / guest
    class_name: Optional[str] = None

# Уверяваме се, че директориите за кеш съществуват преди да ги монтираме
os.makedirs("data/audio_cache", exist_ok=True)
os.makedirs("data/history_cache", exist_ok=True)

app.mount("/audio", StaticFiles(directory="data/audio_cache"), name="audio")
app.mount("/history", StaticFiles(directory="data/history_cache"), name="history")
templates = Jinja2Templates(directory="web/templates")

# Глобална референция към StateManager и FaceManager
state_manager = None
face_manager = None


class ConnectionManager:
    """ Управление на активни WebSocket връзки """
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

def get_video_stream():
    """ Генератор за M-JPEG стрийминг """
    if state_manager:
        state_manager.increment_active_streams()
    try:
        while True:
            if state_manager:
                frame = state_manager.get_latest_frame()
                if frame:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.07) # Ограничаваме до ~14 FPS за пестене на трафик
    finally:
        if state_manager:
            state_manager.decrement_active_streams()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # При свързване изпращаме текущото състояние веднага
        if state_manager:
            await websocket.send_text(json.dumps({
                "type": "initial_state",
                "data": state_manager.get_stats()
            }))
        
        while True:
            # Държим връзката отворена и чакаме (не очакваме данни от клиента засега)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/api/history")
async def get_visual_history():
    """ Връща списък с последните 40 снимки от историята """
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
    
    # Сортираме по време (най-новите първо)
    files.sort(key=lambda x: x["time"], reverse=True)
    return files[:40]

@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(get_video_stream(), media_type="multipart/x-mixed-replace; boundary=frame")

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

@app.get("/api/faces")
async def list_faces():
    """ Връща списък с всички познати лица (папки) """
    if face_manager:
        faces_dir = face_manager.faces_path
        if os.path.exists(faces_dir):
            return [d for d in os.listdir(faces_dir) if os.path.isdir(os.path.join(faces_dir, d))]
    return []

@app.post("/api/upload")
async def upload_face(name: str, file: UploadFile = File(...)):
    """ Качване на нова снимка за лице """
    if face_manager:
        person_path = os.path.join(face_manager.faces_path, name)
        os.makedirs(person_path, exist_ok=True)
        
        file_path = os.path.join(person_path, file.filename)
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        face_manager.load_faces()
        return {"success": True, "message": f"Uploaded {file.filename} for {name}"}
    return {"success": False, "message": "Face manager not initialized"}


# ==========================================
# НОВИ API АДРЕСИ ЗА QR СИСТЕМАТА (ЕТАП 3)
# ==========================================

@app.post("/api/detect_qr")
async def detect_qr(request: QRDetectionRequest, db: Session = Depends(get_db)):
    """
    Приема засичане на QR бадж от камерите.
    Свързва токена с потребител, управлява съобщенията и извлича следващия час.
    """
    token = request.badge_token
    hashed = hash_token(token)
    
    # Търсим бадж
    badge = db.query(Badge).filter(Badge.token_hash == hashed, Badge.status == "active").first()
    
    if not badge:
        # Логваме неразпознат бадж
        sys_event = SystemEvent(
            event_type="unknown_badge_detected",
            timestamp=datetime.utcnow(),
            metadata_json=json.dumps({
                "camera_id": request.camera_id,
                "zone_id": request.zone_id,
                "badge_token": token,
                "confidence": request.confidence
            }, ensure_ascii=False)
        )
        db.add(sys_event)
        db.commit()
        
        # Излъчваме през WebSocket
        ws_payload = {
            "type": "unknown_badge",
            "data": {
                "camera_id": request.camera_id,
                "zone_id": request.zone_id,
                "timestamp": str(datetime.now())
            }
        }
        await manager.broadcast(json.dumps(ws_payload, ensure_ascii=False))
        
        return {"status": "error", "message": "Неразпознат или неактивен бадж"}
        
    person = badge.person
    if not person.active:
        raise HTTPException(status_code=400, detail="Профилът на потребителя е деактивиран")
        
    # Намираме активните съобщения за този потребител
    now = datetime.now()
    pending_messages = db.query(Message).filter(
        Message.recipient_id == person.id,
        Message.status == "active",
        Message.valid_until > now
    ).all()
    
    delivered_texts = []
    for msg in pending_messages:
        delivered_texts.append(msg.text)
        msg.status = "delivered"
        msg.delivered_at = datetime.utcnow()
    
    # Намираме следващия час за днес
    today = date.today()
    current_time = datetime.now().time()
    
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
            
    # Съставяне на персонализирано приветствие
    greeting = ""
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
    
    # Намираме ID на камерата и точката за лога
    camera = db.query(Camera).filter(Camera.name == request.camera_id).first()
    camera_id_db = camera.id if camera else None
    ip_point_id = camera.interaction_point_id if camera else None
    
    # Логваме събитието в базата данни
    sys_event = SystemEvent(
        event_type="badge_detected",
        camera_id=camera_id_db,
        interaction_point_id=ip_point_id,
        person_id=person.id,
        timestamp=datetime.utcnow(),
        metadata_json=json.dumps({
            "camera_id": request.camera_id,
            "zone_id": request.zone_id,
            "confidence": request.confidence,
            "welcome_message": welcome_msg
        }, ensure_ascii=False)
    )
    db.add(sys_event)
    db.commit()
    
    # Излъчваме през WebSocket за екрана
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
        "person": {
            "id": person.id,
            "name": person.full_name,
            "role": person.role,
            "class_name": person.class_name
        },
        "message": welcome_msg,
        "messages_delivered": delivered_texts,
        "next_class": next_class_info
    }

@app.post("/api/messages")
async def create_message(request: MessageCreateRequest, db: Session = Depends(get_db)):
    """
    Оставяне на съобщение за друг потребител.
    """
    sender = db.query(Person).filter(Person.id == request.sender_id).first()
    recipient = db.query(Person).filter(Person.id == request.recipient_id).first()
    if not sender or not recipient:
        raise HTTPException(status_code=404, detail="Изпращачът или получателят не съществува")
        
    from datetime import timedelta
    valid_until = datetime.now() + timedelta(hours=request.valid_hours)
        
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
    
    # Регистрираме техническо събитие
    sys_event = SystemEvent(
        event_type="message_created",
        person_id=sender.id,
        timestamp=datetime.utcnow(),
        metadata_json=json.dumps({
            "message_id": msg.id,
            "recipient_id": msg.recipient_id
        })
    )
    db.add(sys_event)
    db.commit()
    
    return {
        "success": True,
        "message_id": msg.id,
        "text": msg.text,
        "valid_until": msg.valid_until.isoformat()
    }

@app.get("/api/messages/pending")
async def get_pending_messages(person_id: int, db: Session = Depends(get_db)):
    """
    Връща списък с чакащите за доставка съобщения за даден потребител.
    """
    now = datetime.now()
    msgs = db.query(Message).filter(
        Message.recipient_id == person_id,
        Message.status == "active",
        Message.valid_until > now
    ).all()
    return [{
        "id": m.id,
        "sender_name": m.sender.full_name,
        "text": m.text,
        "valid_until": m.valid_until.isoformat()
    } for m in msgs]

@app.post("/api/persons")
async def create_person(request: PersonCreateRequest, db: Session = Depends(get_db)):
    """
    Създава нов потребителски профил.
    """
    person = Person(
        full_name=request.full_name,
        role=request.role,
        class_name=request.class_name,
        active=True
    )
    db.add(person)
    db.commit()
    db.refresh(person)
    return {
        "success": True,
        "person_id": person.id,
        "full_name": person.full_name,
        "role": person.role,
        "class_name": person.class_name
    }

@app.get("/api/persons")
async def list_persons(role: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Връща списък с всички потребители с филтър по роля.
    """
    query = db.query(Person)
    if role:
        query = query.filter(Person.role == role)
    persons = query.all()
    return [{
        "id": p.id,
        "full_name": p.full_name,
        "role": p.role,
        "class_name": p.class_name,
        "active": p.active
    } for p in persons]

@app.get("/api/persons/{person_id}/timetable")
async def get_timetable(person_id: int, date_str: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Връща училищната програма на даден потребител за определена дата.
    """
    target_date = date.today()
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Невалиден формат на дата. Използвайте ГГГГ-ММ-ДД")
            
    timetable = db.query(Timetable).filter(
        Timetable.person_id == person_id,
        Timetable.date == target_date
    ).order_by(Timetable.period).all()
    
    return [{
        "period": t.period,
        "subject": t.subject,
        "room": t.room,
        "start_time": t.start_time.strftime("%H:%M"),
        "end_time": t.end_time.strftime("%H:%M"),
        "class_name": t.class_name
    } for t in timetable]

@app.get("/api/cameras")
async def list_cameras(db: Session = Depends(get_db)):
    """
    Връща списък с всички камери.
    """
    cams = db.query(Camera).all()
    return [{
        "id": c.id,
        "name": c.name,
        "zone_id": c.zone_id,
        "active": c.active,
        "interaction_point_id": c.interaction_point_id
    } for c in cams]

@app.get("/api/interaction_points")
async def list_interaction_points(db: Session = Depends(get_db)):
    """
    Връща списък с всички интерактивни точки.
    """
    pts = db.query(InteractionPoint).all()
    return [{
        "id": p.id,
        "name": p.name,
        "zone_id": p.zone_id,
        "type": p.type,
        "screen_id": p.screen_id,
        "active": p.active
    } for p in pts]

class VoiceCommandRequest(BaseModel):
    person_id: Optional[int] = None
    text_query: str

def parse_intent_rule_based(query: str) -> dict:
    import re
    query = query.lower().strip()
    
    # 1. check_messages
    if any(x in query for x in ["съобщение", "съобщения", "съобщениа", "писма", "писмо", "имам ли нещо"]):
        if any(x in query for x in ["остави съобщение за", "остави съобщение на", "кажи на", "предай на"]):
            pass
        else:
            return {
                "intent": "check_messages",
                "recipient_name": None,
                "message_text": None,
                "room_number": None,
                "date": "today"
            }
            
    # 2. leave_message
    if any(x in query for x in ["остави съобщение за", "остави съобщение на", "кажи на", "предай на", "напиши на"]):
        recipient_name = None
        message_text = None
        for marker in ["остави съобщение за", "остави съобщение на", "кажи на", "предай на", "напиши на"]:
            if marker in query:
                parts = query.split(marker, 1)
                after_marker = parts[1].strip()
                if ", че " in after_marker:
                    name_part, msg_part = after_marker.split(", че ", 1)
                    recipient_name = name_part.strip()
                    message_text = msg_part.strip()
                elif " че " in after_marker:
                    name_part, msg_part = after_marker.split(" че ", 1)
                    recipient_name = name_part.strip()
                    message_text = msg_part.strip()
                elif ", да " in after_marker:
                    name_part, msg_part = after_marker.split(", да ", 1)
                    recipient_name = name_part.strip()
                    message_text = msg_part.strip()
                elif " да " in after_marker:
                    name_part, msg_part = after_marker.split(" да ", 1)
                    recipient_name = name_part.strip()
                    message_text = msg_part.strip()
                else:
                    words = after_marker.split()
                    if len(words) >= 2:
                        recipient_name = " ".join(words[:2])
                        message_text = " ".join(words[2:])
                    else:
                        recipient_name = after_marker
                        message_text = ""
                break
        return {
            "intent": "leave_message",
            "recipient_name": recipient_name,
            "message_text": message_text,
            "room_number": None,
            "date": None
        }

    # 3. check_free_periods
    if any(x in query for x in ["свободен час", "свободни часове", "дупка", "дупки", "прозорец"]):
        target_date = "tomorrow" if "утре" in query else "today"
        return {
            "intent": "check_free_periods",
            "recipient_name": None,
            "message_text": None,
            "room_number": None,
            "date": target_date
        }

    # 4. check_timetable
    if any(x in query for x in ["час", "клас", "програма", "разписание", "програмата", "часове", "часовете"]):
        target_date = "tomorrow" if "утре" in query else "today"
        return {
            "intent": "check_timetable",
            "recipient_name": None,
            "message_text": None,
            "room_number": None,
            "date": target_date
        }

    # 5. check_room
    if any(x in query for x in ["кабинет", "стая", "къде е", "намира се", "салон", "библиотека", "директор", "учителска"]):
        room_num = None
        numbers = re.findall(r'\d+', query)
        if numbers:
            room_num = numbers[0]
        else:
            if "салон" in query:
                room_num = "физкултурен салон"
            elif "библиотека" in query:
                room_num = "библиотека"
            elif "учителска" in query:
                room_num = "учителска стая"
            elif "директор" in query:
                room_num = "директор"
        return {
            "intent": "check_room",
            "recipient_name": None,
            "message_text": None,
            "room_number": room_num,
            "date": None
        }

    # 6. show_events
    if any(x in query for x in ["събитие", "събития", "концерт", "клуб", "клубове", "сбирка", "празник"]):
        return {
            "intent": "show_events",
            "recipient_name": None,
            "message_text": None,
            "room_number": None,
            "date": "today"
        }

    return {
        "intent": "unknown",
        "recipient_name": None,
        "message_text": None,
        "room_number": None,
        "date": None
    }


@app.post("/api/voice_command")
async def voice_command(request: VoiceCommandRequest, db: Session = Depends(get_db)):
    """
    Интеграция на AI 'Мозъка' на Сървъра.
    Приема текстова заявка (или Whisper изход) и разпознава намеренията (Intent).
    """
    query = request.text_query.lower().strip()
    
    # 1. Защита срещу неподходящи заявки
    inappropriate_keywords = [
        "тъп", "глупав", "скапан", "урод", "педераст", "курва", "шибан", "майна", "копеле", 
        "гей", "скапаняк", "боклук", "кучи", "кучка", "лайно", "лайна", "еба", "ебати", "еби"
    ]
    if any(w in query for w in inappropriate_keywords):
        sys_event = SystemEvent(
            event_type="question_asked",
            person_id=request.person_id,
            timestamp=datetime.utcnow(),
            metadata_json=json.dumps({
                "query": request.text_query,
                "intent": "blocked",
                "response": "Заявката е блокирана поради съдържание на неподходящ език."
            }, ensure_ascii=False)
        )
        db.add(sys_event)
        db.commit()
        
        return {
            "intent": "blocked",
            "query": request.text_query,
            "response": "Моля, поддържайте учтив тон и задавайте въпроси, свързани само с училището."
        }

    # 2. Парсиране на намерение (intent) и параметри
    parsed = None
    if llm_manager and (llm_manager.ollama_enabled or llm_manager.gemini_enabled):
        try:
            system_instruction = (
                "Ти си AI училищен асистент. Твоята задача е да анализираш въпрос/команда на български език и да я класифицираш в един от следните интенти (intents):\n"
                "1. 'leave_message' - когато потребителят иска да остави съобщение за някой друг (напр. 'Кажи на Иван...', 'Остави съобщение за г-жа Димитрова...', 'Ако ме търси Георги, му кажи...').\n"
                "2. 'check_messages' - когато потребителят проверява дали има нови съобщения за него (напр. 'Имам ли съобщения?', 'Има ли нещо за мен?').\n"
                "3. 'check_timetable' - когато потребителят проверява разписанието/часовете си (напр. 'Кой е следващият ми час?', 'Каква ми е програмата за днес?', 'Какви часове имам утре?').\n"
                "4. 'check_free_periods' - когато потребителят иска да провери за свободни часове/дупки в програмата си (напр. 'Кога имам свободен час днес?', 'Кога утре имам дупка?').\n"
                "5. 'check_room' - когато потребителят търси местоположението на кабинет или стая (напр. 'Къде е кабинет 304?', 'Къде се намира библиотеката?').\n"
                "6. 'show_events' - когато потребителят пита за събития, новини или училищни мероприятия (напр. 'Какви събития има днес?', 'Кога е концертът?').\n"
                "7. 'unknown' - ако въпросът не съвпада с нито един от горните.\n\n"
                "Трябва да върнеш единствено и само валиден JSON обект в следния формат, без никакви други обяснения, кавички или текст около него:\n"
                "{\n"
                "  \"intent\": \"leave_message\" | \"check_messages\" | \"check_timetable\" | \"check_free_periods\" | \"check_room\" | \"show_events\" | \"unknown\",\n"
                "  \"recipient_name\": \"<извлечено име на получател или null>\",\n"
                "  \"message_text\": \"<извлечен текст на съобщението или null>\",\n"
                "  \"room_number\": \"<извлечен номер на кабинет или име на зала или null>\",\n"
                "  \"date\": \"today\" | \"tomorrow\" | null\n"
                "}"
            )
            
            prompt = f"Анализирай тази заявка: '{request.text_query}'"
            response_json_str = llm_manager.generate(prompt, system_instruction)
            if response_json_str:
                # Почистване от markdown блокове, ако моделът е върнал ```json ... ```
                clean_json = response_json_str.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json[7:]
                if clean_json.endswith("```"):
                    clean_json = clean_json[:-3]
                clean_json = clean_json.strip()
                parsed = json.loads(clean_json)
        except Exception as e:
            # При грешка с LLM или парсирането, ще преминем към rule-based
            pass

    # Фолбек към rule-based парсер
    if not parsed or "intent" not in parsed:
        parsed = parse_intent_rule_based(query)

    intent = parsed.get("intent", "unknown")
    response_text = "Не успях да разбера въпроса Ви. Опитайте отново с други думи."
    
    # 3. Изпълнение на конкретната логика
    if intent == "leave_message":
        if not request.person_id:
            response_text = "Моля първо се идентифицирайте чрез бадж, за да знам кой оставя съобщението."
        else:
            sender = db.query(Person).filter(Person.id == request.person_id).first()
            recipient_name = parsed.get("recipient_name")
            message_text = parsed.get("message_text")
            
            if not recipient_name:
                response_text = "За кого е съобщението? Моля посочете име на получател."
            elif not message_text:
                response_text = f"Какво съобщение искате да оставите за {recipient_name}?"
            else:
                # Търсим получателя в базата
                clean_name = recipient_name.lower()
                for title in ["г-н", "г-за", "г-жа", "господин", "госпожа", "учител", "учителка"]:
                    clean_name = clean_name.replace(title, "").strip()
                
                candidates = db.query(Person).filter(Person.active == True).all()
                recipient = None
                for candidate in candidates:
                    cand_name_lower = candidate.full_name.lower()
                    if clean_name in cand_name_lower or cand_name_lower in clean_name:
                        recipient = candidate
                        break
                
                if recipient:
                    from datetime import timedelta
                    valid_until = datetime.now() + timedelta(hours=24)
                    msg = Message(
                        sender_id=sender.id,
                        recipient_id=recipient.id,
                        text=message_text,
                        valid_until=valid_until,
                        status="active"
                    )
                    db.add(msg)
                    
                    # Записваме и системно събитие за съобщението
                    sys_event_msg = SystemEvent(
                        event_type="message_created",
                        person_id=sender.id,
                        timestamp=datetime.utcnow(),
                        metadata_json=json.dumps({
                            "message_id": msg.id,
                            "recipient_id": recipient.id
                        })
                    )
                    db.add(sys_event_msg)
                    db.commit()
                    
                    response_text = f"Записах съобщението за {recipient.full_name}: '{message_text}'."
                else:
                    response_text = f"Не успях да намеря потребител с име {recipient_name} в базата данни."

    elif intent == "check_messages":
        if not request.person_id:
            response_text = "Моля първо се идентифицирайте чрез бадж, за да проверя съобщенията Ви."
        else:
            now = datetime.now()
            msgs = db.query(Message).filter(
                Message.recipient_id == request.person_id,
                Message.status == "active",
                Message.valid_until > now
            ).all()
            
            if msgs:
                msg_list = []
                for m in msgs:
                    msg_list.append(f"от {m.sender.full_name}: '{m.text}'")
                    m.status = "delivered"
                    m.delivered_at = datetime.utcnow()
                db.commit()
                response_text = f"Имате {len(msgs)} нови съобщения. " + ". ".join(msg_list)
            else:
                response_text = "Нямате нови съобщения."

    elif intent == "check_timetable":
        if not request.person_id:
            response_text = "Моля сканирайте баджа си, за да проверя разписанието Ви."
        else:
            date_param = parsed.get("date") or "today"
            from datetime import timedelta
            target_date = date.today()
            if date_param == "tomorrow":
                target_date = target_date + timedelta(days=1)
                
            is_next_query = any(x in query for x in ["следващ", "следващият", "следващо"])
            
            if is_next_query and target_date == date.today():
                current_time = datetime.now().time()
                next_class = db.query(Timetable).filter(
                    Timetable.person_id == request.person_id,
                    Timetable.date == target_date,
                    Timetable.start_time > current_time
                ).order_by(Timetable.start_time).first()
                
                if next_class:
                    response_text = f"Следващият Ви час е {next_class.subject} в {next_class.room} от {next_class.start_time.strftime('%H:%M')} ч."
                else:
                    response_text = "Нямате повече часове за днес."
            else:
                records = db.query(Timetable).filter(
                    Timetable.person_id == request.person_id,
                    Timetable.date == target_date
                ).order_by(Timetable.period).all()
                
                date_word = "утре" if date_param == "tomorrow" else "днес"
                if records:
                    class_list = []
                    for r in records:
                        class_list.append(f"{r.period}-ти час: {r.subject} в {r.room} ({r.start_time.strftime('%H:%M')}-{r.end_time.strftime('%H:%M')})")
                    response_text = f"Програмата Ви за {date_word} е: " + ", ".join(class_list)
                else:
                    response_text = f"Нямате часове за {date_word}."

    elif intent == "check_free_periods":
        if not request.person_id:
            response_text = "Моля сканирайте баджа си, за да проверя свободните Ви часове."
        else:
            date_param = parsed.get("date") or "today"
            from datetime import timedelta
            target_date = date.today()
            if date_param == "tomorrow":
                target_date = target_date + timedelta(days=1)
                
            records = db.query(Timetable).filter(
                Timetable.person_id == request.person_id,
                Timetable.date == target_date
            ).order_by(Timetable.period).all()
            
            date_word = "утре" if date_param == "tomorrow" else "днес"
            if not records:
                response_text = f"Нямате часове за {date_word}, така че целият учебен ден е свободен."
            else:
                periods_present = [r.period for r in records]
                min_p = min(periods_present)
                max_p = max(periods_present)
                
                gaps = []
                for p in range(min_p + 1, max_p):
                    if p not in periods_present:
                        gaps.append(p)
                
                if gaps:
                    gap_strs = [f"{g}-ти час" for g in gaps]
                    response_text = f"Имате свободен час ({date_word}) на: " + ", ".join(gap_strs) + "."
                else:
                    response_text = f"Нямате свободни часове (дупки) между часовете за {date_word}."

    elif intent == "check_room":
        room_number = parsed.get("room_number")
        if not room_number:
            response_text = "Кой кабинет или зала търсите? Моля кажете номера на кабинета."
        else:
            room_clean = str(room_number).lower().strip()
            rooms_dict = {
                "304": "Кабинет 304 се намира на третия етаж, дясно крило (кабинет по ИТ).",
                "302": "Кабинет 302 се намира на третия етаж, ляво крило (кабинет по Информационни технологии).",
                "201": "Кабинет 201 се намира на втория етаж, ляво крило (кабинет по Математика).",
                "104": "Кабинет 104 се намира на първия етаж, дясно крило (кабинет по Български език).",
                "физкултурен салон": "Физкултурният салон се намира в двора на училището, до спортната площадка.",
                "салон": "Физкултурният салон се намира в двора на училището, до спортната площадка.",
                "библиотека": "Библиотеката се намира на първия етаж, срещу главния вход.",
                "библиотеката": "Библиотеката се намира на първия етаж, срещу главния вход.",
                "учителска стая": "Учителската стая е на втория етаж, до кабинета на директора.",
                "учителската стая": "Учителската стая е на втория етаж, до кабинета на директора.",
                "директор": "Кабинетът на директора се намира на втория етаж, централно фоайе.",
                "директора": "Кабинетът на директора се намира на втория етаж, централно фоайе."
            }
            
            response_text = None
            for k, v in rooms_dict.items():
                if k in room_clean or room_clean in k:
                    response_text = v
                    break
            
            if not response_text:
                response_text = f"Не успях да намеря информация за кабинет или зала '{room_number}'. Моля попитайте на входа."

    elif intent == "show_events":
        today = date.today()
        from datetime import datetime as dt
        today_start = dt.combine(today, dt.min.time())
        today_end = dt.combine(today, dt.max.time())
        
        events = db.query(Event).filter(
            Event.start_time >= today_start,
            Event.start_time <= today_end
        ).order_by(Event.start_time).all()
        
        if events:
            event_list = []
            for e in events:
                event_list.append(f"'{e.title}' в {e.room} от {e.start_time.strftime('%H:%M')}")
            response_text = "Днес има следните събития: " + ", ".join(event_list) + "."
        else:
            response_text = "Няма планирани събития за днес."

    # Записваме въпроса като системно събитие
    sys_event = SystemEvent(
        event_type="question_asked",
        person_id=request.person_id,
        timestamp=datetime.utcnow(),
        metadata_json=json.dumps({
            "query": request.text_query,
            "intent": intent,
            "response": response_text
        }, ensure_ascii=False)
    )
    db.add(sys_event)
    db.commit()
    
    return {
        "intent": intent,
        "query": request.text_query,
        "response": response_text
    }


def state_event_handler(event_type, data):

    """ Калбек функция, която се вика от StateManager """
    # Тъй като FastAPI работи с asyncio, трябва да изпратим съобщението в event loop-а
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    message = json.dumps({"type": event_type, "data": data})
    
    # Тъй като сме в друга нишка, използваме специален начин за broadcast
    # В реална продукция бихме използвали redis/pub-sub, но тук ще ползваме глобалния мениджър
    # с малко по-прост подход за демонстрацията.
    
    # Най-лесният начин в рамките на FastAPI/Uvicorn нишка е да ползваме глобален цикъл:
    # За целта ще трябва да преработим леко начина на broadcast.
    pass

# Преработваме state_event_handler за работа с глобалния loop на FastAPI
def run_server(manager_ref, fm, host="0.0.0.0", port=5000):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm

    # Дефинираме калбека тук, за да има достъп до manager и asyncio loop
    def sync_event_handler(event_type, data):
        message = json.dumps({"type": event_type, "data": data})
        # Използваме broadcast, но трябва да сме сигурни, че става в asyncio контекст
        # FastAPI/Uvicorn ще създаде loop при стартиране.
        # За по-голяма стабилност в нишки, ползваме:
        # (Този калбек се вика от StateManager нишката)
        pass

    # Интегрираме калбека в StateManager
    # state_manager.set_event_callback(sync_event_handler)
    
    uvicorn.run(app, host=host, port=port, log_level="error")

# По-добър подход за нишкова безопасност с WebSockets във FastAPI:
def start_web_server(manager_ref, fm):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm

    # Калбекът, който StateManager ще вика
    def on_state_change(event_type, data):
        if manager.active_connections:
            # Тъй като сме в нишка на StateManager, а WebSocket broadcast трябва да е асинхронен
            # ще използваме asyncio.run_coroutine_threadsafe ако имаме достъп до loop-а.
            # Но FastAPI/Uvicorn крият loop-а. 
            # Алтернатива: ползваме прост broadcast метод, който не е awaitable или ползваме вътрешен loop.
            
            payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
            
            # Тъй като uvicorn/fastapi вървят в своя нишка, най-лесният начин е 
            # да направим broadcast-а нишково безопасен.
            for ws in manager.active_connections:
                asyncio.run_coroutine_threadsafe(ws.send_text(payload), app.loop)

    # Добавяме loop към app за лесен достъп (ще го сетнем при старт)
    server_thread = threading.Thread(target=run_server_thread, args=(manager_ref, fm), daemon=True)
    server_thread.start()
    return server_thread

def run_server_thread(manager_ref, fm):
    global state_manager, face_manager
    state_manager = manager_ref
    face_manager = fm
    
    # Ре регистрираме калбека
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
            _main_loop.call_soon_threadsafe(
                asyncio.create_task, ws.send_text(payload)
            )
