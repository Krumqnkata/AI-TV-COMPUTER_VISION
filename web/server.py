import uvicorn
import os
import json
import asyncio
from fastapi import FastAPI, Response, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import threading
import time
from typing import List

app = FastAPI(title="School AI Control Panel")
app.mount("/audio", StaticFiles(directory="data/audio_cache"), name="audio")
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
    while True:
        if state_manager:
            frame = state_manager.get_latest_frame()
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.07) # Ограничаваме до ~14 FPS за пестене на трафик

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
