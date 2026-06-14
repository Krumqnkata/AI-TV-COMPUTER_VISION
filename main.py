import cv2
import numpy as np
import time
import math
import threading
import os
import warnings
import hashlib
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# Заглушаване на досадни предупреждения от библиотеки на Google/MediaPipe
warnings.filterwarnings("ignore", category=UserWarning, module="google.protobuf.symbol_database")

from utils.config import Config
from engine.face_manager import FaceManager
from engine.tts_manager import TTSManager
from engine.people_counter import DailyPeopleCounter
from utils.logger import log_system
from engine.state_manager import StateManager
from web.server import start_web_server
from engine.ui_manager import UIManager

# ... (rest of the initial constants and FaceRecognitionWorker remain the same)

# Цветове (BGR формат за OpenCV)
COLOR_CYAN = (255, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (0, 0, 0)
COLOR_NEON_GREEN = (0, 255, 0)

# Глобален кеш за шрифтове за избягване на скъпото четене от диск всеки кадър (оразмерени за Full HD)
FONT_PATH = Config.FONT_PATH
try:
    FONT_MAIN = ImageFont.truetype(FONT_PATH, 38)
    FONT_SMALL = ImageFont.truetype(FONT_PATH, 42)
except Exception:
    FONT_MAIN = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

try:
    FONT_COUNTER = ImageFont.truetype(FONT_PATH, 30)
    FONT_COUNTER_TITLE = ImageFont.truetype(FONT_PATH, 34)
except Exception:
    FONT_COUNTER = ImageFont.load_default()
    FONT_COUNTER_TITLE = ImageFont.load_default()

class FaceRecognitionWorker:
    """ Работник във фонов режим за разпознаване на лица без блокиране на GUI нишката """
    def __init__(self, face_manager, tts_manager, people_counter, state_manager=None):
        self.face_manager = face_manager
        self.tts_manager = tts_manager
        self.people_counter = people_counter
        self.state_manager = state_manager
        self.frame_to_process = None
        self.face_data = []
        self.face_history = {} # {name: {"count": count, "grace": grace_frames}}
        self.persistence_threshold = Config.PERSISTENCE_THRESHOLD
        self.grace_limit = Config.GRACE_LIMIT
        self.lock = threading.Lock()
        self.new_frame_event = threading.Event()
        self.running = True
        
        # Визуална история
        self.history_dir = "data/history_cache"
        self.max_history = Config.MAX_HISTORY
        if not os.path.exists(self.history_dir):
            os.makedirs(self.history_dir)

        self.thread = threading.Thread(target=self._worker_loop, daemon=True)

    def start(self):
        self.thread.start()

    def _save_to_history(self, frame, bbox, name):
        """ Изрязва и запазва малка снимка на лицето в историята """
        try:
            top, right, bottom, left = bbox
            # Изрязваме лицето с малък марж
            h, w = frame.shape[:2]
            face_crop = frame[max(0, top-20):min(h, bottom+20), max(0, left-20):min(w, right+20)]
            
            if face_crop.size > 0:
                # Мащабираме до стандартен размер
                face_thumb = cv2.resize(face_crop, (150, 150), interpolation=cv2.INTER_AREA)
                
                # Генерираме име на файла (timestamp + hash_of_name.jpg)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                name_hash = hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
                filename = f"{timestamp}_{name_hash}.jpg"
                filepath = os.path.join(self.history_dir, filename)
                
                cv2.imwrite(filepath, face_thumb)
                self._purge_old_history()
                return filename
        except Exception as e:
            log_system(f"Failed to save history image: {e}", "error")
        return None

    def _purge_old_history(self):
        """ Изтрива най-старите файлове, ако са над лимита (FIFO) """
        try:
            files = [os.path.join(self.history_dir, f) for f in os.listdir(self.history_dir)]
            # Сортираме по време на промяна (най-старите първо)
            files.sort(key=os.path.getmtime)
            
            while len(files) > self.max_history:
                oldest_file = files.pop(0)
                if os.path.exists(oldest_file):
                    os.remove(oldest_file)
        except Exception:
            pass

    def _worker_loop(self):
        while self.running:
            # Чакаме сигнал за нов кадър или спиране
            if not self.new_frame_event.wait(timeout=1.0):
                continue
                
            frame = None
            with self.lock:
                if self.frame_to_process is not None:
                    frame = self.frame_to_process
                    self.frame_to_process = None
                self.new_frame_event.clear()

            if frame is not None:
                # Всичко се обработва вътре в face_manager (вече връща и настроения)
                face_locations, face_names, face_moods = self.face_manager.identify_face(frame, resize_factor=0.25)

                temp_face_data = []
                current_names_in_frame = set(face_names)
                
                # 1. Обновяваме историята за всички имена
                # Първо намаляваме гратисния период на тези, които ги няма в текущия кадър
                for name in list(self.face_history.keys()):
                    if name not in current_names_in_frame:
                        self.face_history[name]["grace"] -= 1
                        if self.face_history[name]["grace"] <= 0:
                            del self.face_history[name]

                # 2. Обработваме имената от текущия кадър
                for (top, right, bottom, left), name, mood in zip(face_locations, face_names, face_moods):
                    # СТАБИЛИЗАЦИЯ: Ако сме разпознали "Unknown", но наскоро сме виждали някой познат
                    # и той все още е в активен "grace" период, не бързаме да го сменяме с "Unknown"
                    is_unknown = (name == self.face_manager._get_mapped_name("Unknown"))
                    if is_unknown:
                        # Търсим дали има някой познат, който все още се пази в историята и е стабилен
                        known_candidates = [n for n, d in self.face_history.items() if n != name and d["grace"] > (self.grace_limit // 2)]
                        if known_candidates:
                            name = known_candidates[0] # "Прилепваме" към познатия човек
                            is_unknown = False

                    if name not in self.face_history:
                        self.face_history[name] = {"count": 0, "grace": self.grace_limit}
                    
                    data = self.face_history[name]
                    data["count"] += 1
                    data["grace"] = self.grace_limit # Рестартираме гратисния период, щом го виждаме
                    
                    # Само ако е засечено достатъчно пъти, го показваме/обработваме
                    if data["count"] >= self.persistence_threshold:
                        temp_face_data.append(((top, right, bottom, left), name))
                        
                        # Ако засичаме това име ЗА ПЪРВИ ПЪТ (или след рестарт)
                        if data["count"] == self.persistence_threshold:
                            # Запазваме в историята
                            img_file = self._save_to_history(frame, (top, right, bottom, left), name)
                            
                            # Регистрираме и поздравяваме
                            if name != "Unknown":
                                self.people_counter.register(name)
                                self.tts_manager.speak_joke(name, mood=mood)
                                if self.state_manager:
                                    self.state_manager.on_face_recognized(name, image_filename=img_file)
                            elif img_file:
                                # Регистрираме уникален Unknown
                                unique_id = f"Unknown_{img_file}"
                                self.people_counter.register(unique_id)
                                
                                if self.state_manager:
                                    self.state_manager.on_face_recognized("Unknown", image_filename=img_file)

                # Обновяваме брояча за непознати - вече не е нужно, тъй като регистрираме всеки Unknown индивидуално

                with self.lock:
                    self.face_data = temp_face_data
            
    def submit_frame(self, frame):
        with self.lock:
            self.frame_to_process = frame
            self.new_frame_event.set()

    def get_face_data(self):
        with self.lock:
            return self.face_data

    def clear_face_data(self):
        with self.lock:
            self.face_data = []



def main():
    log_system("STARTING CYBER-HUD INTERFACE (HD QUALITY)")
    
    # Инициализиране на мениджъра на състоянието
    state_manager = StateManager()
    
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS, state_manager)
    
    # Стартиране на уеб сървъра
    start_web_server(state_manager, face_manager)
    log_system("Web Control Panel active at http://localhost:5000")
    
    source = Config.CAMERA_SOURCE
    is_ip_camera = isinstance(source, str)

    video_capture = cv2.VideoCapture(source)
    
    if not is_ip_camera:
        video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, Config.TARGET_WIDTH)
        video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, Config.TARGET_HEIGHT)

    if not video_capture.isOpened():
        log_system("ERROR: Camera not found!", "error")
        return

    # Настройка за FULLSCREEN
    win_name = 'SCHOOL AI - CYBER HUD'
    cv2.namedWindow(win_name, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    log_system("System active. Press 'q' or 'ESC' to exit.")

    people_counter = DailyPeopleCounter()
    state_manager.set_people_counter(people_counter)

    # Инициализиране на UI мениджъра с целевата резолюция и шрифтове
    ui_manager = UIManager(Config.TARGET_WIDTH, Config.TARGET_HEIGHT, FONT_MAIN, FONT_SMALL, FONT_COUNTER_TITLE)

    recognition_worker = FaceRecognitionWorker(face_manager, tts_manager, people_counter, state_manager)
    recognition_worker.start()

    frame_count = 0
    process_every_n_frames = Config.PROCESS_EVERY_N_FRAMES
    
    state_manager.set_status("Running")
    last_frame_time = time.time()
    reconnect_cooldown = 0

    while state_manager.should_continue():
        ret, frame = video_capture.read()
        
        if not ret:
            current_time = time.time()
            if current_time - last_frame_time > 5.0 and current_time > reconnect_cooldown:
                log_system("Camera stream frozen. Attempting to reconnect...", "error")
                video_capture.release()
                time.sleep(2)
                video_capture = cv2.VideoCapture(source)
                if not is_ip_camera:
                    video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, Config.TARGET_WIDTH)
                    video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, Config.TARGET_HEIGHT)
                
                reconnect_cooldown = time.time() + 5.0
                if video_capture.isOpened():
                    log_system("Reconnected successfully.")
                else:
                    log_system("Reconnect failed. Will try again.", "error")
            
            time.sleep(0.1)
            continue

        # Успешно прочетен кадър - обновяваме таймера
        last_frame_time = time.time()
        is_processing = state_manager.is_processing()

        # НЕ ЪПСКЕЙЛВАМЕ ТУК - UIManager ще го направи само при рисуване на HUD-а

        if is_processing:
            if frame_count % process_every_n_frames == 0:
                recognition_worker.submit_frame(frame.copy())
            face_data = recognition_worker.get_face_data()
        else:
            recognition_worker.clear_face_data()
            face_data = []

        # Рисуването и ъпскейла са вътре в UI мениджъра
        frame = ui_manager.draw(frame, face_data, is_processing, people_counter)
        
        # Обновяваме кадъра за уеб стрийминга
        state_manager.update_frame(frame)
        
        cv2.imshow(win_name, frame)
        frame_count += 1

        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '):
            state_manager.set_processing(not state_manager.is_processing())
            log_system(f"System {'ACTIVE' if state_manager.is_processing() else 'PAUSED'}")
        
        if key == ord('q') or key == 27 or cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
            log_system("System stopped by user.")
            state_manager.stop_system()
            break

    recognition_worker.running = False
    video_capture.release()
    cv2.destroyAllWindows()
    log_system("Goodbye!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_system(f"Critical error: {e}", "error")
