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

def draw_ui(frame, face_data, is_processing):
    """ Основна функция за рисуване на модерния интерфейс """
    height, width = frame.shape[:2]
    
    # 1. Глобален HUD (Горен панел) - Оптимизиран: блендваме само ROI (горните 60 пиксела)
    roi = frame[0:60, 0:width]
    overlay = roi.copy()
    cv2.rectangle(overlay, (0, 0), (width, 60), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.6, roi, 0.4, 0, roi)

    status_text = "SYSTEM: ACTIVE" if is_processing else "SYSTEM: PAUSED"
    time_str = datetime.now().strftime("%H:%M:%S")

    # Рисуваме HUD текстовете чрез OpenCV (бързо и без PIL)
    cv2_status_color = (0, 255, 0) if is_processing else (0, 0, 255)
    (status_w, status_h), _ = cv2.getTextSize(status_text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
    status_x = (width - status_w) // 2
    
    cv2.putText(frame, "SCHOOL AI", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(frame, status_text, (status_x, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, cv2_status_color, 2, cv2.LINE_AA)
    cv2.putText(frame, f"TIME: {time_str}", (width - 260, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)

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
            text_width = len(label_text) * 18  # Приблизителен фолбек за един символ
            
        label_h = 50
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
            
            # Рисуваме кирилския текст върху микро-картинката
            draw_sub.text((10, 5), label_text, font=FONT_SMALL, fill=(255, 255, 255))
            
            # Връщаме обратно в OpenCV формат и вграждаме в големия кадър
            sub_img_final = cv2.cvtColor(np.array(sub_pil), cv2.COLOR_RGB2BGR)
            frame[y1:y2, x1:x2] = sub_img_final

    return frame

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
