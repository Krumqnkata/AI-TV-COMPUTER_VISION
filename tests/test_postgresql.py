"""PostgreSQL schema compilation and optional live migration coverage."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, create_mock_engine, inspect, text
from sqlalchemy.engine import make_url

from engine import admin_models as _admin_models  # noqa: F401
from engine.db import Base


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestPostgreSQLSchemaCompilation(unittest.TestCase):
    def test_complete_metadata_compiles_for_postgresql(self):
        statements: list[str] = []
        engine = create_mock_engine(
            "postgresql+psycopg://",
            lambda sql, *_args, **_kwargs: statements.append(
                str(sql.compile(dialect=engine.dialect))
            ),
        )

        Base.metadata.create_all(engine)

        ddl = "\n".join(statements)
        self.assertEqual(len(Base.metadata.tables), 38)
        self.assertIn("CREATE TABLE persons", ddl)
        self.assertIn("CREATE TABLE staff_accounts", ddl)
        self.assertIn("CREATE TABLE device_nodes", ddl)


@unittest.skipUnless(
    os.getenv("POSTGRES_TEST_DATABASE_URL"),
    "POSTGRES_TEST_DATABASE_URL не е зададен; live PostgreSQL test е пропуснат",
)
class TestLivePostgreSQLMigrations(unittest.TestCase):
    def test_fresh_dedicated_test_database_reaches_alembic_head(self):
        test_url = make_url(os.environ["POSTGRES_TEST_DATABASE_URL"]).set(
            drivername="postgresql+psycopg"
        )
        if test_url.get_backend_name() != "postgresql":
            self.fail("POSTGRES_TEST_DATABASE_URL трябва да сочи към PostgreSQL")
        if not test_url.database or not test_url.database.lower().endswith("_test"):
            self.fail("За защита името на PostgreSQL test базата трябва да завършва на _test")

        test_engine = create_engine(test_url, isolation_level="AUTOCOMMIT")
        with test_engine.connect() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")

        try:
            environment = os.environ.copy()
            environment["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
            migration = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(
                migration.returncode,
                0,
                "Fresh PostgreSQL migration failed; inspect the Alembic output locally.",
            )

            table_names = set(inspect(test_engine).get_table_names())
            self.assertEqual(set(Base.metadata.tables), table_names - {"alembic_version"})
            with test_engine.connect() as connection:
                current_revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            expected_head = ScriptDirectory.from_config(
                AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
            ).get_current_head()
            self.assertEqual(current_revision, expected_head)
        finally:
            with test_engine.connect() as connection:
                connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
                connection.exec_driver_sql("CREATE SCHEMA public")
            test_engine.dispose()


if __name__ == "__main__":
    unittest.main()
