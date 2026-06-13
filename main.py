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
                                if self.state_manager:
                                    self.state_manager.on_face_recognized("Unknown", image_filename=img_file)

                # Обновяваме брояча за непознати
                unknown_count = face_names.count("Unknown")
                self.people_counter.update_unknowns(unknown_count)

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


class UIManager:
    """ Управлява предварително рендерираните графични активи за максимална производителност """
    def __init__(self, target_width, target_height):
        self.target_width = target_width
        self.target_height = target_height
        self.assets = {}
        self.last_status = None
        self.last_time_str = None
        self.last_count = -1
        
        # Данни за визуализация "Невронна мрежа"
        self.neurons = []
        for _ in range(8):
            self.neurons.append({
                "x": np.random.randint(0, 100), 
                "y": np.random.randint(0, 100),
                "vx": np.random.uniform(-1, 1),
                "vy": np.random.uniform(-1, 1)
            })
        
        # Система за известия
        self.notification_text = ""
        self.notification_expiry = 0
        self.notification_asset = None
        self.notification_mask = None
        
        # Предварително рендериране на статични компоненти
        self._pre_render_static_elements()

    def _pre_render_static_elements(self):
        """ Рендерира веднъж елементите, които никога не се променят """
        # 1. Горна HUD лента (основа)
        hud_h = 80
        hud_base = np.zeros((hud_h, self.target_width, 3), dtype=np.uint8)
        cv2.rectangle(hud_base, (0, 0), (self.target_width, hud_h), (30, 30, 30), -1)
        self.assets['hud_base'] = hud_base

        # 2. SCHOOL AI Текст
        self.assets['title'], self.assets['title_mask'] = self._render_text_asset(
            "SCHOOL AI", FONT_MAIN, (255, 255, 0), (300, 80), (20, 18)
        )

        # 3. Долен панел (основа)
        panel_w, panel_h = 380, 60
        panel_base = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)
        cv2.rectangle(panel_base, (0, 0), (panel_w, panel_h), (20, 20, 20), -1)
        cv2.line(panel_base, (0, 0), (panel_w, 0), (255, 255, 0), 2) # Cyan border
        self.assets['panel_base'] = panel_base

    def _render_text_asset(self, text, font, color_rgb, size, position=(0, 0)):
        """ Помощна функция за рендериране на PIL текст в OpenCV формат с маска """
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text(position, text, font=font, fill=(*color_rgb, 255))
        
        # Конвертираме към numpy array директно
        data = np.array(img)
        rgb = cv2.cvtColor(data[:, :, :3], cv2.COLOR_RGB2BGR)
        mask = data[:, :, 3] > 0
        return rgb, mask

    def show_notification(self, name, duration=3):
        """ Задава ново известие за разпознат човек """
        if not name: return
        
        if name == "Unknown" or name == "Непознат":
            text = "ЗАСЕЧЕН Е НЕПОЗНАТ"
            color = (200, 200, 200)
        else:
            text = f"РАЗПОЗНАТ: {name.upper()}"
            color = (0, 255, 255) # Cyan

        if text != self.notification_text:
            self.notification_text = text
            try:
                tw = int(FONT_MAIN.getlength(text))
            except: tw = 400
            self.notification_asset, self.notification_mask = self._render_text_asset(
                text, FONT_MAIN, color, (tw + 20, 50)
            )
        
        self.notification_expiry = time.time() + duration

    def draw(self, frame, face_data, is_processing, people_counter):
        h_orig, w_orig = frame.shape[:2]
        
        # Винаги изчисляваме мащаба, за да напаснем координатите на лицата
        scale_x = self.target_width / w_orig
        scale_y = self.target_height / h_orig

        # Преоразмеряваме кадъра, ако не съвпада с HUD активите
        if w_orig != self.target_width or h_orig != self.target_height:
            frame = cv2.resize(frame, (self.target_width, self.target_height), interpolation=cv2.INTER_LINEAR)
            h, w = self.target_height, self.target_width
        else:
            h, w = h_orig, w_orig

        # Обновяваме известията
        for _, name in face_data:
            self.show_notification(name)

        # 1. Горна HUD лента
        roi_hud = frame[0:80, 0:w]
        cv2.addWeighted(self.assets['hud_base'], 0.6, roi_hud, 0.4, 0, roi_hud)

        # 2. SCHOOL AI Заглавие
        title_h, title_w = self.assets['title'].shape[:2]
        mask_title = self.assets['title_mask']
        frame[0:title_h, 0:title_w][mask_title] = self.assets['title'][mask_title]

        # 3. Интелигентно известие
        current_time = time.time()
        if current_time < self.notification_expiry and self.notification_asset is not None:
            n_h, n_w = self.notification_asset.shape[:2]
            start_x = 320 
            if start_x + n_w < w - 320:
                roi_notif = frame[18:18+n_h, start_x:start_x+n_w]
                roi_notif[self.notification_mask] = self.notification_asset[self.notification_mask]

        # 4. Динамичен часовник
        time_str = datetime.now().strftime("TIME: %H:%M:%S")
        if time_str != self.last_time_str:
            self.assets['time'], self.assets['time_mask'] = self._render_text_asset(
                time_str, FONT_MAIN, (255, 255, 255), (290, 50)
            )
            self.last_time_str = time_str

        t_h, t_w = self.assets['time'].shape[:2]
        start_x_time = w - t_w - 20
        frame[18:18+t_h, start_x_time:start_x_time+t_w][self.assets['time_mask']] = self.assets['time'][self.assets['time_mask']]

        # 5. Статус на системата
        if is_processing != self.last_status:
            status_text = "STATUS: ACTIVE" if is_processing else "STATUS: PAUSED"
            status_color = (0, 255, 0) if is_processing else (255, 0, 0)
            try: sw = int(FONT_MAIN.getlength(status_text))
            except: sw = 300
            self.assets['status'], self.assets['status_mask'] = self._render_text_asset(
                status_text, FONT_MAIN, status_color, (sw + 20, 50)
            )
            self.last_status = is_processing

        s_h, s_w = self.assets['status'].shape[:2]
        start_x_status = start_x_time - s_w - 60
        frame[18:18+s_h, start_x_status:start_x_status+s_w][self.assets['status_mask']] = self.assets['status'][self.assets['status_mask']]

        # 6. Долен панел
        count = people_counter.get_count()
        px, py = 15, h - 75
        roi_panel = frame[py:py+60, px:px+380]
        cv2.addWeighted(self.assets['panel_base'], 0.7, roi_panel, 0.3, 0, roi_panel)

        if count != self.last_count:
            count_text = f"ЗАСЕЧЕНИ ДНЕС: {count}"
            self.assets['count'], self.assets['count_mask'] = self._render_text_asset(
                count_text, FONT_COUNTER_TITLE, (0, 255, 255), (360, 50)
            )
            self.last_count = count

        c_h, c_w = self.assets['count'].shape[:2]
        frame[py+12:py+12+c_h, px+12:px+12+c_w][self.assets['count_mask']] = self.assets['count'][self.assets['count_mask']]

        # 7. Рамки около лицата и ИМЕНА (на Кирилица)
        if 'name_labels' not in self.assets:
            self.assets['name_labels'] = {} # Кеш за рендерирани имена

        for (top_orig, right_orig, bottom_orig, left_orig), name in face_data:
            # Мащабираме координатите към текущия размер на екрана
            top = int(top_orig * scale_y)
            right = int(right_orig * scale_x)
            bottom = int(bottom_orig * scale_y)
            left = int(left_orig * scale_x)

            color = (0, 255, 255) if name != "Unknown" else (200, 200, 200)
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
            
            # Използваме кеширано изображение за името
            label = name.upper()
            if label not in self.assets['name_labels']:
                # Рендерираме името веднъж и го кешираме
                try: tw = int(FONT_SMALL.getlength(label))
                except: tw = len(label) * 20
                self.assets['name_labels'][label] = self._render_text_asset(
                    label, FONT_SMALL, color, (tw + 10, 50)
                )
            
            label_img, label_mask = self.assets['name_labels'][label]
            lh, lw = label_img.shape[:2]
            
            # Позиционираме името под рамката
            lx = left
            ly = bottom + 10
            
            if ly + lh < h and lx + lw < w:
                roi_label = frame[ly:ly+lh, lx:lx+lw]
                roi_label[label_mask] = label_img[label_mask]

        # 8. Пулсиращ индикатор за "мислене"
        if is_processing:
            self._draw_thinking_indicator(frame, w, h)

        return frame

    def _draw_thinking_indicator(self, frame, w, h):
        """ Рисува динамична "кибер-невронна мрежа" в долния десен ъгъл при обработка """
        # Параметри на визуализацията (увеличени)
        center_x, center_y = w - 150, h - 150
        neuron_radius = 4
        connection_threshold = 100
        area_size = 150 # Размер на зоната на движение
        
        # Обновяваме позициите
        for n in self.neurons:
            n["x"] += n["vx"]
            n["y"] += n["vy"]
            
            # Отскачане от границите на зоната
            if n["x"] < 0 or n["x"] > area_size: n["vx"] *= -1
            if n["y"] < 0 or n["y"] > area_size: n["vy"] *= -1
        
        # Рисуваме връзките (Сини/Циано: (255, 255, 0))
        for i in range(len(self.neurons)):
            for j in range(i + 1, len(self.neurons)):
                n1 = self.neurons[i]
                n2 = self.neurons[j]
                
                # Позиции в рамките на кадъра
                p1 = (int(center_x + n1["x"] - area_size/2), int(center_y + n1["y"] - area_size/2))
                p2 = (int(center_x + n2["x"] - area_size/2), int(center_y + n2["y"] - area_size/2))
                
                dist = math.sqrt((n1["x"] - n2["x"])**2 + (n1["y"] - n2["y"])**2)
                
                if dist < connection_threshold:
                    cv2.line(frame, p1, p2, (255, 255, 0), 1)
        
        # Рисуваме невроните (точките) - Зелени: (0, 255, 0)
        for n in self.neurons:
            pos = (int(center_x + n["x"] - area_size/2), int(center_y + n["y"] - area_size/2))
            cv2.circle(frame, pos, neuron_radius, (0, 255, 0), -1)

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

    # Инициализиране на UI мениджъра с целевата резолюция
    ui_manager = UIManager(Config.TARGET_WIDTH, Config.TARGET_HEIGHT)

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
