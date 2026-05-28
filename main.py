import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from utils.config import Config
from engine.face_manager import FaceManager
from engine.tts_manager import TTSManager
from utils.logger import log_system

def draw_text_cyrillic(img, text, position, font_size=25, color=(255, 255, 255)):
    """ Рисува текст на кирилица върху кадъра чрез Pillow """
    try:
        # Конвертираме OpenCV (BGR) към PIL (RGB)
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        # Път към кирилски шрифт в Windows
        font_path = "C:\\Windows\\Fonts\\arial.ttf"
        font = ImageFont.truetype(font_path, font_size)
        
        # Рисуваме текста
        draw.text(position, text, font=font, fill=color)
        
        # Обратно към OpenCV (BGR)
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    except Exception as e:
        # Ако Pillow се провали, падаме до стандартния OpenCV (без кирилица)
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return img

def main():
    log_system("🚀 СТАРТИРАНЕ НА SCHOOL AI TV SYSTEM")
    
    # Инициализация на мениджърите
    face_manager = FaceManager(Config.FACES_DATA_PATH)
    face_manager.load_faces()
    
    tts_manager = TTSManager(Config.JOKES_FILE_PATH, Config.COOLDOWN_SECONDS)
    
    # Инициализация на камерата
    video_capture = cv2.VideoCapture(Config.CAMERA_INDEX)

    if not video_capture.isOpened():
        log_system("❌ ГРЕШКА: Камерата не може да бъде отворена!", "error")
        return

    log_system("✅ Системата работи. Натисни 'q' в прозореца за изход.")

    while True:
        ret, frame = video_capture.read()
        if not ret:
            log_system("⚠️ Загуба на видео поток.", "error")
            break

        # Намаляваме размера на кадъра за по-бързо разпознаване
        small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        
        # Разпознаване на лица
        face_locations, face_names = face_manager.identify_face(small_frame)

        for (top, right, bottom, left), name in zip(face_locations, face_names):
            # Мащабираме координатите обратно (тъй като работихме с малък кадър)
            top *= 4
            right *= 4
            bottom *= 4
            left *= 4

            # Рисуваме рамка (Зелена)
            cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)
            
            # Изписваме името на кирилица
            frame = draw_text_cyrillic(frame, name, (left, top - 35), font_size=24, color=(0, 255, 0))

            # Ако човекът е разпознат, активираме шегата
            if name != "Unknown":
                tts_manager.speak_joke(name)

        # Показване на видеото
        cv2.imshow('School AI TV - Bulgarian Version', frame)

        # Изход при натискане на 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            log_system("🛑 Спиране на системата от потребителя.")
            break

    # Почистване
    video_capture.release()
    cv2.destroyAllWindows()
    log_system("👋 Довиждане!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_system(f"Критична грешка при работа: {e}", "error")
