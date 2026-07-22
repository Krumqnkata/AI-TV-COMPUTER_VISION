from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from engine.db import Base
# Import the additive control-centre models before ``create_all`` so their
# tables are registered in the shared metadata.
from engine import admin_models as _admin_models  # noqa: F401
from utils.config import Config


DATABASE_URL = Config.DATABASE_URL
_sqlite = DATABASE_URL.startswith("sqlite")
db_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _sqlite else {},
    pool_pre_ping=not _sqlite,
)


if _sqlite:
    @event.listens_for(db_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


def init_schema() -> None:
    Base.metadata.create_all(bind=db_engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
