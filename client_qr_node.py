import cv2
import requests
import time
import os
import tempfile
import pygame
from gtts import gTTS

# URL на централния сървър
SERVER_URL = "http://localhost:5000"
CAMERA_ID = "CAM-ENTRANCE-01"
ZONE_ID = "MAIN_ENTRANCE"

def speak_message(text):
    """ Конвертира текст в българска реч чрез gTTS и я възпроизвежда """
    if not text:
        return
    print(f"[TTS] Изговаряне: {text}")
    try:
        # Генерираме аудио файл чрез gTTS
        tts = gTTS(text=text, lang='bg')
        
        # Записваме във временен файл
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            temp_filename = fp.name
            
        tts.save(temp_filename)
        
        # Възпроизвеждаме чрез pygame.mixer
        pygame.mixer.music.load(temp_filename)
        pygame.mixer.music.play()
        
        # Изчакваме приключването на възпроизвеждането
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
            
        pygame.mixer.music.unload()
        # Изтриваме временния файл
        os.remove(temp_filename)
    except Exception as e:
        print(f"[Грешка] Неуспешно генериране/възпроизвеждане на говор: {e}")

def send_voice_command(person_id, text_query):
    """ Изпраща гласова/текстова команда към сървъра """
    url = f"{SERVER_URL}/api/voice_command"
    payload = {
        "person_id": person_id,
        "text_query": text_query
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            res_data = response.json()
            print(f"\n[AI Отговор] {res_data['response']}")
            speak_message(res_data['response'])
        else:
            print(f"[Грешка] Сървърът върна статус: {response.status_code}")
    except Exception as e:
        print(f"[Грешка] Връзката със сървъра пропадна: {e}")

def main():
    print("=" * 60)
    print(" СТАРТИРАНЕ НА КРАЙНА ТОЧКА (CLIENT NODE) - QR ЧЕТЕЦ")
    print("=" * 60)
    
    # Инициализираме pygame за аудио
    pygame.mixer.init()
    
    # Инициализираме камерата
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[Грешка] Камерата не може да бъде отворена!")
        return

    # OpenCV QR детектор
    detector = cv2.QRCodeDetector()
    
    # Коодаун за засичане на един и същ бадж (в секунди)
    cooldown_period = 10
    detected_badges = {}  # {badge_token: last_detection_timestamp}
    
    last_person_id = None
    
    print("\nИнструкции:")
    print(" -> Покажете QR бадж пред камерата.")
    print(" -> Натиснете [SPACE] в конзолата, за да въведете текстова команда.")
    print(" -> Натиснете 'Q' върху прозореца на камерата, за да излезете.")
    print("-" * 60)

    # Прозорец на камерата
    win_name = "School AI - QR Node Camera"
    cv2.namedWindow(win_name)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[Грешка] Проблем с получаването на кадър.")
            break

        # Засичане и разчитане на QR кодове
        data, bbox, _ = detector.detectAndDecode(frame)
        
        current_time = time.time()
        
        # Рисуваме кутия около QR кода, ако е намерен
        if bbox is not None and len(bbox) > 0:
            pts = bbox[0].astype(int)
            for i in range(len(pts)):
                cv2.line(frame, tuple(pts[i]), tuple(pts[(i+1) % len(pts)]), (0, 255, 0), 2)
            
            if data:
                # Извличаме токена
                token = data.strip()
                
                # Проверяваме за коодаун
                last_seen = detected_badges.get(token, 0)
                if current_time - last_seen > cooldown_period:
                    detected_badges[token] = current_time
                    print(f"\n[QR] Засечен бадж: {token}")
                    
                    # Изпращаме към сървъра
                    url = f"{SERVER_URL}/api/detect_qr"
                    payload = {
                        "camera_id": CAMERA_ID,
                        "zone_id": ZONE_ID,
                        "badge_token": token,
                        "confidence": 1.0
                    }
                    
                    try:
                        print("[Мрежа] Изпращане на събитие към сървъра...")
                        response = requests.post(url, json=payload)
                        if response.status_code == 200:
                            res_data = response.json()
                            if res_data.get("status") == "success":
                                welcome_msg = res_data.get("message")
                                last_person_id = res_data["person"]["id"]
                                print(f"[Сървър] Разпознат: {res_data['person']['name']}")
                                print(f"[Приветствие] {welcome_msg}")
                                speak_message(welcome_msg)
                            else:
                                print(f"[Сървър] Грешка: {res_data.get('message')}")
                                speak_message(res_data.get("message"))
                        else:
                            print(f"[Грешка] Сървърът върна статус: {response.status_code}")
                    except Exception as e:
                        print(f"[Грешка] Връзката със сървъра пропадна: {e}")

        # Показваме видео потока
        cv2.putText(frame, "Scan QR badge...", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow(win_name, frame)

        # Клавишни команди
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            break
            
        # Заявка за въвеждане на текст (в конзолата)
        # Тъй като cv2.waitKey е неблокиращ, можем да проверим дали потребителят е натиснал интервал в прозореца на камерата
        if key == ord(' '):
            print("\n" + "="*30)
            print(" РЕЖИМ ГЛАСОВА/ТЕКСТОВА КОМАНДА")
            print("="*30)
            if not last_person_id:
                print("[Внимание] Няма идентифициран потребител. Командата ще бъде изпратена като Гост.")
            
            query = input("Въведете команда (напр. 'имам ли съобщения', 'къде е кабинет 304'): ")
            if query.strip():
                send_voice_command(last_person_id, query)
            print("Връщане към режим на сканиране...")
            print("="*30 + "\n")

    cap.release()
    cv2.destroyAllWindows()
    print("Излизане...")

if __name__ == "__main__":
    main()
