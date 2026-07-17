import sys
import os
import unittest
from datetime import datetime, date

# Добавяме основната папка в sys.path, за да можем да импортираме модулите
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from web.server import app, get_db, SessionLocal
from engine.db import init_db, seed_db, Person, Badge, Message, Timetable, Event, SystemEvent


class TestSchoolAIAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web.server import db_engine
        from engine.db import Base
        
        # Дропваме и пресъздаваме таблиците за чист старт на теста
        Base.metadata.drop_all(bind=db_engine)
        Base.metadata.create_all(bind=db_engine)
        
        cls.db = SessionLocal()
        seed_db(cls.db)
        
        # Обновяваме датите на разписанието, събитията и валидността на съобщенията към днешна дата,
        # за да може тестовете да минават успешно независимо от текущата дата.
        from datetime import timedelta, date as date_type
        today = date_type.today()
        now = datetime.now()
        
        # 1. Обновяваме разписанието за днес
        for t in cls.db.query(Timetable).all():
            t.date = today
        
        # 2. Обновяваме събитията за днес
        for e in cls.db.query(Event).all():
            # Заменяме датата с днешна, запазвайки часа
            e.start_time = datetime.combine(today, e.start_time.time())
            e.end_time = datetime.combine(today, e.end_time.time())
            
        # 3. Обновяваме съобщенията да са валидни
        for m in cls.db.query(Message).all():
            m.valid_until = now + timedelta(days=1)
            
        cls.db.commit()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_list_persons(self):
        """ Тест за получаване на списък с потребители """
        response = self.client.get("/api/persons")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertGreater(len(data), 0)
        # Проверяваме дали Антон Иванов е в списъка
        names = [p["full_name"] for p in data]
        self.assertIn("Антон Иванов", names)

    def test_02_detect_qr_valid(self):
        """ Тест за разпознаване на валиден QR бадж (Антон) """
        payload = {
            "camera_id": "CAM-ENTRANCE-01",
            "zone_id": "MAIN_ENTRANCE",
            "badge_token": "SCH-8F3A92C1",
            "confidence": 0.99
        }
        response = self.client.post("/api/detect_qr", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["person"]["name"], "Антон Иванов")
        self.assertEqual(data["person"]["role"], "student")
        
        # Тъй като в seed_db Мария е оставила съобщение за Антон,
        # проверете дали съобщението се връща като доставено
        self.assertIn("messages_delivered", data)
        self.assertGreater(len(data["messages_delivered"]), 0)
        self.assertIn("Антоне, моля те ела в учителската стая", data["messages_delivered"][0])

    def test_03_detect_qr_invalid(self):
        """ Тест за разпознаване на невалиден QR бадж """
        payload = {
            "camera_id": "CAM-ENTRANCE-01",
            "zone_id": "MAIN_ENTRANCE",
            "badge_token": "SCH-INVALID-TOKEN",
            "confidence": 0.95
        }
        response = self.client.post("/api/detect_qr", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["message"], "Неразпознат или неактивен бадж")

    def test_04_create_message(self):
        """ Тест за създаване на ново съобщение """
        # Намираме Антон и Георги от базата данни
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        georgi = self.db.query(Person).filter(Person.full_name == "Георги Петров").first()
        
        payload = {
            "sender_id": georgi.id,
            "recipient_id": anton.id,
            "text": "Тест съобщение от Георги до Антон за проекта.",
            "valid_hours": 12
        }
        response = self.client.post("/api/messages", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertTrue(data["success"])
        self.assertEqual(data["text"], "Тест съобщение от Георги до Антон за проекта.")
        
        # Проверяваме дали съобщението се вижда като чакащо
        pending_response = self.client.get(f"/api/messages/pending?person_id={anton.id}")
        self.assertEqual(pending_response.status_code, 200)
        pending_data = pending_response.json()
        
        texts = [m["text"] for m in pending_data]
        self.assertIn("Тест съобщение от Георги до Антон за проекта.", texts)

    def test_05_get_timetable(self):
        """ Тест за взимане на училищно разписание """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        
        # Проверяваме разписанието за днешна дата (seeded динамична дата)
        today_str = date.today().strftime("%Y-%m-%d")
        response = self.client.get(f"/api/persons/{anton.id}/timetable?date_str={today_str}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertGreater(len(data), 0)
        subjects = [t["subject"] for t in data]
        self.assertIn("Информатика", subjects)
        self.assertIn("Математика", subjects)

    def test_06_voice_command_room(self):
        """ Тест за гласова/текстова AI команда относно кабинет """
        payload = {
            "text_query": "къде се намира кабинет 304?"
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["intent"], "check_room")
        self.assertIn("Кабинет 304 се намира на третия етаж", data["response"])

    def test_07_cameras_and_points(self):
        """ Тест за камери и интерактивни точки """
        cam_res = self.client.get("/api/cameras")
        self.assertEqual(cam_res.status_code, 200)
        cams = cam_res.json()
        self.assertGreater(len(cams), 0)
        
        pt_res = self.client.get("/api/interaction_points")
        self.assertEqual(pt_res.status_code, 200)
        pts = pt_res.json()
        self.assertGreater(len(pts), 0)

    def test_08_voice_command_blocked(self):
        """ Тест за блокиране на неподходяща заявка """
        payload = {
            "text_query": "този скапан асистент е много глупав"
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["intent"], "blocked")
        self.assertIn("учтив тон", data["response"])

    def test_09_voice_command_leave_message(self):
        """ Тест за оставяне на съобщение чрез гласова команда """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        georgi = self.db.query(Person).filter(Person.full_name == "Георги Петров").first()
        
        payload = {
            "person_id": anton.id,
            "text_query": f"Кажи на Георги Петров, че го чакам след часовете в двора."
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["intent"], "leave_message")
        self.assertIn("Записах съобщението за Георги Петров", data["response"])
        
        # Проверяваме дали съобщението наистина се е записало в базата данни
        pending_response = self.client.get(f"/api/messages/pending?person_id={georgi.id}")
        self.assertEqual(pending_response.status_code, 200)
        pending_data = pending_response.json()
        texts = [m["text"] for m in pending_data]
        self.assertTrue(any("го чакам след часовете в двора" in t for t in texts))

    def test_10_voice_command_check_messages(self):
        """ Тест за проверка на съобщения с гласова команда """
        georgi = self.db.query(Person).filter(Person.full_name == "Георги Петров").first()
        
        payload = {
            "person_id": georgi.id,
            "text_query": "имам ли нови съобщения?"
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["intent"], "check_messages")
        self.assertIn("Имате", data["response"])

    def test_11_voice_command_free_periods(self):
        """ Тест за свободен час чрез гласова команда """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        
        payload = {
            "person_id": anton.id,
            "text_query": "кога днес имам свободен час?"
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["intent"], "check_free_periods")
        self.assertIn("свободен час", data["response"])

    def test_12_voice_command_events(self):
        """ Тест за показване на събития чрез гласова команда """
        payload = {
            "text_query": "какви събития има днес?"
        }
        response = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["intent"], "show_events")
        self.assertIn("Днес има следните събития", data["response"])

    def test_13_admin_events_crud(self):
        """ Тест за CRUD операции върху събития """
        res = self.client.get("/api/events")
        self.assertEqual(res.status_code, 200)
        initial_count = len(res.json())
        
        payload = {
            "title": "Тестово събитие",
            "description": "Тестово описание",
            "start_time": "2026-07-10T15:00:00",
            "end_time": "2026-07-10T16:00:00",
            "target_group": "All",
            "room": "Фоайе"
        }
        res_post = self.client.post("/api/events", json=payload)
        self.assertEqual(res_post.status_code, 200)
        self.assertTrue(res_post.json()["success"])
        event_id = res_post.json()["event_id"]
        
        res2 = self.client.get("/api/events")
        self.assertEqual(len(res2.json()), initial_count + 1)
        
        res_del = self.client.delete(f"/api/events/{event_id}")
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.json()["success"])

    def test_14_admin_timetable_crud(self):
        """ Тест за създаване и изтриване на часове от разписанието """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        payload = {
            "person_id": anton.id,
            "date": "2026-07-10",
            "period": 6,
            "start_time": "12:30",
            "end_time": "13:15",
            "subject": "Тест предмет",
            "class_name": "9Б",
            "room": "Кабинет 304"
        }
        res = self.client.post("/api/timetable", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        record_id = res.json()["record_id"]
        
        res_del = self.client.delete(f"/api/timetable/{record_id}")
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.json()["success"])

    def test_15_admin_badges(self):
        """ Тест за управление на баджове """
        res = self.client.get("/api/badges")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.json()), 0)
        
        # Създаваме нов тестов потребител, за да не деактивираме основния бадж на Антон
        test_person = Person(full_name="Тестов Бадж Потребител", role="student", class_name="9Б", active=True)
        self.db.add(test_person)
        self.db.commit()
        
        res_gen = self.client.post(f"/api/persons/{test_person.id}/badge")
        self.assertEqual(res_gen.status_code, 200)
        data = res_gen.json()
        self.assertTrue(data["success"])
        self.assertIn("SCH-", data["token"])
        badge_id = data["badge_id"]
        
        res_status = self.client.post(f"/api/badges/{badge_id}/status", json={"status": "lost"})
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.json()["status"], "lost")

    def test_16_admin_person_status(self):
        """ Тест за активиране/деактивиране на потребител """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        
        res = self.client.post(f"/api/persons/{anton.id}/status", json={"active": False})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["active"])
        
        res = self.client.post(f"/api/persons/{anton.id}/status", json={"active": True})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["active"])

    def test_17_admin_get_messages(self):
        """ Тест за взимане на всички съобщения """
        res = self.client.get("/api/messages")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.json()), 0)

    def test_18_duplicate_detection(self):
        """ Тест за филтриране на дублирани засичания от същата камера (cooldown) """
        # Първо засичане на Георги
        payload = {
            "camera_id": "CAM-LOBBY-01",
            "zone_id": "LOBBY",
            "badge_token": "SCH-9A2C3B4D",
            "confidence": 1.0
        }
        res1 = self.client.post("/api/detect_qr", json=payload)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["status"], "success")

        # Второ засичане веднага след това от същата камера
        res2 = self.client.post("/api/detect_qr", json=payload)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["status"], "ignored")
        self.assertEqual(res2.json()["reason"], "duplicate_same_camera")

    def test_19_session_control(self):
        """ Тест за управление на сесии и заключване на интерактивна точка """
        # Инициализираме сесия за Антон на точка MAIN_ENTRANCE
        payload_anton = {
            "camera_id": "CAM-ENTRANCE-01",
            "zone_id": "MAIN_ENTRANCE",
            "badge_token": "SCH-8F3A92C1",
            "confidence": 1.0
        }
        res_anton = self.client.post("/api/detect_qr", json=payload_anton)
        self.assertEqual(res_anton.status_code, 200)
        # Може да е success или ignored (ако предходен тест е заел точката, но setUpClass прави чиста БД)
        self.assertIn(res_anton.json()["status"], ["success", "ignored"])

        # Опит за засичане на Мария на същата точка докато сесията е активна (Мария не е сканирана наскоро)
        payload_maria = {
            "camera_id": "CAM-ENTRANCE-01",
            "zone_id": "MAIN_ENTRANCE",
            "badge_token": "SCH-7E1B2C3A",
            "confidence": 1.0
        }
        res_maria = self.client.post("/api/detect_qr", json=payload_maria)
        self.assertEqual(res_maria.status_code, 200)
        self.assertEqual(res_maria.json()["status"], "ignored")
        self.assertEqual(res_maria.json()["reason"], "kiosk_busy")

        # Затваряме сесията ръчно
        close_res = self.client.post("/api/sessions/close", json={"zone_id": "MAIN_ENTRANCE"})
        self.assertEqual(close_res.status_code, 200)
        self.assertTrue(close_res.json()["success"])

        # Отново опитваме да засечем Мария - вече трябва да е успешен
        res_maria_new = self.client.post("/api/detect_qr", json=payload_maria)
        self.assertEqual(res_maria_new.status_code, 200)
        self.assertEqual(res_maria_new.json()["status"], "success")

    def test_20_smart_name_matching(self):
        """ Тест за интелигентно и частично търсене на имена и двусмислия """
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        
        # 1. Търсене с титла и фамилия: "г-жа Димитрова" (трябва да намери Мария Димитрова)
        payload = {
            "person_id": anton.id,
            "text_query": "Остави съобщение на г-жа Димитрова, че съм готов."
        }
        res = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["intent"], "leave_message")
        self.assertIn("Записах съобщението за Мария Димитрова", res.json()["response"])

        # 2. Търсене с инициали/префикси: "А. Ивано" (трябва да намери Антон Иванов)
        georgi = self.db.query(Person).filter(Person.full_name == "Георги Петров").first()
        payload = {
            "person_id": georgi.id,
            "text_query": "Кажи на Ант. Ивано, че идвам."
        }
        res = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["intent"], "leave_message")
        self.assertIn("Записах съобщението за Антон Иванов", res.json()["response"])

        # 3. Тест за дублиране на имена (двусмислица)
        # Добавяме още един Антон в базата
        another_anton = Person(full_name="Антон Димитров", role="student", class_name="10А", active=True)
        self.db.add(another_anton)
        self.db.commit()

        payload = {
            "person_id": georgi.id,
            "text_query": "Остави съобщение за Антон, че проектът е супер."
        }
        res = self.client.post("/api/voice_command", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Намерих няколко съвпадения: Антон Иванов (9Б) или Антон Димитров (10А)", res.json()["response"])


if __name__ == "__main__":
    unittest.main()
