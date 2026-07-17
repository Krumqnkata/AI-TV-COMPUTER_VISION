import requests
import threading
import time
import sys
import random

SERVER_URL = "http://localhost:5000"

def send_qr_scan(camera_id, zone_id, token, confidence=1.0):
    url = f"{SERVER_URL}/api/detect_qr"
    payload = {
        "camera_id": camera_id,
        "zone_id": zone_id,
        "badge_token": token,
        "confidence": confidence
    }
    try:
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=10)
        elapsed = time.time() - start_time
        if response.status_code == 200:
            res_data = response.json()
            status = res_data.get("status")
            msg = res_data.get("message", "")
            print(f"[+] [{camera_id}] Scan sent in {elapsed:.3f}s. Result: {status} | Msg: {msg[:100]}...")
            return res_data
        else:
            print(f"[-] [{camera_id}] Server returned error status {response.status_code}")
    except Exception as e:
        print(f"[-] [{camera_id}] Request failed: {e}")
    return None

def send_voice_command(person_id, query):
    url = f"{SERVER_URL}/api/voice_command"
    payload = {
        "person_id": person_id,
        "text_query": query
    }
    try:
        start_time = time.time()
        response = requests.post(url, json=payload, timeout=30)
        elapsed = time.time() - start_time
        if response.status_code == 200:
            res_data = response.json()
            intent = res_data.get("intent")
            resp = res_data.get("response", "")
            print(f"[AI] [Person {person_id}] Command: '{query}' -> Intent: {intent} | Elapsed: {elapsed:.3f}s | Resp: {resp[:120]}...")
            return res_data
        else:
            print(f"[-] [Person {person_id}] Server returned error status {response.status_code}")
    except Exception as e:
        print(f"[-] [Person {person_id}] Voice command failed: {e}")
    return None

def run_simulation():
    print("=" * 70)
    print("   STARTING MULTI-NODE NETWORK SCAN & CONCURRENCY SIMULATION")
    print("=" * 70)
    print(f"Connecting to Server: {SERVER_URL}")
    print("Ensure the server is running (run 'main.py' or 'run.bat' in a separate terminal)")
    print("-" * 70)
    
    # Първо тестваме дали сървърът работи
    try:
        test_res = requests.get(f"{SERVER_URL}/api/persons", timeout=3)
        if test_res.status_code == 200:
            print("[✓] Server is online and responding.")
        else:
            print(f"[!] Server returned status {test_res.status_code}. Is it running?")
            sys.exit(1)
    except Exception as e:
        print(f"[✗] Could not connect to backend server at {SERVER_URL}. Please start it first!")
        print("    You can start it by running `python main.py` or double-clicking `run.bat`.")
        sys.exit(1)

    print("\n[Сценарий 1] Симулиране на нормален последователен поток на входа...")
    # Антон минава през входа (SCH-8F3A92C1)
    send_qr_scan("CAM-ENTRANCE-01", "MAIN_ENTRANCE", "SCH-8F3A92C1")
    time.sleep(2)
    
    print("\n[Сценарий 2] Опит за заемане на заета сесия на входа от друг ученик...")
    # Георги се опитва да сканира баджа си на входа, докато Антон все още има активна 60-секундна сесия там
    # Това трябва да бъде отхвърлено с 'kiosk_busy'
    send_qr_scan("CAM-ENTRANCE-01", "MAIN_ENTRANCE", "SCH-9A2C3B4D")
    time.sleep(1)

    print("\n[Сценарий 3] Паралелно сканиране от 3 различни камери в различни зони...")
    # 3 камери засичат баджове по едно и също време
    threads = []
    scans = [
        ("CAM-ENTRANCE-01", "MAIN_ENTRANCE", "SCH-8F3A92C1"),  # Антон (удължава своята сесия)
        ("CAM-LOBBY-01", "LOBBY", "SCH-9A2C3B4D"),            # Георги (нова сесия във фоайето)
        ("CAM-TEACHERS-01", "TEACHERS_ROOM", "SCH-7E1B2C3A")  # Мария (нова сесия в учителската стая)
    ]
    
    for cam, zone, token in scans:
        t = threading.Thread(target=send_qr_scan, args=(cam, zone, token))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    time.sleep(2)

    print("\n[Сценарий 4] Паралелни AI заявки (гласови команди) от тримата потребители...")
    # Трима потребители задават въпроси едновременно, натоварвайки AI мозъка на сървъра
    commands = [
        (1, "кога днес имам свободен час?"),  # Антон
        (2, "имам ли нови съобщения?"),       # Георги
        (3, "какви събития има днес?")        # Мария
    ]
    
    ai_threads = []
    for person_id, query in commands:
        t = threading.Thread(target=send_voice_command, args=(person_id, query))
        ai_threads.append(t)
        t.start()
        
    for t in ai_threads:
        t.join()

    print("\n[Сценарий 5] Затваряне на сесията на входа и ново сканиране...")
    # Ръчно затваряме сесията на входа
    try:
        res = requests.post(f"{SERVER_URL}/api/sessions/close", json={"zone_id": "MAIN_ENTRANCE"})
        print(f"[+] Session close response: {res.json()}")
    except Exception as e:
        print(f"[-] Session close failed: {e}")
        
    # Сега Георги вече може да се сканира на входа успешно
    send_qr_scan("CAM-ENTRANCE-01", "MAIN_ENTRANCE", "SCH-9A2C3B4D")

    print("\n" + "=" * 70)
    print("   SIMULATION COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_simulation()
