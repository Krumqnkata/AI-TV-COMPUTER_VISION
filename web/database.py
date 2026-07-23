from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from engine import admin_models as _admin_models  # noqa: F401
from engine.db import Base
from utils.config import Config


DATABASE_URL = Config.DATABASE_URL
_database_backend = make_url(DATABASE_URL).get_backend_name()
_sqlite = _database_backend == "sqlite"
_postgresql = _database_backend == "postgresql"

_engine_options = {
    "connect_args": {"check_same_thread": False} if _sqlite else {},
    "pool_pre_ping": not _sqlite,
}
if _postgresql:
    _engine_options.update({
        "pool_size": Config.DB_POOL_SIZE,
        "max_overflow": Config.DB_MAX_OVERFLOW,
        "pool_timeout": Config.DB_POOL_TIMEOUT_SECONDS,
        "pool_recycle": Config.DB_POOL_RECYCLE_SECONDS,
    })

db_engine = create_engine(DATABASE_URL, **_engine_options)


if _sqlite:
    @event.listens_for(db_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


def schema_revisions(engine=db_engine) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the database revisions and the revisions expected by the code."""
    alembic_config = AlembicConfig(str(Config.PROJECT_ROOT / "alembic.ini"))
    expected = tuple(ScriptDirectory.from_config(alembic_config).get_heads())
    database_name = engine.url.database
    if engine.url.get_backend_name() == "sqlite" and database_name and database_name != ":memory:":
        database_path = Path(database_name).expanduser()
        if not database_path.is_absolute():
            database_path = Config.PROJECT_ROOT / database_path
        if not database_path.exists():
            return (), expected
    with engine.connect() as connection:
        current = tuple(MigrationContext.configure(connection).get_current_heads())
    return current, expected


def assert_schema_current(engine=db_engine) -> None:
    """Fail fast when the configured database has not been migrated."""
    current, expected = schema_revisions(engine)
    if set(current) == set(expected):
        return
    current_label = ", ".join(current) if current else "няма migration revision"
    expected_label = ", ".join(expected) if expected else "няма дефиниран head"
    raise RuntimeError(
        "Базата данни не е мигрирана до текущата версия "
        f"(current: {current_label}; expected: {expected_label}). "
        "Изпълнете: python -m alembic upgrade head"
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
