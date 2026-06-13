import threading
import cv2
import time
import queue

class StateManager:
    """ 
    Централизиран мениджър на състоянието за синхронизация между 
    OpenCV цикъла и FastAPI уеб сървъра.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_frame = None
        self._pending_frame = None
        self._is_running = True
        self._is_processing = True
        self._people_counter = None
        self._system_status = "Initializing"
        self._on_event_callback = None # Callback function for WebSockets
        self._active_streams = 0
        self._last_frame_time = 0
        
        # Опашка за събития (WebSocket известия)
        self._event_queue = queue.Queue()
        self._event_thread = threading.Thread(target=self._event_worker, daemon=True)
        self._event_thread.start()

        # Оптимизация на уеб стрийминга
        self._new_frame_event = threading.Event()
        self._encoding_thread = threading.Thread(target=self._encoding_worker, daemon=True)
        self._encoding_thread.start()

    def set_event_callback(self, callback):
        """ Задава функция, която да се вика при промяна на състоянието """
        with self._lock:
            self._on_event_callback = callback

    def _event_worker(self):
        """ Единствена нишка за обработка на всички известия """
        while self._is_running:
            try:
                event_type, data = self._event_queue.get(timeout=1.0)
                if self._on_event_callback:
                    try:
                        self._on_event_callback(event_type, data)
                    except Exception:
                        pass
                self._event_queue.task_done()
            except queue.Empty:
                continue

    def _notify(self, event_type: str, data: dict):
        """ Помощен метод за добавяне на известия в опашката """
        self._event_queue.put((event_type, data))

    def increment_active_streams(self):
        """ Увеличава броя на активните уеб стриймове """
        with self._lock:
            self._active_streams += 1

    def decrement_active_streams(self):
        """ Намалява броя на активните уеб стриймове """
        with self._lock:
            self._active_streams = max(0, self._active_streams - 1)

    def has_active_streams(self):
        """ Връща дали има поне един активен уеб стрийм """
        with self._lock:
            return self._active_streams > 0

    def _encoding_worker(self):
        """ Фонова нишка за JPEG компресия """
        while self._is_running:
            # Чакаме за нов кадър или сигнал за изход
            if not self._new_frame_event.wait(timeout=1.0):
                continue
            
            frame = None
            with self._lock:
                # Вземаме последния чакащ кадър САМО ако има активни зрители
                if self._pending_frame is not None and self._active_streams > 0:
                    frame = self._pending_frame
                    self._pending_frame = None
                self._new_frame_event.clear()

            if frame is not None:
                try:
                    # 1. Намаляваме резолюцията за уеб стрийма (720p)
                    h, w = frame.shape[:2]
                    target_h = 720
                    if h > target_h:
                        scale = target_h / h
                        frame_web = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    else:
                        frame_web = frame

                    # 2. Кодираме с по-висока компресия (Quality: 70)
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
                    ret, buffer = cv2.imencode('.jpg', frame_web, encode_param)
                    if ret:
                        with self._lock:
                            self._latest_frame = buffer.tobytes()
                except Exception:
                    pass

    def update_frame(self, frame):
        """ Записва новия кадър за фонова обработка """
        with self._lock:
            # Ако никой не гледа, не правим нищо
            if self._active_streams <= 0:
                return
            
            # Ограничаваме стрийма до ~15 FPS
            current_time = time.time()
            if current_time - self._last_frame_time < 0.066:
                return
            self._last_frame_time = current_time
            
            # Само запазваме суровия кадър и събуждаме работника
            self._pending_frame = frame.copy()
            self._new_frame_event.set()

    def get_latest_frame(self):
        """ Връща последния JPEG кадър за стрийминг """
        with self._lock:
            return self._latest_frame

    def set_processing(self, state: bool):
        """ Пуска или спира обработката (анализа на лица) """
        with self._lock:
            self._is_processing = state
        self._notify("status_change", {"is_processing": state})

    def on_face_recognized(self, name: str):
        """ Вика се при успешно разпознаване на лице """
        self._notify("recognition", {
            "name": name, 
            "total": self._people_counter.get_count() if self._people_counter else 0
        })

    def on_speech_ready(self, audio_filename: str):
        """ Вика се когато аудио файлът е генериран и готов за пускане """
        self._notify("speech", {"url": f"/audio/{audio_filename}"})

    def is_processing(self):
        with self._lock:
            return self._is_processing

    def set_people_counter(self, counter):
        """ Свързва брояча на хора за достъп до статистика """
        with self._lock:
            self._people_counter = counter

    def get_stats(self):
        """ Връща текущата статистика за уеб интерфейса """
        if self._people_counter:
            return {
                "total": self._people_counter.get_count(),
                "is_processing": self.is_processing(),
                "status": self._system_status
            }
        return {"total": 0, "is_processing": self.is_processing(), "status": self._system_status}

    def set_status(self, status: str):
        with self._lock:
            self._system_status = status

    def stop_system(self):
        with self._lock:
            self._is_running = False

    def should_continue(self):
        with self._lock:
            return self._is_running
