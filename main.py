import cv2
import numpy as np
import time
import threading
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from utils.config import Config
from engine.face_manager import FaceManager
from engine.tts_manager import TTSManager
from utils.logger import log_system

# Цветове (BGR формат за OpenCV)
COLOR_CYAN = (255, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (0, 0, 0)
COLOR_NEON_GREEN = (0, 255, 0)

# Глобален кеш за шрифтове за избягване на скъпото четене от диск всеки кадър
FONT_PATH = "ARIAL.TTF"
try:
    FONT_MAIN = ImageFont.truetype(FONT_PATH, 32)
    FONT_SMALL = ImageFont.truetype(FONT_PATH, 36)
except Exception:
    FONT_MAIN = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

class FaceRecognitionWorker:
    """ Работник във фонов режим за разпознаване на лица без блокиране на GUI нишката """
    def __init__(self, face_manager, tts_manager):
        self.face_manager = face_manager
        self.tts_manager = tts_manager
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
                for (top, right, bottom, left), name in zip(face_locations, face_names):
                    temp_face_data.append(((top * 4, right * 4, bottom * 4, left * 4), name))
                    if name != "Unknown":
                        self.tts_manager.speak_joke(name)

                with self.lock:
                    self.face_data = temp_face_data

            time.sleep(0.01)

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

def draw_ui(frame, face_data, is_processing):
    """ Основна функция за рисуване на модерния интерфейс """
    height, width = frame.shape[:2]
    
    # 1. Глобален HUD (Горен панел) - Оптимизиран: блендваме само ROI (горните 60 пиксела)
    roi = frame[0:60, 0:width]
    overlay = roi.copy()
    cv2.rectangle(overlay, (0, 0), (width, 60), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.6, roi, 0.4, 0, roi)

    # Конвертираме към PIL за рисуване на текстове на кирилица с високо качество (anti-aliasing)
    img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil, "RGBA")

    # Рисуваме заглавие, статус и часовник
    time_str = datetime.now().strftime("%H:%M:%S")
    status_text = "SYSTEM: ACTIVE" if is_processing else "SYSTEM: PAUSED"
    status_color = (0, 255, 0) if is_processing else (0, 0, 255)
    
    # Изчисляваме центъра
    status_width = draw.textlength(status_text, font=FONT_MAIN)
    status_x = (width - status_width) // 2
    
    draw.text((20, 12), "SCHOOL AI", font=FONT_MAIN, fill=(0, 255, 255))
    draw.text((status_x, 12), status_text, font=FONT_MAIN, fill=status_color)
    draw.text((width - 280, 15), f"TIME: {time_str}", font=FONT_MAIN, fill=(255, 255, 255))

    # Рисуваме елементи за всяко лице
    for (top, right, bottom, left), name in face_data:
        length = 35
        t = 3
        color_neon = (0, 255, 255, 255) # Cyan
        
        # Cyber Brackets
        draw.line([(left, top), (left + length, top)], fill=color_neon, width=t)
        draw.line([(left, top), (left, top + length)], fill=color_neon, width=t)
        draw.line([(right, top), (right - length, top)], fill=color_neon, width=t)
        draw.line([(right, top), (right, top + length)], fill=color_neon, width=t)
        draw.line([(left, bottom), (left + length, bottom)], fill=color_neon, width=t)
        draw.line([(left, bottom), (left, bottom - length)], fill=color_neon, width=t)
        draw.line([(right, bottom), (right - length, bottom)], fill=color_neon, width=t)
        draw.line([(right, bottom), (right, bottom - length)], fill=color_neon, width=t)

        # Подложка за името
        draw.rectangle([left, top - 50, right, top], fill=(0, 0, 0, 160))
        draw.text((left + 10, top - 45), f"NAME: {name}", font=FONT_SMALL, fill=(255, 255, 255))

    # Обратно към OpenCV формат
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

def main():
    log_system("STARTING CYBER-HUD INTERFACE (HD QUALITY)")
    
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS)
    
    video_capture = cv2.VideoCapture(Config.CAMERA_INDEX)
    
    # ЗАДАВАМЕ HD РЕЗОЛЮЦИЯ ЗА ЧИСТ ОБРАЗ И ТЕКСТ
    video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not video_capture.isOpened():
        log_system("ERROR: Camera not found!", "error")
        return

    # Настройка за FULLSCREEN
    win_name = 'SCHOOL AI - CYBER HUD'
    cv2.namedWindow(win_name, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    log_system("System active. Press 'q' or 'ESC' to exit.")

    # Стартиране на фоновия работник за лицево разпознаване
    recognition_worker = FaceRecognitionWorker(face_manager, tts_manager)
    recognition_worker.start()

    frame_count = 0
    process_every_n_frames = 10  # Изпращай нов кадър за анализ на всеки 10 кадъра
    is_processing = True

    while True:
        ret, frame = video_capture.read()
        if not ret: break

        if is_processing:
            if frame_count % process_every_n_frames == 0:
                recognition_worker.submit_frame(frame)
            face_data = recognition_worker.get_face_data()
        else:
            recognition_worker.clear_face_data()
            face_data = []

        # Рисуването е оптимизирано и плавно при всеки кадър
        frame = draw_ui(frame, face_data, is_processing)
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

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_system(f"Critical error: {e}", "error")
