"""Database backup helpers must keep PostgreSQL credentials out of commands."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.engine import make_url

from utils.config import Config
from web.services.backups import (
    _postgres_connection_arguments,
    _postgres_environment,
    _postgres_tool,
    backup_media_type,
)


class TestPostgreSQLBackupHelpers(unittest.TestCase):
    def test_password_is_kept_out_of_pg_command_arguments(self):
        url = make_url(
            "postgresql+psycopg://school_ai:private-password@localhost:5432/school_ai_dev"
        )

        arguments = _postgres_connection_arguments(url)

        self.assertIn("--dbname=school_ai_dev", arguments)
        self.assertIn("--host=localhost", arguments)
        self.assertIn("--port=5432", arguments)
        self.assertIn("--username=school_ai", arguments)
        self.assertNotIn("private-password", " ".join(arguments))

    def test_password_is_passed_only_through_pg_environment(self):
        url = make_url(
            "postgresql+psycopg://school_ai:private-password@localhost:5432/school_ai_dev"
        )

        with patch.dict(os.environ, {"PGPASSWORD": "stale-password"}):
            environment = _postgres_environment(url)

        self.assertEqual(environment["PGPASSWORD"], "private-password")
        self.assertEqual(environment["PGCLIENTENCODING"], "UTF8")

    def test_configured_postgres_bin_directory_is_respected(self):
        executable = "pg_dump.exe" if os.name == "nt" else "pg_dump"
        with tempfile.TemporaryDirectory() as temporary_directory:
            tool_path = Path(temporary_directory) / executable
            tool_path.touch()
            with (
                patch.object(Config, "POSTGRES_BIN_DIR", Path(temporary_directory)),
                patch.object(Config, "POSTGRES_BIN_DIR_CONFIGURED", True),
            ):
                self.assertEqual(_postgres_tool("pg_dump"), tool_path)

    def test_backup_media_type_matches_archive_format(self):
        self.assertEqual(
            backup_media_type(SimpleNamespace(database_type="sqlite")),
            "application/vnd.sqlite3",
        )
        self.assertEqual(
            backup_media_type(SimpleNamespace(database_type="postgresql")),
            "application/octet-stream",
        )


if __name__ == "__main__":
    unittest.main()
