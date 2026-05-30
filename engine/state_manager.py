import threading
import cv2

class StateManager:
    """ 
    Централизиран мениджър на състоянието за синхронизация между 
    OpenCV цикъла и FastAPI уеб сървъра.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_frame = None
        self._is_running = True
        self._is_processing = True
        self._people_counter = None
        self._system_status = "Initializing"
        self._on_event_callback = None # Callback function for WebSockets

    def set_event_callback(self, callback):
        """ Задава функция, която да се вика при промяна на състоянието """
        with self._lock:
            self._on_event_callback = callback

    def _notify(self, event_type: str, data: dict):
        """ Помощен метод за изпращане на известия """
        if self._on_event_callback:
            # Извикваме калбека в отделна нишка, за да не бавим обработката
            threading.Thread(target=self._on_event_callback, args=(event_type, data), daemon=True).start()

    def update_frame(self, frame):
        """ Записва най-новия кадър от камерата (оптимизиран за уеб стрийминг) """
        with self._lock:
            # 1. Намаляваме резолюцията за уеб стрийма (напр. до 720p), за да спестим трафик
            h, w = frame.shape[:2]
            target_h = 720
            if h > target_h:
                scale = target_h / h
                frame_web = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            else:
                frame_web = frame

            # 2. Кодираме с по-висока компресия (Quality: 40)
            # Това драстично намалява Mbps без голяма загуба на видимо качество
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 90]
            ret, buffer = cv2.imencode('.jpg', frame_web, encode_param)
            if ret:
                self._latest_frame = buffer.tobytes()

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
