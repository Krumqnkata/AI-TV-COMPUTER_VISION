import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env.local")
load_dotenv(PROJECT_ROOT / ".env")

def _parse_camera_source(value):
    """ Ако стойността е число — USB камера (int). Иначе — IP камера URL (str). """
    try:
        return int(value)
    except (ValueError, TypeError):
        return value  # RTSP/HTTP URL

class Config:
    PROJECT_ROOT = PROJECT_ROOT
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(PROJECT_ROOT / 'data' / 'school_ai.db').as_posix()}",
    )
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))
    SERVER_URL = os.getenv("SERVER_URL", "http://localhost:5000").rstrip("/")
    DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "")
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "")
    COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 30))
    CAMERA_SOURCE = _parse_camera_source(os.getenv("CAMERA_SOURCE", "0"))
    CAMERA_ID = os.getenv("CAMERA_ID", "CAM-ENTRANCE-01")
    ZONE_ID = os.getenv("ZONE_ID", "MAIN_ENTRANCE")
    SCREEN_ID = os.getenv("SCREEN_ID", "SCR-ENTRANCE-01")
    HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    JOKES_FILE_PATH = os.getenv("JOKES_FILE_PATH", "jokes.json")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL_ID = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash")

    # Нови настройки от плана за рефакторинг
    TARGET_WIDTH = int(os.getenv("TARGET_WIDTH", 1920))
    TARGET_HEIGHT = int(os.getenv("TARGET_HEIGHT", 1080))
    PROCESS_EVERY_N_FRAMES = int(os.getenv("PROCESS_EVERY_N_FRAMES", 10))
    FONT_PATH = os.getenv("FONT_PATH", "ARIAL.TTF")
    
    MAX_HISTORY = int(os.getenv("MAX_HISTORY", 40))
    
    AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", 0.95))
    AI_RATE_LIMIT_PER_MINUTE = int(os.getenv("AI_RATE_LIMIT_PER_MINUTE", 4))
    AI_CACHE_REUSE_PROB = float(os.getenv("AI_CACHE_REUSE_PROB", 0.60))
    
    PIPER_MODEL_PATH = os.getenv("PIPER_MODEL_PATH", os.path.join("engine", "piper", "bg_BG-dimitar-medium.onnx"))

    OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "false").lower() == "true"
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

    # Логове
    LOGS_DIR = "logs"
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)
