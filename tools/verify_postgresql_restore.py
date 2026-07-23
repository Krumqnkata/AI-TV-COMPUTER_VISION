"""Restore a PostgreSQL backup into a disposable test database and validate it."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url

from engine import admin_models as _admin_models  # noqa: F401
from engine.db import Base
from utils.config import Config
from web.services.backups import (
    _postgres_connection_arguments,
    _postgres_tool,
    _run_postgres_tool,
)

def _normalize_postgresql_url(raw_url: str) -> URL:
    url = make_url(raw_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


def validate_restore_target(raw_url: str, runtime_url: str) -> URL:
    """Allow destructive restore checks only in a separate PostgreSQL *_test DB."""
    test_url = _normalize_postgresql_url(raw_url)
    production_url = _normalize_postgresql_url(runtime_url)
    if test_url.get_backend_name() != "postgresql":
        raise RuntimeError("POSTGRES_TEST_DATABASE_URL трябва да сочи към PostgreSQL")
    if production_url.get_backend_name() != "postgresql":
        raise RuntimeError("Runtime DATABASE_URL трябва да сочи към PostgreSQL")
    if not test_url.database or not test_url.database.lower().endswith("_test"):
        raise RuntimeError("Restore test базата трябва да има име, завършващо на _test")
    same_server = (
        (test_url.host or "localhost") == (production_url.host or "localhost")
        and (test_url.port or 5432) == (production_url.port or 5432)
    )
    if same_server and test_url.database == production_url.database:
        raise RuntimeError("Restore test базата не може да бъде runtime базата")
    return test_url


def _reset_public_schema(engine) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")


def verify_restore(archive: Path, test_url: URL) -> tuple[int, str]:
    """Restore, validate the schema/revision, then leave the test DB empty."""
    if not archive.is_file():
        raise FileNotFoundError(f"Backup архивът не е намерен: {archive}")
    test_engine = create_engine(test_url, isolation_level="AUTOCOMMIT")
    try:
        _reset_public_schema(test_engine)
        command = [
            str(_postgres_tool("pg_restore")),
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            *_postgres_connection_arguments(test_url),
            str(archive.resolve()),
        ]
        _run_postgres_tool(command, test_url)

        actual_tables = set(inspect(test_engine).get_table_names())
        expected_tables = set(Base.metadata.tables)
        missing_tables = sorted(expected_tables - actual_tables)
        if missing_tables:
            raise RuntimeError(
                f"Restore архивът няма {len(missing_tables)} очаквани таблици: "
                f"{', '.join(missing_tables[:10])}"
            )
        with test_engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        expected_head = ScriptDirectory.from_config(
            AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
        ).get_current_head()
        if revision != expected_head:
            raise RuntimeError(
                f"Restore архивът е на Alembic revision {revision}, очаква се {expected_head}"
            )
        return len(actual_tables), revision
    finally:
        try:
            _reset_public_schema(test_engine)
        finally:
            test_engine.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Проверява PostgreSQL backup чрез destructive restore само в *_test база."
    )
    parser.add_argument("archive", type=Path, help="Път до custom-format .dump архив")
    parser.add_argument(
        "--confirm-destroy-test-database",
        action="store_true",
        help="Потвърждава изтриването на public schema в POSTGRES_TEST_DATABASE_URL",
    )
    args = parser.parse_args()
    if not args.confirm_destroy_test_database:
        parser.error("Изисква се --confirm-destroy-test-database")

    raw_test_url = os.getenv("POSTGRES_TEST_DATABASE_URL", "").strip()
    if not raw_test_url:
        raise RuntimeError("POSTGRES_TEST_DATABASE_URL не е зададен")
    test_url = validate_restore_target(raw_test_url, Config.DATABASE_URL)
    table_count, revision = verify_restore(args.archive.resolve(), test_url)
    print(
        f"Restore проверката е успешна: {table_count} таблици, "
        f"Alembic revision {revision}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
