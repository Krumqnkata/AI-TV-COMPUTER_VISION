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
    def __init__(self, face_manager, tts_manager, people_counter):
        self.face_manager = face_manager
        self.tts_manager = tts_manager
        self.people_counter = people_counter
        self.frame_to_process = None
        self.face_data = []
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
                # Ресайзваме във фоновата нишка за максимална производителност на основната
                small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                face_locations, face_names = self.face_manager.identify_face(small_frame)

                temp_face_data = []
                unknown_in_frame = 0
                for (top, right, bottom, left), name in zip(face_locations, face_names):
                    temp_face_data.append(((top * 4, right * 4, bottom * 4, left * 4), name))
                    if name != "Unknown":
                        self.people_counter.register(name)
                        self.tts_manager.speak_joke(name)
                    else:
                        unknown_in_frame += 1

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


def draw_ui(frame, face_data, is_processing, people_counter):
    """ Основна функция за рисуване на модерния интерфейс """
    height, width = frame.shape[:2]
    
    # 1. Глобален HUD (Горен панел) - Оптимизиран: блендваме само ROI (горните 80 пиксела за Full HD)
    roi = frame[0:80, 0:width]
    overlay = roi.copy()
    cv2.rectangle(overlay, (0, 0), (width, 80), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.6, roi, 0.4, 0, roi)

    status_text = "SYSTEM: ACTIVE" if is_processing else "SYSTEM: PAUSED"
    time_str = datetime.now().strftime("%H:%M:%S")

    # Рисуваме HUD текстовете чрез PIL за кристален антиалиасиран текст
    status_color_rgb = (0, 255, 0) if is_processing else (255, 0, 0)

    # Изчисляваме позицията за центриран статус текст
    try:
        status_w = int(FONT_MAIN.getlength(status_text))
    except Exception:
        status_w = len(status_text) * 20
    status_x = (width - status_w) // 2

    _render_pil_text_on_frame(frame, "SCHOOL AI", (20, 18), FONT_MAIN, (0, 255, 255))
    _render_pil_text_on_frame(frame, status_text, (status_x, 18), FONT_MAIN, status_color_rgb)
    _render_pil_text_on_frame(frame, f"TIME: {time_str}", (width - 290, 18), FONT_MAIN, (255, 255, 255))

    # Рисуваме елементи за всяко лице
    for (top, right, bottom, left), name in face_data:
        length = 35
        t = 3
        color_neon = (255, 255, 0) # Cyan в BGR формат за OpenCV
        
        # Cyber Brackets (OpenCV - нативно и изключително бързо)
        # Горно-ляво
        cv2.line(frame, (left, top), (left + length, top), color_neon, t)
        cv2.line(frame, (left, top), (left, top + length), color_neon, t)
        # Горно-дясно
        cv2.line(frame, (right, top), (right - length, top), color_neon, t)
        cv2.line(frame, (right, top), (right, top + length), color_neon, t)
        # Долно-ляво
        cv2.line(frame, (left, bottom), (left + length, bottom), color_neon, t)
        cv2.line(frame, (left, bottom), (left, bottom - length), color_neon, t)
        # Долно-дясно
        cv2.line(frame, (right, bottom), (right - length, bottom), color_neon, t)
        cv2.line(frame, (right, bottom), (right, bottom - length), color_neon, t)

        # Подложка за името (Cyrillic Text Support via PIL on Small Crop Only)
        label_text = f"NAME: {name}"
        
        # Изчисляваме динамично ширината на текста според шрифта
        try:
            text_width = int(FONT_SMALL.getlength(label_text))
        except Exception:
            text_width = len(label_text) * 22  # Приблизителен фолбек за един символ
            
        label_h = 55
        # Ширината е по-голямата стойност между ширината на лицето и дължината на текста + отстъп
        label_w = max(right - left, text_width + 25)
            
        # Защита за границите на екрана (ако кутията излиза отдясно, я преместваме наляво)
        y1 = max(0, top - label_h)
        y2 = max(0, top)
        
        x2 = left + label_w
        if x2 > width:
            x2 = width
            x1 = max(0, x2 - label_w)
        else:
            x1 = max(0, left)
        
        if y2 > y1 and x2 > x1:
            # Изрязваме само малката лента за надписа
            sub_img = frame[y1:y2, x1:x2].copy()
            
            # Нанасяме полупрозрачен черен цвят върху нея
            overlay_box = np.zeros_like(sub_img)
            cv2.rectangle(overlay_box, (0, 0), (x2 - x1, y2 - y1), (0, 0, 0), -1)
            cv2.addWeighted(overlay_box, 0.6, sub_img, 0.4, 0, sub_img)
            
            # Конвертираме САМО тази микро-картинка към PIL за текстовия рендеринг
            sub_pil = Image.fromarray(cv2.cvtColor(sub_img, cv2.COLOR_BGR2RGB))
            draw_sub = ImageDraw.Draw(sub_pil)
            
            # Рисуваме кирилския текст върху микро-картинката (вертикално центриран)
            draw_sub.text((10, 5), label_text, font=FONT_SMALL, fill=(255, 255, 255))
            
            # Връщаме обратно в OpenCV формат и вграждаме в големия кадър
            sub_img_final = cv2.cvtColor(np.array(sub_pil), cv2.COLOR_RGB2BGR)
            frame[y1:y2, x1:x2] = sub_img_final

    # 3. Брояч "Засечени днес" (долен ляв ъгъл) - компактен панел само с бройка
    count = people_counter.get_count()

    panel_w = 380
    panel_h = 60

    panel_x1 = 15
    panel_y1 = height - panel_h - 15
    panel_x2 = panel_x1 + panel_w
    panel_y2 = height - 15

    # Клампваме координатите в рамките на кадъра
    panel_y1 = max(0, panel_y1)
    panel_x2 = min(width, panel_x2)

    if panel_y2 > panel_y1 and panel_x2 > panel_x1:
        # Полупрозрачен тъмен фон за панела
        roi_panel = frame[panel_y1:panel_y2, panel_x1:panel_x2].copy()
        overlay_panel = np.zeros_like(roi_panel)
        cv2.rectangle(overlay_panel, (0, 0), (panel_w, panel_h), (20, 20, 20), -1)
        cv2.addWeighted(overlay_panel, 0.7, roi_panel, 0.3, 0, roi_panel)

        # Тънка cyan рамка отгоре
        cv2.line(roi_panel, (0, 0), (panel_w, 0), (255, 255, 0), 2)

        frame[panel_y1:panel_y2, panel_x1:panel_x2] = roi_panel

        # "ЗАСЕЧЕНИ ДНЕС: X"
        title_text = f"ЗАСЕЧЕНИ ДНЕС: {count}"
        _render_pil_text_on_frame(frame, title_text, (panel_x1 + 12, panel_y1 + 12), FONT_COUNTER_TITLE, (0, 255, 255))

    return frame

def main():
    log_system("STARTING CYBER-HUD INTERFACE (HD QUALITY)")
    
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS)
    
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

    # Стартиране на фоновия работник за лицево разпознаване
    recognition_worker = FaceRecognitionWorker(face_manager, tts_manager, people_counter)
    recognition_worker.start()

    frame_count = 0
    process_every_n_frames = Config.PROCESS_EVERY_N_FRAMES  # Изпращай нов кадър за анализ на всеки n кадъра
    is_processing = True

    while True:
        ret, frame = video_capture.read()
        if not ret: break

        # ЪПСКЕЙЛ: Ако камерата дава по-ниска резолюция, качествено ъпскейлваме ПРЕДИ рисуване на текста.
        # Това гарантира, че текстът се рендерира в Full HD пространство и никога не е пикселизиран.
        if needs_upscale:
            frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_CUBIC)

        if is_processing:
            if frame_count % process_every_n_frames == 0:
                recognition_worker.submit_frame(frame)
            face_data = recognition_worker.get_face_data()
        else:
            recognition_worker.clear_face_data()
            face_data = []

        # Рисуването е оптимизирано и плавно при всеки кадър
        frame = draw_ui(frame, face_data, is_processing, people_counter)
        cv2.imshow(win_name, frame)

        frame_count += 1

        # Изход
        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '): # Toggle with Space
            is_processing = not is_processing
            log_system(f"System {'ACTIVE' if is_processing else 'PAUSED'}")
        
        if key == ord('q') or key == 27 or cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
            log_system("System stopped by user.")
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
