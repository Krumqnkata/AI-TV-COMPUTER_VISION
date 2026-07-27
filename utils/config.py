import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env.local")
load_dotenv(PROJECT_ROOT / ".env")


def _project_path(value: str, default: str) -> Path:
    """Return an absolute deployment path, rooted in the project by default."""
    candidate = Path(value or default).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _database_url(value: str) -> str:
    """Normalize supported database URLs and anchor relative SQLite files."""
    if value.startswith("postgres://"):
        return f"postgresql+psycopg://{value.removeprefix('postgres://')}"
    if value.startswith("postgresql://"):
        return f"postgresql+psycopg://{value.removeprefix('postgresql://')}"
    prefix = "sqlite:///"
    if not value.startswith(prefix):
        return value
    database_name = value.removeprefix(prefix)
    if database_name == ":memory:":
        return value
    database_path = Path(database_name).expanduser()
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return f"{prefix}{database_path.resolve().as_posix()}"


def _parse_camera_source(value):
    """ Ако стойността е число — USB камера (int). Иначе — IP камера URL (str). """
    try:
        return int(value)
    except (ValueError, TypeError):
        return value  # RTSP/HTTP URL

class Config:
    PROJECT_ROOT = PROJECT_ROOT
    DATABASE_URL = _database_url(
        os.getenv(
            "DATABASE_URL",
            f"sqlite:///{(PROJECT_ROOT / 'data' / 'school_ai.db').as_posix()}",
        )
    )
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT_SECONDS = int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))
    DB_POOL_RECYCLE_SECONDS = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800"))
    POSTGRES_BIN_DIR = _project_path(os.getenv("POSTGRES_BIN_DIR", ""), ".")
    POSTGRES_BIN_DIR_CONFIGURED = bool(os.getenv("POSTGRES_BIN_DIR", "").strip())
    BACKUP_COMMAND_TIMEOUT_SECONDS = int(os.getenv("BACKUP_COMMAND_TIMEOUT_SECONDS", "300"))
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))
    SERVER_URL = os.getenv("SERVER_URL", "http://localhost:5000").rstrip("/")
    DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "")
    DEVICE_ID = os.getenv("DEVICE_ID", "")
    DEVICE_KEY = os.getenv("DEVICE_KEY", "")
    DEVICE_NAME = os.getenv("DEVICE_NAME", "QR четец")
    DEVICE_TYPE = os.getenv("DEVICE_TYPE", "camera_node")
    DEVICE_ENROLLMENT_TOKEN = os.getenv("DEVICE_ENROLLMENT_TOKEN", "")
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")
    ADMIN_SECRET_IS_EPHEMERAL = False
    SETTINGS_MASTER_KEY = os.getenv("SETTINGS_MASTER_KEY", "")
    COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    ADMIN_SESSION_SECONDS = int(os.getenv("ADMIN_SESSION_SECONDS", str(8 * 60 * 60)))
    ADMIN_LOGIN_MAX_FAILURES = int(os.getenv("ADMIN_LOGIN_MAX_FAILURES", "5"))
    ADMIN_LOGIN_LOCK_MINUTES = int(os.getenv("ADMIN_LOGIN_LOCK_MINUTES", "15"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT = os.getenv("LOG_FORMAT", "console").strip().lower()

    # These paths are deployment boundaries. Their contents are managed from
    # the panel, but administrators cannot redirect them to arbitrary folders.
    BACKUP_DIR = _project_path(os.getenv("BACKUP_DIR", ""), "data/backups")
    IMPORT_DIR = _project_path(os.getenv("IMPORT_DIR", ""), "data/imports")
    MAX_IMPORT_BYTES = int(os.getenv("MAX_IMPORT_BYTES", str(10 * 1024 * 1024)))

    CAMERA_SOURCE = _parse_camera_source(os.getenv("CAMERA_SOURCE", "0"))
    CAMERA_ID = os.getenv("CAMERA_ID", "CAM-ENTRANCE-01")
    ZONE_ID = os.getenv("ZONE_ID", "MAIN_ENTRANCE")
    SCREEN_ID = os.getenv("SCREEN_ID", "SCR-ENTRANCE-01")
    HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL_ID = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash")
    AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", 0.95))
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

    LOGS_DIR = _project_path(os.getenv("LOGS_DIR", ""), "logs")
