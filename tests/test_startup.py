"""Startup composition must not mutate a database during module import."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestStartupBoundaries(unittest.TestCase):
    @staticmethod
    def _environment(database_path: Path, log_dir: Path) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update({
            "DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "ADMIN_SECRET_KEY": "import-test-admin-secret-with-sufficient-length",
            "SETTINGS_MASTER_KEY": "import-test-settings-secret-with-sufficient-length",
            "LOGS_DIR": str(log_dir),
        })
        return environment

    def test_importing_server_does_not_create_schema(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_import_") as temp_dir:
            database_path = Path(temp_dir) / "import-only.db"
            environment = self._environment(database_path, Path(temp_dir) / "logs")
            result = subprocess.run(
                [sys.executable, "-c", "import web.server"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            if database_path.exists():
                connection = sqlite3.connect(database_path)
                tables = connection.execute(
                    "select name from sqlite_master where type='table'"
                ).fetchall()
                connection.close()
                self.assertEqual(tables, [])

    def test_main_fails_without_migrations_and_does_not_create_database(self):
        with tempfile.TemporaryDirectory(prefix="school_ai_main_") as temp_dir:
            database_path = Path(temp_dir) / "missing.db"
            result = subprocess.run(
                [sys.executable, "main.py"],
                cwd=PROJECT_ROOT,
                env=self._environment(database_path, Path(temp_dir) / "logs"),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("alembic upgrade head", result.stdout + result.stderr)
            self.assertFalse(database_path.exists())


if __name__ == "__main__":
    unittest.main()
