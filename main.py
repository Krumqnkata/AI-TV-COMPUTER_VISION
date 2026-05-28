import cv2
import numpy as np
import time
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

def draw_ui(frame, face_data):
    """ Основна функция за рисуване на модерния интерфейс """
    height, width = frame.shape[:2]
    
    # 1. Глобален HUD (Горен панел)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, 60), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    
    # Конвертираме към PIL за рисуване на текстове на кирилица
    img_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil, "RGBA")
    
    # Зареждане на шрифтове
    font_path = "ARIAL.TTF"
    try:
        font_main = ImageFont.truetype(font_path, 24)
        font_small = ImageFont.truetype(font_path, 18)
    except:
        font_main = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Рисуваме заглавие и часовник (БЕЗ емоджита)
    time_str = datetime.now().strftime("%H:%M:%S")
    draw.text((20, 15), "SCHOOL AI - SYSTEM MONITORING", font=font_main, fill=(0, 255, 255))
    draw.text((width - 180, 15), f"TIME: {time_str}", font=font_main, fill=(255, 255, 255))

    # Рисуваме елементи за всяко лице
    for (top, right, bottom, left), name in face_data:
        # Стилизирани ъгли (Cyber Brackets)
        length = 35
        t = 3
        color_neon = (0, 255, 255, 255) # Cyan (RGBA за PIL)
        
        # Горе ляво
        draw.line([(left, top), (left + length, top)], fill=color_neon, width=t)
        draw.line([(left, top), (left, top + length)], fill=color_neon, width=t)
        # Горе дясно
        draw.line([(right, top), (right - length, top)], fill=color_neon, width=t)
        draw.line([(right, top), (right, top + length)], fill=color_neon, width=t)
        # Долу ляво
        draw.line([(left, bottom), (left + length, bottom)], fill=color_neon, width=t)
        draw.line([(left, bottom), (left, bottom - length)], fill=color_neon, width=t)
        # Долу дясно
        draw.line([(right, bottom), (right - length, bottom)], fill=color_neon, width=t)
        draw.line([(right, bottom), (right, bottom - length)], fill=color_neon, width=t)

        # Подложка за името (полупрозрачна)
        draw.rectangle([left, top - 35, right, top], fill=(0, 0, 0, 160))
        
        # Име (БЕЗ емоджи)
        display_name = f"NAME: {name}"
        draw.text((left + 5, top - 30), display_name, font=font_small, fill=(255, 255, 255))

    # Обратно към OpenCV формат
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

def main():
    log_system("STARTING CYBER-HUD INTERFACE (FULLSCREEN)")
    
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS)
    
    video_capture = cv2.VideoCapture(Config.CAMERA_INDEX)
    if not video_capture.isOpened():
        log_system("ERROR: Camera not found!", "error")
        return

    # Настройка за истински FULLSCREEN
    win_name = 'SCHOOL AI - CYBER HUD'
    cv2.namedWindow(win_name, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    log_system("System active. Press 'q' or 'ESC' to exit.")

    while True:
        ret, frame = video_capture.read()
        if not ret: break

        # Разпознаване (на малък кадър за бързина)
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        face_locations, face_names = face_manager.identify_face(small_frame)

        # Подготовка на данните за мащабиране
        face_data = []
        for (top, right, bottom, left), name in zip(face_locations, face_names):
            face_data.append(((top*4, right*4, bottom*4, left*4), name))
            if name != "Unknown":
                tts_manager.speak_joke(name)

        # Рисуване на интерфейса
        frame = draw_ui(frame, face_data)

        cv2.imshow(win_name, frame)

        # Изход при 'q' (113), 'ESC' (27) или затваряне на прозореца
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27 or cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
            log_system("System stopped by user.")
            break

    video_capture.release()
    cv2.destroyAllWindows()
    log_system("Goodbye!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_system(f"Critical error: {e}", "error")
