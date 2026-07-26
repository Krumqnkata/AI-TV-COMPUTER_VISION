"""Real-browser acceptance tests for the cross-platform kiosk and screen PWAs."""

from __future__ import annotations

import json
import gc
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_BROWSER_E2E = os.environ.get("RUN_BROWSER_E2E") == "1"


@unittest.skipUnless(RUN_BROWSER_E2E, "Set RUN_BROWSER_E2E=1 for Playwright acceptance tests")
class TestPwaBrowserAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.temp_dir = tempfile.TemporaryDirectory(
            prefix="school_ai_pwa_e2e_",
            ignore_cleanup_errors=True,
        )
        cls.database_path = Path(cls.temp_dir.name) / "pwa_e2e.db"
        cls.server_log_path = Path(cls.temp_dir.name) / "server.log"
        cls.environment = os.environ.copy()
        cls.environment.update({
            "DATABASE_URL": f"sqlite:///{cls.database_path.as_posix()}",
            "DEVICE_API_KEY": "e2e-legacy-key-with-sufficient-entropy",
            "ADMIN_SECRET_KEY": "e2e-admin-session-secret-with-sufficient-length",
            "SETTINGS_MASTER_KEY": "e2e-settings-master-key-with-sufficient-length",
            "COOKIE_SECURE": "false",
            "BACKUP_DIR": str(Path(cls.temp_dir.name) / "backups"),
            "PYTHONUNBUFFERED": "1",
        })

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT,
            env=cls.environment,
            check=True,
            capture_output=True,
            text=True,
        )
        seeded = subprocess.run(
            [sys.executable, "tests/_pwa_e2e_seed.py"],
            cwd=PROJECT_ROOT,
            env=cls.environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if seeded.returncode:
            raise RuntimeError(f"E2E fixture seed failed:\n{seeded.stderr}")
        cls.tokens = json.loads(seeded.stdout.strip().splitlines()[-1])

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server_log = cls.server_log_path.open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "web.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--log-level",
                "warning",
            ],
            cwd=PROJECT_ROOT,
            env=cls.environment,
            stdout=cls.server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls._wait_for_server()

        cls.playwright = sync_playwright().start()
        cls.browser_name = os.environ.get("PLAYWRIGHT_BROWSER", "chromium")
        if cls.browser_name == "msedge":
            cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)
        elif cls.browser_name == "chromium":
            cls.browser = cls.playwright.chromium.launch(
                headless=True,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                ],
            )
        else:
            cls.browser = getattr(cls.playwright, cls.browser_name).launch(headless=True)

    @classmethod
    def _wait_for_server(cls):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{cls.base_url}/", timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        cls.server_log.flush()
        log = cls.server_log_path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"E2E server did not start:\n{log}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "playwright"):
            cls.playwright.stop()
        if hasattr(cls, "server") and cls.server.poll() is None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.server.kill()
                cls.server.wait(timeout=5)
        if hasattr(cls, "server_log"):
            cls.server_log.close()
        if hasattr(cls, "temp_dir"):
            gc.collect()
            time.sleep(0.25)
            cls.temp_dir.cleanup()

    def _pair(self, context, profile: str, token: str):
        from playwright.sync_api import expect

        page = context.new_page()
        page.goto(f"{self.base_url}/pair?profile={profile}", wait_until="domcontentloaded")
        page.locator('input[name="enrollment_token"]').fill(token)
        page.locator('input[name="name"]').fill(
            "Playwright киоск" if profile == "kiosk" else "Playwright екран"
        )
        page.locator("[data-pair-submit]").click()
        expect(page).to_have_url(re.compile(rf"/{profile}$"), timeout=12_000)
        return page

    def _wait_for_acknowledgment(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                with sqlite3.connect(self.database_path) as connection:
                    row = connection.execute(
                        "SELECT status FROM delivery_receipts ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                if row and row[0] == "acknowledged":
                    return
            except sqlite3.OperationalError:
                pass
            time.sleep(0.2)
        self.fail("The browser did not persist and deliver its acknowledgment")

    def _start_connection_trace(self, page):
        page.evaluate(
            """
            () => {
                const status = document.querySelector("[data-connection-status]");
                window.__schoolAiConnectionTrace = [];
                const record = () => {
                    window.__schoolAiConnectionTrace.push({
                        at: Math.round(performance.now()),
                        online: navigator.onLine,
                        state: status?.dataset.state || null,
                        label: status?.textContent?.trim() || null,
                    });
                    window.__schoolAiConnectionTrace =
                        window.__schoolAiConnectionTrace.slice(-30);
                };
                const observer = new MutationObserver(record);
                observer.observe(status, {
                    attributes: true,
                    childList: true,
                    subtree: true,
                });
                window.__schoolAiConnectionObserver = observer;
                record();
            }
            """
        )

    def _expect_connected(self, page, *, cycle: int):
        from playwright.sync_api import expect

        status = page.locator("[data-connection-status]")
        try:
            expect(status).to_have_attribute("data-state", "online", timeout=12_000)
            expect(status).to_contain_text("Свързан")
        except AssertionError as error:
            snapshot = page.evaluate(
                """
                () => {
                    const status = document.querySelector("[data-connection-status]");
                    const banner = document.querySelector("[data-offline-banner]");
                    return {
                        navigator_online: navigator.onLine,
                        state: status?.dataset.state || null,
                        label: status?.textContent?.trim() || null,
                        offline_banner_hidden: banner?.hidden ?? null,
                        trace: window.__schoolAiConnectionTrace || [],
                    };
                }
                """
            )
            self.server_log.flush()
            server_tail = self.server_log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )[-4_000:]
            self.fail(
                f"WebSocket did not recover after offline cycle {cycle}:\n"
                f"{error}\n"
                f"Browser snapshot:\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n"
                f"Server log tail:\n{server_tail}"
            )

    def test_pwa_acceptance(self):
        from playwright.sync_api import expect

        mode = os.environ.get("PWA_E2E_MODE", "full")
        screen_context = self.browser.new_context(locale="bg-BG")
        screen_page = self._pair(
            screen_context,
            "screen",
            self.tokens["screen_token"],
        )
        expect(screen_page.locator("[data-slide-title]")).to_have_text(
            "Playwright публична новина",
            timeout=10_000,
        )
        expect(screen_page.locator("[data-personal-overlay]")).to_be_hidden()

        screen_cookies = screen_context.cookies(self.base_url)
        secret_cookie = next(
            item for item in screen_cookies
            if item["name"] == "school_ai_screen_device_key"
        )
        self.assertTrue(secret_cookie["httpOnly"])

        if mode == "smoke":
            manifest = screen_page.evaluate(
                "() => document.querySelector('link[rel=\"manifest\"]').getAttribute('href')"
            )
            self.assertEqual(manifest, "/manifest-screen.webmanifest")
            self.assertIn("Информационен екран", screen_page.title())
            screen_context.close()
            return

        kiosk_context = self.browser.new_context(locale="bg-BG")
        kiosk_page = self._pair(
            kiosk_context,
            "kiosk",
            self.tokens["kiosk_token"],
        )
        self._start_connection_trace(kiosk_page)
        kiosk_cookies = kiosk_context.cookies(self.base_url)
        kiosk_secret = next(
            item for item in kiosk_cookies
            if item["name"] == "school_ai_kiosk_device_key"
        )
        self.assertTrue(kiosk_secret["httpOnly"])

        kiosk_page.locator(".manual-scan summary").click()
        kiosk_page.locator('input[name="badge_token"]').fill("SCH-8F3A92C1")
        kiosk_page.locator("[data-manual-scan-form] button").click()
        expect(kiosk_page.locator("[data-session-view]")).to_be_visible(timeout=10_000)
        expect(kiosk_page.locator("[data-person-greeting]")).to_contain_text("Антон")
        self._wait_for_acknowledgment()

        screen_page.wait_for_timeout(900)
        expect(screen_page.locator("[data-personal-overlay]")).to_be_hidden()

        local_values = kiosk_page.evaluate("() => Object.values(localStorage)")
        self.assertFalse(any("Антон" in value for value in local_values))
        self.assertFalse(any("учителската" in value for value in local_values))

        expect(kiosk_page.locator("[data-idle-view]")).to_be_visible(timeout=20_000)
        expect(kiosk_page.locator("[data-session-view]")).to_be_hidden()
        self.assertEqual(kiosk_page.locator("[data-person-greeting]").text_content(), "")

        for cycle in range(1, 3):
            kiosk_context.set_offline(True)
            kiosk_page.wait_for_function(
                "() => navigator.onLine === false",
                timeout=5_000,
            )
            expect(kiosk_page.locator("[data-offline-banner]")).to_be_visible(
                timeout=5_000,
            )
            expect(kiosk_page.locator("[data-connection-status]")).to_have_attribute(
                "data-state",
                "offline",
                timeout=5_000,
            )
            # Keep the browser offline beyond the first retry. Recovery must not
            # depend on a single online event after that timer has fired.
            kiosk_page.wait_for_timeout(1_500)

            kiosk_context.set_offline(False)
            kiosk_page.wait_for_function(
                "() => navigator.onLine === true",
                timeout=5_000,
            )
            expect(kiosk_page.locator("[data-offline-banner]")).to_be_hidden(
                timeout=5_000,
            )
            self._expect_connected(kiosk_page, cycle=cycle)

        controlled = kiosk_page.evaluate(
            "() => 'serviceWorker' in navigator"
            " ? navigator.serviceWorker.ready.then(() => true) : false"
        )
        self.assertTrue(controlled)
        kiosk_context.close()
        screen_context.close()


if __name__ == "__main__":
    unittest.main()
