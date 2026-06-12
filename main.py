import cv2
import numpy as np
import time
import threading
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from utils.config import Config
from engine.face_manager import FaceManager
from engine.tts_manager import TTSManager
from engine.people_counter import DailyPeopleCounter
from utils.logger import log_system
from engine.state_manager import StateManager
from web.server import start_web_server

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
        self.face_history = {} # {name: count}
        self.persistence_threshold = 3 # Минимум засичания за потвърждение
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)

    def start(self):
        self.thread.start()

    def _worker_loop(self):
        while self.running:
            frame = None
            with self.lock:
                if self.frame_to_process is not None:
                    frame = self.frame_to_process
                    self.frame_to_process = None

            if frame is not None:
                # Всичко се обработва вътре в face_manager
                face_locations, face_names = self.face_manager.identify_face(frame, resize_factor=0.25)

                temp_face_data = []
                current_names = set(face_names)
                unknown_in_frame = 0
                
                # Обновяваме историята за стабилност
                new_history = {}
                
                for (top, right, bottom, left), name in zip(face_locations, face_names):
                    # Броим засичанията за всяко име
                    count = self.face_history.get(name, 0) + 1
                    new_history[name] = count
                    
                    # Само ако е засечено достатъчно пъти, го показваме/обработваме
                    if count >= self.persistence_threshold:
                        temp_face_data.append(((top, right, bottom, left), name))
                        
                        if name != "Unknown":
                            self.people_counter.register(name)
                            self.tts_manager.speak_joke(name)
                            if self.state_manager:
                                self.state_manager.on_face_recognized(name)
                        else:
                            unknown_in_frame += 1
                
                self.face_history = new_history

                # Обновяваме брояча за непознати (брои само НОВИ появявания)
                self.people_counter.update_unknowns(unknown_in_frame)

                with self.lock:
                    self.face_data = temp_face_data

                # ДИНАМИЧНО ПРИСПИВАНЕ (CPU COOLDOWN):
                # Наличието на лица стартира тежки математически изчисления за генериране на 128D вектори.
                # Заспиването тук освобождава ресурси за основната графична нишка, премахвайки микро-насичанията.
                if face_locations:
                    time.sleep(0.4)  # Даваме глътка въздух на CPU-то, ако има лица
                else:
                    time.sleep(0.1)  # Малко забавяне, за да спестим цикли, когато е празно
            else:
                time.sleep(0.02)

    def submit_frame(self, frame):
        with self.lock:
            # Презаписваме само най-новия кадър, избягвайки опашка и закъснение
            self.frame_to_process = frame

    def get_face_data(self):
        with self.lock:
            return self.face_data

    def clear_face_data(self):
        with self.lock:
            self.face_data = []

def _render_pil_text_on_frame(frame, text, position, font, color_rgb):
    """
    Помощна функция: рисува антиалиасиран текст чрез PIL върху OpenCV кадър.
    Рисува САМО върху малък ROI около текста, за да не конвертираме целия кадър.
    color_rgb е кортеж (R, G, B).
    """
    x, y = position
    height, width = frame.shape[:2]

    # Изчисляваме размера на текста за ROI
    try:
        bbox = font.getbbox(text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_y_offset = bbox[1]  # Горен отстъп на глифовете
    except Exception:
        text_w = len(text) * 22
        text_h = 40
        text_y_offset = 0

    # Определяме ROI с малък padding
    pad = 5
    roi_x1 = max(0, x - pad)
    roi_y1 = max(0, y - pad)
    roi_x2 = min(width, x + text_w + pad)
    roi_y2 = min(height, y + text_h + pad)

    if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
        return  # Текстът е извън екрана

    # Изрязваме ROI, конвертираме само него към PIL
    roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]
    pil_roi = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_roi)

    # Позиция на текста вътре в ROI
    local_x = x - roi_x1
    local_y = y - roi_y1 - text_y_offset
    draw.text((local_x, local_y), text, font=font, fill=color_rgb)

    # Връщаме PIL обратно в OpenCV формат
    frame[roi_y1:roi_y2, roi_x1:roi_x2] = cv2.cvtColor(np.array(pil_roi), cv2.COLOR_RGB2BGR)


class UIManager:
    """ Управлява предварително рендерираните графични активи за максимална производителност """
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.assets = {}
        self.last_status = None
        self.last_time_str = None
        self.last_count = -1
        
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
        hud_base = np.zeros((hud_h, self.width, 3), dtype=np.uint8)
        cv2.rectangle(hud_base, (0, 0), (self.width, hud_h), (30, 30, 30), -1)
        self.assets['hud_base'] = hud_base

        # 2. SCHOOL AI Текст (PIL рендериране веднъж)
        title_img = Image.new("RGBA", (300, 80), (0, 0, 0, 0))
        draw = ImageDraw.Draw(title_img)
        draw.text((20, 18), "SCHOOL AI", font=FONT_MAIN, fill=(255, 255, 0, 255)) # Cyan
        self.assets['title'] = cv2.cvtColor(np.array(title_img.convert("RGB")), cv2.COLOR_RGB2BGR)
        self.assets['title_mask'] = np.array(title_img)[:, :, 3] > 0

        # 3. Долен панел (основа)
        panel_w, panel_h = 380, 60
        panel_base = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)
        cv2.rectangle(panel_base, (0, 0), (panel_w, panel_h), (20, 20, 20), -1)
        cv2.line(panel_base, (0, 0), (panel_w, 0), (255, 255, 0), 2) # Cyan border
        self.assets['panel_base'] = panel_base

    def _render_text_asset(self, text, font, color_rgb, size):
        """ Помощна функция за рендериране на PIL текст в OpenCV формат """
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((0, 0), text, font=font, fill=(*color_rgb, 255))
        cv_img = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
        mask = np.array(img)[:, :, 3] > 0
        return cv_img, mask

    def show_notification(self, name, duration=3):
        """ Задава ново известие за разпознат човек """
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
        # Обновяваме известията на база на засечените лица в момента
        for _, name in face_data:
            if name: self.show_notification(name)

        # 1. Горна HUD лента
        roi_hud = frame[0:80, 0:self.width]
        cv2.addWeighted(self.assets['hud_base'], 0.6, roi_hud, 0.4, 0, roi_hud)

        # 2. SCHOOL AI Заглавие
        title_h, title_w = self.assets['title'].shape[:2]
        mask_title = self.assets['title_mask']
        roi_title = frame[0:title_h, 0:title_w]
        roi_title[mask_title] = self.assets['title'][mask_title]

        # 3. Интелигентно известие (в горния HUD)
        current_time = time.time()
        if current_time < self.notification_expiry and self.notification_asset is not None:
            n_h, n_w = self.notification_asset.shape[:2]
            # Поставяме го след заглавието
            start_x = 320 
            if start_x + n_w < self.width - 320: # Проверка да не застъпи часовника
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
        start_x_time = self.width - t_w - 20
        roi_time = frame[18:18+t_h, start_x_time:start_x_time+t_w]
        roi_time[self.assets['time_mask']] = self.assets['time'][self.assets['time_mask']]

        # 4.5. Статус на системата (ACTIVE / PAUSED)
        if is_processing != self.last_status:
            status_text = "STATUS: ACTIVE" if is_processing else "STATUS: PAUSED"
            status_color = (0, 255, 0) if is_processing else (255, 0, 0)
            try:
                sw = int(FONT_MAIN.getlength(status_text))
            except: sw = 300
            self.assets['status'], self.assets['status_mask'] = self._render_text_asset(
                status_text, FONT_MAIN, status_color, (sw + 20, 50)
            )
            self.last_status = is_processing

        s_h, s_w = self.assets['status'].shape[:2]
        start_x_status = start_x_time - s_w - 60
        roi_status = frame[18:18+s_h, start_x_status:start_x_status+s_w]
        roi_status[self.assets['status_mask']] = self.assets['status'][self.assets['status_mask']]

        # 5. Долен панел
        count = people_counter.get_count()
        px, py = 15, self.height - 75
        roi_panel = frame[py:py+60, px:px+380]
        cv2.addWeighted(self.assets['panel_base'], 0.7, roi_panel, 0.3, 0, roi_panel)

        if count != self.last_count:
            count_text = f"ЗАСЕЧЕНИ ДНЕС: {count}"
            self.assets['count'], self.assets['count_mask'] = self._render_text_asset(
                count_text, FONT_COUNTER_TITLE, (0, 255, 255), (360, 50)
            )
            self.last_count = count

        c_h, c_w = self.assets['count'].shape[:2]
        roi_count = frame[py+12:py+12+c_h, px+12:px+12+c_w]
        roi_count[self.assets['count_mask']] = self.assets['count'][self.assets['count_mask']]

        return frame

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

    if is_ip_camera:
        log_system(f"Connecting to IP camera: {source}")
    else:
        log_system(f"Opening USB camera index: {source}")

    video_capture = cv2.VideoCapture(source)
    
    # За USB камери задаваме Full HD резолюция (IP камерите си носят собствена)
    if not is_ip_camera:
        video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, Config.TARGET_WIDTH)
        video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, Config.TARGET_HEIGHT)

    if not video_capture.isOpened():
        log_system("ERROR: Camera not found!", "error")
        return

    # Проверяваме реалната резолюция, която камерата връща
    actual_w = int(video_capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    target_w, target_h = Config.TARGET_WIDTH, Config.TARGET_HEIGHT
    needs_upscale = (actual_w < target_w or actual_h < target_h)

    if needs_upscale:
        log_system(f"Camera delivers {actual_w}x{actual_h}. Will upscale to {target_w}x{target_h} with INTER_CUBIC for crisp text.")
    else:
        log_system(f"Camera delivers native {actual_w}x{actual_h}. No upscale needed.")

    # Настройка за FULLSCREEN
    win_name = 'SCHOOL AI - CYBER HUD'
    cv2.namedWindow(win_name, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    log_system("System active. Press 'q' or 'ESC' to exit.")

    # Брояч на уникални разпознати хора за деня
    people_counter = DailyPeopleCounter()
    state_manager.set_people_counter(people_counter)

    # Инициализиране на UI мениджъра
    ui_manager = UIManager(target_w, target_h)

    # Стартиране на фоновия работник за лицево разпознаване
    recognition_worker = FaceRecognitionWorker(face_manager, tts_manager, people_counter, state_manager)
    recognition_worker.start()

    frame_count = 0
    process_every_n_frames = Config.PROCESS_EVERY_N_FRAMES  # Изпращай нов кадър за анализ на всеки n кадъра
    
    state_manager.set_status("Running")

    while state_manager.should_continue():
        ret, frame = video_capture.read()
        if not ret: break

        # Синхронизираме състоянието за пауза с уеб панела
        is_processing = state_manager.is_processing()

        # ЪПСКЕЙЛ: Ако камерата дава по-ниска резолюция, качествено ъпскейлваме ПРЕДИ рисуване на текста.
        if needs_upscale:
            frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        if is_processing:
            if frame_count % process_every_n_frames == 0:
                recognition_worker.submit_frame(frame.copy())
            face_data = recognition_worker.get_face_data()
        else:
            recognition_worker.clear_face_data()
            face_data = []

        # Рисуването е оптимизирано чрез UI мениджъра
        frame = ui_manager.draw(frame, face_data, is_processing, people_counter)
        
        # Обновяваме кадъра за уеб стрийминга
        state_manager.update_frame(frame)
        
        cv2.imshow(win_name, frame)

        frame_count += 1

        # Изход
        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '): # Toggle with Space
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
