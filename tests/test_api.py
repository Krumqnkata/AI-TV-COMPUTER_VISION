"""Isolated API tests.

The database URL is set before importing the application, so this suite can
never drop or modify ``data/school_ai.db``.
"""

import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


TEST_DEVICE_KEY = "test-device-key-with-sufficient-entropy"
_temp_dir = tempfile.TemporaryDirectory(prefix="school_ai_tests_")
_database_path = Path(_temp_dir.name) / "school_ai_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_database_path.as_posix()}"
os.environ["DEVICE_API_KEY"] = TEST_DEVICE_KEY
os.environ["ADMIN_SECRET_KEY"] = "test-admin-session-secret-with-sufficient-length"
os.environ["COOKIE_SECURE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from engine.auth import get_password_hash  # noqa: E402
from engine.db import Badge, Base, DeliveryReceipt, Message, Person, hash_token, now_bg, seed_db  # noqa: E402
from web.database import SessionLocal, db_engine  # noqa: E402
from web.security import require_admin  # noqa: E402
from web.server import app  # noqa: E402
from web.services.runtime import runtime_registry  # noqa: E402


DEVICE_HEADERS = {"X-Device-Key": TEST_DEVICE_KEY}


class TestSchoolAIAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(bind=db_engine)
        Base.metadata.create_all(bind=db_engine)
        cls.db = SessionLocal()
        seed_db(cls.db)
        cls.admin = cls.db.query(Person).filter(Person.role == "admin").first()
        cls.admin.password_hash = get_password_hash("test-admin-password")
        cls.db.commit()
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        cls.client_context.__exit__(None, None, None)
        cls.db.close()
        db_engine.dispose()
        _temp_dir.cleanup()

    def setUp(self):
        runtime_registry.clear()
        self.db.expire_all()
        for message in self.db.query(Message).all():
            message.status = "active"
            message.delivered_at = None
            message.valid_until = now_bg() + timedelta(days=1)
        self.db.query(DeliveryReceipt).delete()
        self.db.commit()

    def _person(self, name: str) -> Person:
        return self.db.query(Person).filter(Person.full_name == name).one()

    def _scan(self, token="SCH-8F3A92C1", camera="CAM-ENTRANCE-01", zone="MAIN_ENTRANCE"):
        return self.client.post(
            "/api/detect_qr",
            headers=DEVICE_HEADERS,
            json={"camera_id": camera, "zone_id": zone, "badge_token": token, "confidence": 0.99},
        )

    def test_device_authentication_is_required(self):
        response = self.client.post(
            "/api/detect_qr",
            json={"camera_id": "CAM-ENTRANCE-01", "zone_id": "MAIN_ENTRANCE", "badge_token": "SCH-8F3A92C1"},
        )
        self.assertEqual(response.status_code, 403)  # rejected by CSRF before device dependency
        response = self.client.post(
            "/api/detect_qr",
            headers={"X-Device-Key": "wrong"},
            json={"camera_id": "CAM-ENTRANCE-01", "zone_id": "MAIN_ENTRANCE", "badge_token": "SCH-8F3A92C1"},
        )
        self.assertEqual(response.status_code, 401)

    def test_restored_read_endpoints(self):
        for path in ["/api/persons", "/api/cameras", "/api/interaction_points"]:
            response = self.client.get(path, headers=DEVICE_HEADERS)
            self.assertEqual(response.status_code, 200, path)
            self.assertGreater(len(response.json()), 0, path)

        anton = self._person("Антон Иванов")
        timetable = self.client.get(
            f"/api/persons/{anton.id}/timetable",
            headers=DEVICE_HEADERS,
            params={"date_str": now_bg().date().isoformat()},
        )
        self.assertEqual(timetable.status_code, 200)
        self.assertGreater(len(timetable.json()), 0)

    def test_websocket_registration_targeted_delivery_and_ack(self):
        with self.client.websocket_connect("/ws") as websocket, self.client.websocket_connect("/ws") as other_zone:
            websocket.send_json({
                "type": "register",
                "device_key": TEST_DEVICE_KEY,
                "screen_id": "SCR-ENTRANCE-01",
                "zone_id": "MAIN_ENTRANCE",
            })
            self.assertEqual(websocket.receive_json()["type"], "registered")
            other_zone.send_json({
                "type": "register",
                "device_key": TEST_DEVICE_KEY,
                "screen_id": "SCR-LIBRARY-01",
                "zone_id": "LIBRARY",
            })
            self.assertEqual(other_zone.receive_json()["type"], "registered")

            response = self._scan()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "success")
            event = websocket.receive_json()
            self.assertEqual(event["type"], "badge_detected")
            self.assertEqual(event["data"]["name"], "Антон Иванов")
            self.assertTrue(event["data"]["delivery_id"])

            # The unrelated screen must not receive the personal event. Its
            # next response should therefore be the reply to this ping.
            other_zone.send_json({"type": "ping"})
            self.assertEqual(other_zone.receive_json()["type"], "pong")

            websocket.send_json({
                "type": "ack",
                "delivery_id": event["data"]["delivery_id"],
                "message_ids": event["data"]["message_ids"],
            })
            ack = websocket.receive_json()
            self.assertEqual(ack["type"], "ack_result")
            self.assertTrue(ack["data"]["success"])

        self.db.expire_all()
        anton = self._person("Антон Иванов")
        statuses = [m.status for m in self.db.query(Message).filter(Message.recipient_id == anton.id).all()]
        self.assertIn("delivered", statuses)

    def test_duplicate_and_busy_session_rules(self):
        first = self._scan()
        self.assertEqual(first.json()["status"], "success")
        duplicate = self._scan()
        self.assertEqual(duplicate.json(), {"status": "ignored", "reason": "duplicate_same_camera"})

        maria = self._scan(token="SCH-7E1B2C3A")
        self.assertEqual(maria.json()["reason"], "kiosk_busy")
        closed = self.client.post(
            "/api/sessions/close",
            headers=DEVICE_HEADERS,
            json={"screen_id": "SCR-ENTRANCE-01", "zone_id": "MAIN_ENTRANCE"},
        )
        self.assertTrue(closed.json()["success"])
        maria_after = self._scan(token="SCH-7E1B2C3A")
        self.assertEqual(maria_after.json()["status"], "success")

    def test_message_sender_must_have_active_session(self):
        anton = self._person("Антон Иванов")
        georgi = self._person("Георги Петров")
        payload = {
            "sender_id": anton.id,
            "recipient_id": georgi.id,
            "text": "Ще се видим след часа.",
            "zone_id": "MAIN_ENTRANCE",
            "screen_id": "SCR-ENTRANCE-01",
        }
        denied = self.client.post("/api/messages", headers=DEVICE_HEADERS, json=payload)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self._scan().json()["status"], "success")
        created = self.client.post("/api/messages", headers=DEVICE_HEADERS, json=payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["text"], payload["text"])

    def test_voice_command_uses_active_identity(self):
        anton = self._person("Антон Иванов")
        payload = {
            "person_id": anton.id,
            "text_query": "къде е кабинет 304",
            "zone_id": "MAIN_ENTRANCE",
            "screen_id": "SCR-ENTRANCE-01",
        }
        self.assertEqual(self.client.post("/api/voice_command", headers=DEVICE_HEADERS, json=payload).status_code, 403)
        self._scan()
        response = self.client.post("/api/voice_command", headers=DEVICE_HEADERS, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "check_room")

    def test_csrf_requires_header_for_browser_mutations(self):
        page = self.client.get("/")
        token = page.cookies.get("csrf_token") or self.client.cookies.get("csrf_token")
        no_header = self.client.post("/api/persons", json={"full_name": "Тест Потребител", "role": "student"})
        self.assertEqual(no_header.status_code, 403)
        with_header = self.client.post(
            "/api/persons",
            headers={"X-CSRF-Token": token},
            json={"full_name": "Тест Потребител", "role": "student"},
        )
        self.assertEqual(with_header.status_code, 401)

    def test_admin_login_accepts_argon2_password(self):
        self.client.get("/admin/login")
        response = self.client.post(
            "/admin/login",
            data={"username": self.admin.full_name, "password": "test-admin-password"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        self.client.cookies.delete("session")

    def test_admin_crud_contracts(self):
        app.dependency_overrides[require_admin] = lambda: self.admin
        self.client.get("/")
        token = self.client.cookies.get("csrf_token")
        headers = {"X-CSRF-Token": token}
        try:
            created = self.client.post(
                "/api/persons",
                headers=headers,
                json={"full_name": "Нов Тестов Ученик", "role": "student", "class_name": "8А"},
            )
            self.assertEqual(created.status_code, 200)
            person_id = created.json()["person_id"]
            badge = self.client.post(f"/api/persons/{person_id}/badge", headers=headers)
            self.assertEqual(badge.status_code, 200)
            self.assertTrue(badge.json()["token"].startswith("SCH-"))
            deleted = self.client.delete(f"/api/persons/{person_id}", headers=headers)
            self.assertEqual(deleted.status_code, 200)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
