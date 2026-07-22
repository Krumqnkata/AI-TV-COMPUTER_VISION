import cv2
import requests
import time
import os
import sys
import tempfile
import pygame
from gtts import gTTS
from utils.config import Config

# URL на централния сървър
SERVER_URL = Config.SERVER_URL
CAMERA_ID = Config.CAMERA_ID
ZONE_ID = Config.ZONE_ID
SCREEN_ID = Config.SCREEN_ID
DEVICE_API_KEY = Config.DEVICE_API_KEY
DEVICE_ID = Config.DEVICE_ID
DEVICE_KEY = Config.DEVICE_KEY
HTTP_TIMEOUT = Config.HTTP_TIMEOUT_SECONDS

http = requests.Session()


def set_device_credentials(device_id, device_key):
    """Apply individual credentials, falling back to the legacy shared key."""
    global DEVICE_ID, DEVICE_KEY
    DEVICE_ID = device_id or ""
    DEVICE_KEY = device_key or ""
    http.headers.pop("X-Device-ID", None)
    http.headers.pop("X-Device-Key", None)
    if DEVICE_KEY:
        http.headers.update({"X-Device-ID": DEVICE_ID, "X-Device-Key": DEVICE_KEY})
    elif DEVICE_API_KEY:
        http.headers.update({"X-Device-Key": DEVICE_API_KEY})


set_device_credentials(DEVICE_ID, DEVICE_KEY)


def enroll_device(enrollment_token):
    """Redeem a short-lived code created in the admin panel."""
    if not DEVICE_ID:
        raise RuntimeError("DEVICE_ID е задължителен за сдвояване")
    response = requests.post(
        f"{SERVER_URL}/api/devices/enroll",
        headers={"X-Enrollment-Token": enrollment_token},
        json={
            "identifier": DEVICE_ID,
            "name": Config.DEVICE_NAME,
            "device_type": Config.DEVICE_TYPE,
            "capabilities": ["camera", "qr", "audio"],
            "software_version": "2.1.0",
        },
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    set_device_credentials(payload["device_id"], payload["device_key"])
    print("\n[Сдвояване] Устройството е регистрирано успешно.")
    print("[ВАЖНО] Запазете следните deployment стойности преди следващ рестарт:")
    print(f"DEVICE_ID={payload['device_id']}")
    print(f"DEVICE_KEY={payload['device_key']}")
    return payload


def sync_device_control(runtime, cap):
    """Heartbeat/config polling and acknowledged safe commands."""
    if not DEVICE_ID or not DEVICE_KEY:
        return False
    now = time.monotonic()
    if now - runtime["last_heartbeat"] >= 30:
        response = http.post(
            f"{SERVER_URL}/api/devices/heartbeat",
            json={
                "status": "paused" if runtime["paused"] else "online",
                "software_version": "2.1.0",
                "capabilities": ["camera", "qr", "audio"],
            },
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        runtime["last_heartbeat"] = now

    if now - runtime["last_config"] >= 60:
        response = http.get(f"{SERVER_URL}/api/devices/config", timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        config = response.json().get("settings", {})
        runtime["idle_seconds"] = int(config.get("kiosk_idle_seconds", runtime["idle_seconds"]))
        runtime["cooldown_seconds"] = int(config.get("qr_same_camera_seconds", runtime["cooldown_seconds"]))
        runtime["voice_enabled"] = bool(config.get("voice_enabled", runtime["voice_enabled"]))
        runtime["last_config"] = now

    if now - runtime["last_commands"] < 5:
        return False
    runtime["last_commands"] = now
    response = http.get(f"{SERVER_URL}/api/devices/commands/pending", timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    restart_requested = False
    for item in response.json():
        success = True
        result = {}
        try:
            command = item["command"]
            if command == "refresh_config":
                runtime["last_config"] = 0
            elif command == "enable":
                runtime["paused"] = False
            elif command == "disable":
                runtime["paused"] = True
            elif command == "test_camera":
                success = bool(cap and cap.isOpened())
                result = {"camera_open": success}
            elif command == "test_audio":
                speak_message("Тестът на звука е успешен.")
            elif command == "test_screen":
                result = {"window": "active"}
            elif command == "restart_app":
                restart_requested = True
            else:
                success = False
                result = {"error": "unsupported_command"}
        except Exception as exc:
            success = False
            result = {"error": str(exc)[:300]}
        http.post(
            f"{SERVER_URL}/api/devices/commands/{item['id']}/ack",
            json={"success": success, "result": result},
            timeout=HTTP_TIMEOUT,
        ).raise_for_status()
    return restart_requested

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
    except Exception as e:
        print(f"[Грешка] Неуспешно генериране/възпроизвеждане на говор: {e}")
    finally:
        if 'temp_filename' in locals() and os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except OSError:
                pass

def send_voice_command(person_id, text_query):
    """ Изпраща гласова/текстова команда към сървъра """
    url = f"{SERVER_URL}/api/voice_command"
    payload = {
        "person_id": person_id,
        "text_query": text_query,
        "zone_id": ZONE_ID,
        "screen_id": SCREEN_ID,
    }
    try:
        response = http.post(url, json=payload, timeout=HTTP_TIMEOUT)
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
    
    if not DEVICE_KEY and Config.DEVICE_ENROLLMENT_TOKEN:
        try:
            enroll_device(Config.DEVICE_ENROLLMENT_TOKEN)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[Грешка] Сдвояването е неуспешно: {exc}")
            return
    if not DEVICE_KEY and not DEVICE_API_KEY:
        print("[Грешка] Конфигурирайте DEVICE_ID + DEVICE_KEY или временния legacy DEVICE_API_KEY.")
        return

    # Инициализираме pygame за аудио
    pygame.mixer.init()
    
    # Инициализираме камерата
    cap = cv2.VideoCapture(Config.CAMERA_SOURCE)
    if not cap.isOpened():
        print("[Грешка] Камерата не може да бъде отворена!")
        return

    # OpenCV QR детектор
    detector = cv2.QRCodeDetector()
    
    # Коодаун за засичане на един и същ бадж (в секунди)
    runtime = {
        "cooldown_seconds": 10,
        "idle_seconds": 60,
        "voice_enabled": True,
        "paused": False,
        "last_heartbeat": 0,
        "last_config": 0,
        "last_commands": 0,
    }
    detected_badges = {}  # {badge_token: last_detection_timestamp}
    
    last_person_id = None
    last_detection_time = 0
    
    print("\nИнструкции:")
    print(" -> Покажете QR бадж пред камерата.")
    print(" -> Натиснете [SPACE] в конзолата, за да въведете текстова команда.")
    print(" -> Натиснете 'Q' върху прозореца на камерата, за да излезете.")
    print("-" * 60)

    # Прозорец на камерата
    win_name = "School AI - QR Node Camera"
    cv2.namedWindow(win_name)

    restart_requested = False
    while True:
        try:
            restart_requested = sync_device_control(runtime, cap)
        except requests.RequestException as exc:
            print(f"[Управление] Временен проблем с control plane: {exc}")
        if restart_requested:
            print("[Управление] Получена потвърдена команда за рестарт на приложението.")
            break
        ret, frame = cap.read()
        if not ret:
            print("[Грешка] Проблем с получаването на кадър.")
            break

        # Засичане и разчитане на QR кодове
        data, bbox, _ = detector.detectAndDecode(frame)
        
        current_time = time.time()
        
        # Автоматично затваряне на сесията след 60 секунди бездействие на този клиент
        if last_person_id and (current_time - last_detection_time > runtime["idle_seconds"]):
            print("[Сесия] Сесията изтече поради бездействие. Потребителят е отписан.")
            try:
                http.post(
                    f"{SERVER_URL}/api/sessions/close",
                    json={"zone_id": ZONE_ID, "screen_id": SCREEN_ID},
                    timeout=HTTP_TIMEOUT,
                )
            except requests.RequestException:
                pass
            last_person_id = None
        
        # Рисуваме кутия около QR кода, ако е намерен
        if not runtime["paused"] and bbox is not None and len(bbox) > 0:
            pts = bbox[0].astype(int)
            for i in range(len(pts)):
                cv2.line(frame, tuple(pts[i]), tuple(pts[(i+1) % len(pts)]), (0, 255, 0), 2)
            
            if data:
                # Извличаме токена
                token = data.strip()
                
                # Проверяваме за коодаун
                last_seen = detected_badges.get(token, 0)
                if current_time - last_seen > runtime["cooldown_seconds"]:
                    detected_badges[token] = current_time
                    print("\n[QR] Засечен бадж; изпращане към сървъра...")
                    
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
                        response = http.post(url, json=payload, timeout=HTTP_TIMEOUT)
                        if response.status_code == 200:
                            res_data = response.json()
                            if res_data.get("status") == "success":
                                welcome_msg = res_data.get("message")
                                last_person_id = res_data["person"]["id"]
                                last_detection_time = current_time  # Запомняме времето на последно засичане
                                print(f"[Сървър] Разпознат: {res_data['person']['name']}")
                                print(f"[Приветствие] {welcome_msg}")
                                speak_message(welcome_msg)
                                delivery_id = res_data.get("delivery_id")
                                message_ids = res_data.get("message_ids", [])
                                if delivery_id:
                                    http.post(
                                        f"{SERVER_URL}/api/deliveries/ack",
                                        json={"delivery_id": delivery_id, "message_ids": message_ids},
                                        timeout=HTTP_TIMEOUT,
                                    )
                            elif res_data.get("status") == "ignored":
                                reason = res_data.get("reason")
                                if reason == "kiosk_busy":
                                    print(f"[Сървър] Точката за засичане е заделена за друг потребител в момента.")
                                # При дублирани засичания (cooldown) не правим нищо и сме тихи
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
            last_detection_time = time.time()  # Удължаваме сесията на клиента при натискане на Space
            print("\n" + "="*30)
            print(" РЕЖИМ ГЛАСОВА/ТЕКСТОВА КОМАНДА")
            print("="*30)
            if not last_person_id:
                print("[Внимание] Няма идентифициран потребител. Командата ще бъде изпратена като Гост.")
            
            query = input("Въведете команда (напр. 'имам ли съобщения', 'къде е кабинет 304'): ")
            if query.strip() and runtime["voice_enabled"]:
                send_voice_command(last_person_id, query)
            elif query.strip():
                print("[Информация] Гласовите/текстовите команди са изключени от администратора.")
            print("Връщане към режим на сканиране...")
            print("="*30 + "\n")

    cap.release()
    cv2.destroyAllWindows()
    if restart_requested:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    print("Излизане...")

if __name__ == "__main__":
    main()
