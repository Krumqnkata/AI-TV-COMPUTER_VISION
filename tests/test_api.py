"""Isolated API tests backed by an Alembic-built temporary database."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


TEST_DEVICE_KEY = "test-device-key-with-sufficient-entropy"
_temp_dir = tempfile.TemporaryDirectory(prefix="school_ai_tests_")
_database_path = Path(_temp_dir.name) / "school_ai_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_database_path.as_posix()}"
os.environ["DEVICE_API_KEY"] = TEST_DEVICE_KEY
os.environ["ADMIN_SECRET_KEY"] = "test-admin-session-secret-with-sufficient-length"
os.environ["SETTINGS_MASTER_KEY"] = "test-settings-master-key-with-sufficient-length"
os.environ["COOKIE_SECURE"] = "false"
os.environ["BACKUP_DIR"] = str(Path(_temp_dir.name) / "backups")

from fastapi.testclient import TestClient  # noqa: E402
from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402


_test_migration_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
alembic_command.upgrade(_test_migration_config, "head")


from engine.auth import get_password_hash  # noqa: E402
from engine.admin_models import AdminAuditEvent, Announcement, BackupRecord, DeviceCommand, DeviceNode, EncryptedSecret, OperationalJobRun, StaffAccount, StaffRole, SystemSetting  # noqa: E402
from engine.db import Badge, Base, DeliveryReceipt, InteractionPoint, Message, Person, SystemEvent, Timetable, hash_token, now_bg  # noqa: E402
from tests.fixtures import seed_test_data  # noqa: E402
from web.database import SessionLocal, assert_schema_current, db_engine  # noqa: E402
from web.security import require_admin  # noqa: E402
from web.server import app  # noqa: E402
from web.services.runtime import runtime_registry  # noqa: E402
from web.services.admin_control import (  # noqa: E402
    authenticate_staff,
    ensure_admin_foundation,
    get_setting,
    read_secret,
    save_secret,
    update_settings,
)
from web.services.backups import create_sqlite_backup, verify_backup  # noqa: E402
from web.services.device_control import available_safe_commands, create_enrollment_token, queue_command  # noqa: E402
from web.services.imports import execute_schedule_import, preview_schedule_import  # noqa: E402
from web.services.operations import collect_operational_warnings, run_operations_cycle  # noqa: E402
from web.services.maintenance import run_maintenance_job  # noqa: E402
from web.services.privacy import execute_retention_cleanup, retention_preview  # noqa: E402
from utils.config import Config as AppConfig  # noqa: E402


DEVICE_HEADERS = {"X-Device-Key": TEST_DEVICE_KEY}
LEGACY_TABLE_NAMES = {
    "persons", "badges", "interaction_points", "cameras", "messages",
    "timetable", "events", "system_events", "delivery_receipts",
}
CONTROL_TABLE_NAMES = {
    "staff_accounts",
    "device_nodes",
    "system_settings",
    "operational_job_runs",
}


class TestSchoolAIAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SessionLocal()
        seed_test_data(cls.db)
        cls.admin = cls.db.query(Person).filter(Person.role == "admin").first()
        cls.admin.password_hash = get_password_hash("test-admin-password")
        cls.db.commit()
        ensure_admin_foundation(cls.db)
        cls.staff = cls.db.query(StaffAccount).filter(StaffAccount.linked_person_id == cls.admin.id).one()
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
        self.db.query(Announcement).delete()
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

    def _login_admin(self):
        self.client.get("/admin/login")
        response = self.client.post(
            "/admin/login",
            data={"username": self.staff.username, "password": "test-admin-password"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        self.assertIn(response.status_code, (302, 303))
        return response

    def _pair_pwa(
        self,
        profile: str,
        identifier: str,
        *,
        point_name: str = "Главен вход - Екран",
        initial_config: dict | None = None,
    ):
        point = self.db.query(InteractionPoint).filter(
            InteractionPoint.name == point_name,
        ).one()
        _token, raw_token = create_enrollment_token(
            self.db,
            self.staff,
            label=f"{profile} test",
            device_type=profile,
            expected_identifier=identifier,
            zone_id=point.zone_id,
            screen_id=point.screen_id,
            interaction_point_id=point.id,
            initial_config=initial_config or {},
        )
        self.client.get(f"/pair?profile={profile}")
        csrf_token = self.client.cookies.get("csrf_token")
        return self.client.post(
            f"/api/{profile}/pair",
            headers={
                "X-Enrollment-Token": raw_token,
                "X-CSRF-Token": csrf_token,
            },
            json={
                "identifier": identifier,
                "name": f"{profile.title()} test device",
                "software_version": "pwa-test",
            },
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

    def test_cross_platform_pwa_pages_and_manifests(self):
        kiosk = self.client.get("/kiosk")
        self.assertEqual(kiosk.status_code, 200)
        self.assertIn('data-profile="kiosk"', kiosk.text)
        self.assertIn("camera=(self)", kiosk.headers["permissions-policy"])
        self.assertIn("default-src 'self'", kiosk.headers["content-security-policy"])

        screen = self.client.get("/screen")
        self.assertEqual(screen.status_code, 200)
        self.assertIn('data-profile="screen"', screen.text)
        self.assertIn("camera=()", screen.headers["permissions-policy"])

        kiosk_manifest = self.client.get("/manifest-kiosk.webmanifest").json()
        screen_manifest = self.client.get("/manifest-screen.webmanifest").json()
        self.assertEqual(kiosk_manifest["id"], "/pwa/kiosk")
        self.assertEqual(kiosk_manifest["start_url"], "/kiosk")
        self.assertEqual(screen_manifest["id"], "/pwa/screen")
        self.assertEqual(screen_manifest["start_url"], "/screen")
        self.assertNotEqual(kiosk_manifest["id"], screen_manifest["id"])

        service_worker = self.client.get("/kiosk-sw.js")
        self.assertEqual(service_worker.status_code, 200)
        self.assertEqual(service_worker.headers["service-worker-allowed"], "/")

    def test_health_probes_report_process_and_dependency_state(self):
        live = self.client.get(
            "/health/live",
            headers={"X-Request-ID": "health-contract-123"},
        )
        self.assertEqual(live.status_code, 200, live.text)
        self.assertTrue(live.json()["alive"])
        self.assertEqual(live.headers["cache-control"], "no-store")
        self.assertEqual(live.headers["x-request-id"], "health-contract-123")

        ready = self.client.get("/health/ready")
        self.assertEqual(ready.status_code, 200, ready.text)
        payload = ready.json()
        self.assertTrue(payload["ready"])
        self.assertTrue(payload["checks"]["database"]["ok"])
        self.assertTrue(payload["checks"]["migrations"]["ok"])
        self.assertTrue(payload["checks"]["operations_monitor"]["ok"])
        self.assertNotIn("password", ready.text.casefold())

        metrics = self.client.get("/health/metrics")
        self.assertEqual(metrics.status_code, 200, metrics.text)
        self.assertIn("school_ai_http_requests_total", metrics.text)
        self.assertIn("school_ai_http_request_duration_seconds_sum", metrics.text)
        self.assertIn("school_ai_active_websockets", metrics.text)
        self.assertIn("school_ai_delayed_command_acks", metrics.text)
        self.assertNotIn("device_id=", metrics.text)

    def test_pwa_pair_bootstrap_and_unpair_use_separate_http_only_credentials(self):
        response = self._pair_pwa("kiosk", "pwa-kiosk-cookie-test")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("device_key", payload)
        self.assertEqual(payload["zone_id"], "MAIN_ENTRANCE")
        self.assertEqual(payload["screen_id"], "SCR-ENTRANCE-01")
        self.assertTrue(payload["camera_id"].startswith("CAM-DEVICE-"))
        cookies = response.headers.get_list("set-cookie")
        self.assertTrue(any("school_ai_kiosk_device_key=" in item and "HttpOnly" in item for item in cookies))
        self.assertFalse(any("school_ai_screen_device_key=" in item for item in cookies))

        bootstrap = self.client.get("/api/kiosk/bootstrap")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.json()["profile"], "kiosk")
        self.assertEqual(bootstrap.json()["device_id"], "pwa-kiosk-cookie-test")

        csrf_token = self.client.cookies.get("csrf_token")
        unpair = self.client.post(
            "/api/kiosk/unpair",
            headers={"X-CSRF-Token": csrf_token},
            json={},
        )
        self.assertEqual(unpair.status_code, 200)
        self.assertEqual(self.client.get("/api/kiosk/bootstrap").status_code, 401)

    def test_public_screen_feed_is_scoped_cacheable_and_has_etag(self):
        self.db.add_all([
            Announcement(
                title="Публична новина",
                body="Информация за всички.",
                audience="all",
                priority="normal",
                publish_from=now_bg() - timedelta(minutes=1),
                published=True,
            ),
            Announcement(
                title="Само за 8А",
                body="Ограничена информация.",
                audience="8А",
                priority="normal",
                publish_from=now_bg() - timedelta(minutes=1),
                published=True,
            ),
        ])
        self.db.commit()
        paired = self._pair_pwa(
            "screen",
            "pwa-public-screen-test",
            initial_config={
                "screen_mode": "public",
                "screen_audience": "all",
                "show_announcements": True,
                "show_events": False,
                "show_substitutions": False,
            },
        )
        self.assertEqual(paired.status_code, 200, paired.text)

        response = self.client.get("/api/screen/feed")
        self.assertEqual(response.status_code, 200)
        titles = [slide["title"] for slide in response.json()["slides"]]
        self.assertIn("Публична новина", titles)
        self.assertNotIn("Само за 8А", titles)
        self.assertIn("etag", response.headers)
        self.assertNotIn("person_id", response.text)

        unchanged = self.client.get(
            "/api/screen/feed",
            headers={"If-None-Match": response.headers["etag"]},
        )
        self.assertEqual(unchanged.status_code, 304)

    def test_profile_websockets_target_kiosk_and_isolate_public_screen(self):
        public_screen = self._pair_pwa(
            "screen",
            "pwa-public-ws-screen",
            initial_config={"screen_mode": "public"},
        )
        self.assertEqual(public_screen.status_code, 200, public_screen.text)
        kiosk = self._pair_pwa("kiosk", "pwa-ws-kiosk")
        self.assertEqual(kiosk.status_code, 200, kiosk.text)

        with (
            self.client.websocket_connect("/ws/screen") as screen_socket,
            self.client.websocket_connect("/ws/kiosk") as kiosk_socket,
        ):
            screen_registration = screen_socket.receive_json()
            kiosk_registration = kiosk_socket.receive_json()
            self.assertEqual(screen_registration["data"]["screen_mode"], "public")
            self.assertEqual(kiosk_registration["data"]["profile"], "kiosk")

            csrf_token = self.client.cookies.get("csrf_token")
            detection = self.client.post(
                "/api/kiosk/detect",
                headers={"X-CSRF-Token": csrf_token},
                json={"badge_token": "SCH-8F3A92C1", "confidence": 0.99},
            )
            self.assertEqual(detection.status_code, 200, detection.text)
            self.assertEqual(detection.json()["status"], "success")

            event = kiosk_socket.receive_json()
            self.assertEqual(event["type"], "badge_detected")
            self.assertEqual(event["data"]["name"], "Антон Иванов")

            screen_socket.send_json({"type": "ping"})
            self.assertEqual(screen_socket.receive_json()["type"], "pong")

            kiosk_socket.send_json({
                "type": "ack",
                "delivery_id": event["data"]["delivery_id"],
                "message_ids": event["data"]["message_ids"],
            })
            acknowledgment = kiosk_socket.receive_json()
            self.assertTrue(acknowledgment["data"]["success"])

        self.db.expire_all()
        screen_device = self.db.query(DeviceNode).filter(
            DeviceNode.identifier == "pwa-public-ws-screen",
        ).one()
        kiosk_device = self.db.query(DeviceNode).filter(
            DeviceNode.identifier == "pwa-ws-kiosk",
        ).one()
        self.assertIsNotNone(screen_device.last_websocket_at)
        self.assertIsNotNone(kiosk_device.last_websocket_at)

    def test_admin_command_wakes_exact_pwa_device_over_websocket(self):
        paired = self._pair_pwa("kiosk", "pwa-command-wakeup")
        self.assertEqual(paired.status_code, 200, paired.text)
        self.db.expire_all()
        device = self.db.query(DeviceNode).filter(
            DeviceNode.identifier == "pwa-command-wakeup",
        ).one()
        self.assertIn("request_diagnostics", available_safe_commands(device))

        self._login_admin()
        with self.client.websocket_connect("/ws/kiosk") as socket:
            self.assertEqual(socket.receive_json()["type"], "registered")
            response = self.client.post(
                f"/admin/devices/{device.id}/command",
                data={"command": "request_diagnostics"},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303, response.text)
            notification = socket.receive_json()
            self.assertEqual(notification["type"], "device_command_available")
            self.assertIsInstance(notification["data"]["command_id"], int)
        history = self.client.get("/admin/devices")
        self.assertEqual(history.status_code, 200)
        self.assertIn("Последни дистанционни команди", history.text)
        self.assertIn("Изискай актуална диагностика", history.text)
        self.assertIn("pending", history.text)
        self.client.cookies.delete("session")

        screen = DeviceNode(
            identifier="command-profile-screen",
            name="Command profile screen",
            device_type="screen",
        )
        self.db.add(screen)
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "не се поддържа"):
            queue_command(self.db, screen, "test_audio", self.staff)
        self.db.delete(screen)
        self.db.commit()

    def test_device_heartbeat_diagnostics_are_visible_to_admin(self):
        paired = self._pair_pwa("kiosk", "pwa-diagnostics-kiosk")
        self.assertEqual(paired.status_code, 200, paired.text)
        csrf_token = self.client.cookies.get("csrf_token")
        heartbeat = self.client.post(
            "/api/kiosk/device/heartbeat",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "status": "online",
                "software_version": "pwa-test-diagnostics",
                "capabilities": ["camera", "qr", "screen"],
                "diagnostics": {
                    "browser": "Test Browser 1",
                    "platform": "Windows",
                    "language": "bg-BG",
                    "secure_context": True,
                    "standalone": False,
                    "camera_api": True,
                    "camera_permission": "granted",
                    "camera_status": "active",
                    "scanner_engine": "native",
                    "barcode_detector": True,
                    "service_worker": True,
                    "indexed_db": True,
                    "web_socket": True,
                    "speech_synthesis": True,
                    "viewport_width": 1280,
                    "viewport_height": 720,
                },
            },
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

        self.db.expire_all()
        device = self.db.query(DeviceNode).filter(
            DeviceNode.identifier == "pwa-diagnostics-kiosk",
        ).one()
        self.assertIn('"camera_status": "active"', device.diagnostics_json)
        self.assertNotIn("user_agent", device.diagnostics_json)

        self._login_admin()
        page = self.client.get("/admin/diagnostics")
        self.assertEqual(page.status_code, 200, page.text[:500])
        self.assertIn("Test Browser 1", page.text)
        self.assertIn("pwa-diagnostics-kiosk", page.text)
        self.assertIn("Camera API", page.text)
        self.client.cookies.delete("session")

    def test_operations_cycle_marks_offline_and_builds_ack_backup_warnings(self):
        stale_time = now_bg() - timedelta(minutes=10)
        device = DeviceNode(
            identifier="operations-stale-device",
            name="Старо тестово устройство",
            device_type="screen",
            active=True,
            status="online",
            last_seen_at=stale_time,
        )
        self.db.add(device)
        self.db.flush()
        command = DeviceCommand(
            device_id=device.id,
            command="refresh_config",
            status="delivered",
            created_at=stale_time,
            delivered_at=stale_time,
        )
        delivery = DeliveryReceipt(
            delivery_id="operations-stale-delivery",
            person_id=self.admin.id,
            screen_id="TEST-SCREEN",
            zone_id="TEST-ZONE",
            message_ids_json="[]",
            status="pending",
            created_at=stale_time,
        )
        self.db.add_all([command, delivery])
        self.db.commit()

        try:
            self.assertGreaterEqual(run_operations_cycle(), 1)
            self.db.expire_all()
            self.assertEqual(self.db.get(DeviceNode, device.id).status, "offline")

            warnings = collect_operational_warnings(
                self.db,
                current_time=now_bg() + timedelta(days=3),
            )
            codes = {warning.code for warning in warnings}
            self.assertIn(f"heartbeat-stale-{device.id}", codes)
            self.assertIn("commands-without-ack", codes)
            self.assertIn("deliveries-without-ack", codes)
            self.assertTrue(
                {"backup-missing", "backup-stale"}.intersection(codes),
                codes,
            )
        finally:
            self.db.query(DeviceCommand).filter(DeviceCommand.id == command.id).delete()
            self.db.query(DeliveryReceipt).filter(
                DeliveryReceipt.id == delivery.id,
            ).delete()
            self.db.query(DeviceNode).filter(DeviceNode.id == device.id).delete()
            self.db.commit()

    def test_scheduler_records_success_failure_audit_and_warnings(self):
        success = run_maintenance_job("backup")
        self.assertEqual(success.status, "completed")
        self.db.expire_all()
        completed = self.db.get(OperationalJobRun, success.run_id)
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.finished_at)

        with patch(
            "web.services.maintenance.create_database_backup",
            side_effect=RuntimeError("sensitive-test-detail"),
        ):
            with self.assertRaises(RuntimeError):
                run_maintenance_job("backup")

        self.db.expire_all()
        failed = self.db.query(OperationalJobRun).filter(
            OperationalJobRun.job_name == "backup",
            OperationalJobRun.status == "failed",
        ).order_by(OperationalJobRun.id.desc()).one()
        self.assertEqual(failed.error_type, "RuntimeError")
        self.assertNotIn("sensitive-test-detail", failed.summary_json or "")
        failure_audit = self.db.query(AdminAuditEvent).filter(
            AdminAuditEvent.action == "operations.job_failed",
            AdminAuditEvent.entity_id == str(failed.id),
        ).one()
        self.assertIsNone(failure_audit.actor_staff_id)
        self.assertNotIn("sensitive-test-detail", failure_audit.changes_json or "")

        try:
            update_settings(
                self.db,
                {"operations.maintenance_enabled": True},
                self.staff,
            )
            with patch(
                "web.services.operations.disk_space_report",
                return_value={
                    "available": True,
                    "path": "test",
                    "free_mb": 1,
                    "total_mb": 100,
                    "used_percent": 99,
                },
            ):
                codes = {
                    warning.code
                    for warning in collect_operational_warnings(self.db)
                }
            self.assertIn("disk-space-low", codes)
            self.assertIn("maintenance-failed-backup", codes)
            self.assertIn("maintenance-never-retention", codes)
        finally:
            update_settings(
                self.db,
                {"operations.maintenance_enabled": False},
                self.staff,
            )

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

    def test_websocket_registration_with_individual_device_credentials(self):
        _token, raw_token = create_enrollment_token(
            self.db,
            self.staff,
            label="Индивидуален WS екран",
            device_type="screen",
            expected_identifier="ws-screen-test",
            zone_id="WS_TEST_ZONE",
            screen_id="WS_TEST_SCREEN",
        )
        enrolled = self.client.post(
            "/api/devices/enroll",
            headers={"X-Enrollment-Token": raw_token},
            json={
                "identifier": "ws-screen-test",
                "name": "WS тестов екран",
                "device_type": "screen",
                "capabilities": ["screen"],
            },
        ).json()
        with self.client.websocket_connect("/ws") as websocket:
            websocket.send_json({
                "type": "register",
                "device_id": enrolled["device_id"],
                "device_key": enrolled["device_key"],
                "screen_id": "WS_TEST_SCREEN",
                "zone_id": "WS_TEST_ZONE",
            })
            response = websocket.receive_json()
            self.assertEqual(response["type"], "registered")
            self.assertEqual(response["data"]["device_id"], "ws-screen-test")
            websocket.send_json({"type": "ping"})
            self.assertEqual(websocket.receive_json()["type"], "pong")

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

    def test_session_close_uses_all_provided_scope_fields(self):
        now = now_bg()
        first_person = self._person("Антон Иванов")
        second_person = self._person("Георги Петров")
        runtime_registry.acquire_session(
            first_person.id,
            "SHARED_ZONE",
            101,
            "SCREEN-A",
            now,
        )
        runtime_registry.acquire_session(
            second_person.id,
            "SHARED_ZONE",
            102,
            "SCREEN-B",
            now,
        )

        closed = runtime_registry.close(
            zone_id="SHARED_ZONE",
            screen_id="SCREEN-A",
        )
        self.assertEqual([session.person_id for session in closed], [first_person.id])
        remaining = runtime_registry.current_session(
            now_bg(),
            zone_id="SHARED_ZONE",
            screen_id="SCREEN-B",
        )
        self.assertIsNotNone(remaining)
        self.assertEqual(remaining.person_id, second_person.id)

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

    def test_admin_foundation_settings_and_encrypted_secrets(self):
        self.assertGreaterEqual(self.db.query(StaffRole).count(), 4)
        self.assertGreater(self.db.query(SystemSetting).count(), 10)
        self.assertIn("superadmin", {role.code for role in self.staff.roles})
        update_settings(self.db, {"school.name": "Тестово училище"}, self.staff)
        self.assertEqual(get_setting(self.db, "school.name"), "Тестово училище")
        saved = save_secret(self.db, "gemini.api_key", "very-secret-test-key", self.staff)
        self.assertNotIn("very-secret-test-key", saved.ciphertext)
        self.assertEqual(read_secret(self.db, "gemini.api_key"), "very-secret-test-key")
        self.assertEqual(self.db.query(EncryptedSecret).filter_by(key="gemini.api_key").count(), 1)

    def test_admin_dashboard_is_bulgarian_and_permission_aware(self):
        self._login_admin()
        response = self.client.get("/admin/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Контролен център", response.text)
        self.assertIn("Бързи действия", response.text)
        self.assertNotIn("password_hash", response.text)
        health = self.client.get("/admin/dashboard/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("Сървър и база", health.text)
        self.client.cookies.delete("session")

    def test_admin_workflow_pages_render_without_exposing_secrets(self):
        self._login_admin()
        for path in [
            "/admin/settings",
            "/admin/staff",
            "/admin/badges/issue",
            "/admin/schedule-import",
            "/admin/devices",
            "/admin/diagnostics",
            "/admin/privacy",
            "/admin/backups",
            "/admin/person/list",
            "/admin/timetable/list",
            "/admin/announcement/list",
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text[:300]}")
            self.assertNotIn("password_hash", response.text, path)
            self.assertNotIn("ciphertext", response.text, path)
        self.client.cookies.delete("session")

    def test_admin_backup_action_creates_verified_archive(self):
        self._login_admin()
        before = self.db.query(BackupRecord).count()

        response = self.client.post(
            "/admin/backups/generate",
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303, response.text[:300])
        self.assertTrue(response.headers["location"].startswith("/admin/backups?ok="))
        self.db.expire_all()
        self.assertEqual(self.db.query(BackupRecord).count(), before + 1)
        record = self.db.query(BackupRecord).order_by(BackupRecord.id.desc()).first()
        self.assertEqual(record.status, "verified")
        self.assertTrue(Path(record.storage_path).is_file())
        self.client.cookies.delete("session")

    def test_staff_login_lockout(self):
        account = StaffAccount(
            username="locked-test-user",
            display_name="Заключван тест",
            password_hash=get_password_hash("correct-test-password"),
            active=True,
        )
        account.roles = [self.db.query(StaffRole).filter(StaffRole.code == "teacher_editor").one()]
        self.db.add(account)
        self.db.commit()
        for _ in range(5):
            self.assertIsNone(authenticate_staff(self.db, account.username, "wrong-password"))
        self.db.refresh(account)
        self.assertIsNotNone(account.locked_until)
        self.assertIsNone(authenticate_staff(self.db, account.username, "correct-test-password"))

    def test_managed_device_enrollment_heartbeat_config_command_and_ack(self):
        token, raw_token = create_enrollment_token(
            self.db,
            self.staff,
            label="Тестов екран",
            device_type="kiosk",
            expected_identifier="test-kiosk-01",
            zone_id="TEST_ZONE",
            screen_id="TEST_SCREEN",
        )
        response = self.client.post(
            "/api/devices/enroll",
            headers={"X-Enrollment-Token": raw_token},
            json={
                "identifier": "test-kiosk-01",
                "name": "Тестов киоск",
                "device_type": "kiosk",
                "capabilities": ["screen"],
                "software_version": "test-1",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        credentials = {
            "X-Device-ID": response.json()["device_id"],
            "X-Device-Key": response.json()["device_key"],
        }
        heartbeat = self.client.post(
            "/api/devices/heartbeat",
            headers=credentials,
            json={"status": "online", "software_version": "test-2", "capabilities": ["screen"]},
        )
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        config = self.client.get("/api/devices/config", headers=credentials)
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["zone_id"], "TEST_ZONE")
        self.db.expire_all()
        device = self.db.query(DeviceNode).filter(DeviceNode.identifier == "test-kiosk-01").one()
        command = queue_command(self.db, device, "test_screen", self.staff)
        pending = self.client.get("/api/devices/commands/pending", headers=credentials)
        self.assertEqual([item["id"] for item in pending.json()], [command.id])
        ack = self.client.post(
            f"/api/devices/commands/{command.id}/ack",
            headers=credentials,
            json={"success": True, "result": {"pixels": "ok"}},
        )
        self.assertEqual(ack.status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.get(DeviceCommand, command.id).status, "acknowledged")
        self.assertIsNotNone(token.used_at)

    def test_fresh_alembic_migration_builds_complete_schema(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_fresh_migration_") as migration_dir:
            database_path = Path(migration_dir) / "fresh.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            migration_engine = create_engine(database_url)
            original_url = AppConfig.DATABASE_URL
            try:
                AppConfig.DATABASE_URL = database_url
                migration_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
                alembic_command.upgrade(migration_config, "head")
            finally:
                AppConfig.DATABASE_URL = original_url
            tables = set(inspect(migration_engine).get_table_names())
            device_columns = {
                item["name"]
                for item in inspect(migration_engine).get_columns("device_nodes")
            }
            self.assertTrue(LEGACY_TABLE_NAMES.issubset(tables))
            self.assertTrue(CONTROL_TABLE_NAMES.issubset(tables))
            self.assertIn("alembic_version", tables)
            self.assertTrue({
                "diagnostics_json",
                "last_websocket_at",
                "last_websocket_disconnected_at",
            }.issubset(device_columns))
            assert_schema_current(migration_engine)
            migration_engine.dispose()

    def test_schema_guard_rejects_unmigrated_database(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_unmigrated_") as migration_dir:
            database_path = Path(migration_dir) / "empty.db"
            migration_engine = create_engine(f"sqlite:///{database_path.as_posix()}")
            with self.assertRaisesRegex(RuntimeError, "alembic upgrade head"):
                assert_schema_current(migration_engine)
            migration_engine.dispose()

    def test_existing_head_revision_remains_valid_after_baseline_link(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_stamped_") as migration_dir:
            database_path = Path(migration_dir) / "stamped.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            migration_engine = create_engine(database_url)
            Base.metadata.create_all(migration_engine)
            original_url = AppConfig.DATABASE_URL
            try:
                AppConfig.DATABASE_URL = database_url
                migration_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
                alembic_command.stamp(migration_config, "20260722_01")
                alembic_command.upgrade(migration_config, "head")
            finally:
                AppConfig.DATABASE_URL = original_url
            assert_schema_current(migration_engine)
            migration_engine.dispose()

    def test_additive_alembic_migration_preserves_legacy_tables(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_migration_") as migration_dir:
            database_path = Path(migration_dir) / "legacy.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            migration_engine = create_engine(database_url)
            for table_name in LEGACY_TABLE_NAMES:
                Base.metadata.tables[table_name].create(migration_engine, checkfirst=True)
            before = set(inspect(migration_engine).get_table_names())
            original_url = AppConfig.DATABASE_URL
            try:
                AppConfig.DATABASE_URL = database_url
                migration_config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
                alembic_command.upgrade(migration_config, "head")
            finally:
                AppConfig.DATABASE_URL = original_url
            after = set(inspect(migration_engine).get_table_names())
            self.assertTrue(LEGACY_TABLE_NAMES.issubset(after))
            self.assertTrue(before.issubset(after))
            self.assertIn("staff_accounts", after)
            self.assertIn("device_nodes", after)
            self.assertIn("alembic_version", after)
            migration_engine.dispose()

    def test_schedule_import_preview_and_upsert(self):
        csv_content = (
            "full_name;date;period;start_time;end_time;subject;class_name;room\n"
            "Антон Иванов;2099-01-15;3;10:00;10:40;Тестов предмет;8А;T-1\n"
        ).encode("utf-8")
        preview = preview_schedule_import(self.db, "schedule.csv", csv_content)
        self.assertEqual(len(preview.rows), 1)
        self.assertEqual(preview.issues, [])
        job = execute_schedule_import(self.db, "schedule.csv", csv_content, "upsert", self.staff)
        self.assertEqual(job.status, "completed")
        anton = self._person("Антон Иванов")
        record = self.db.query(Timetable).filter(Timetable.person_id == anton.id, Timetable.date == datetime(2099, 1, 15).date(), Timetable.period == 3).one()
        self.assertEqual(record.room, "T-1")

    def test_retention_preview_cleanup_and_verified_backup(self):
        old_event = SystemEvent(event_type="old-test-event", timestamp=now_bg() - timedelta(days=400))
        self.db.add(old_event)
        self.db.commit()
        preview = retention_preview(self.db)
        self.assertGreaterEqual(preview["system_events"]["count"], 1)
        execute_retention_cleanup(self.db, self.staff)
        self.assertEqual(self.db.query(SystemEvent).filter(SystemEvent.event_type == "old-test-event").count(), 0)
        backup = create_sqlite_backup(self.db, self.staff)
        self.assertTrue(Path(backup.storage_path).is_file())
        self.assertTrue(verify_backup(self.db, backup))

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
