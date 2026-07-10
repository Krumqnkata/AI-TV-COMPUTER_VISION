import sys
import os
import unittest
from datetime import datetime, date

# Добавяме основната папка в sys.path, за да можем да импортираме модулите
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from web.server import app, get_db, SessionLocal
from engine.db import init_db, seed_db, Person, Badge, Message, Timetable


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
        
        # Проверяваме разписанието за 10 Юли 2026г. ( seeded дата )
        response = self.client.get(f"/api/persons/{anton.id}/timetable?date_str=2026-07-10")
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
        
        anton = self.db.query(Person).filter(Person.full_name == "Антон Иванов").first()
        res_gen = self.client.post(f"/api/persons/{anton.id}/badge")
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


if __name__ == "__main__":
    unittest.main()
