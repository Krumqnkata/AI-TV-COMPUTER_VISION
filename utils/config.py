import os
from dotenv import load_dotenv

load_dotenv()

def _parse_camera_source(value):
    """ Ако стойността е число — USB камера (int). Иначе — IP камера URL (str). """
    try:
        return int(value)
    except (ValueError, TypeError):
        return value  # RTSP/HTTP URL

class Config:
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 30))
    CAMERA_SOURCE = _parse_camera_source(os.getenv("CAMERA_SOURCE", "0"))
    FACES_DATA_PATH = os.getenv("FACES_DATA_PATH", "data/faces")
    JOKES_FILE_PATH = os.getenv("JOKES_FILE_PATH", "jokes.json")
    NAMES_MAPPING_PATH = os.getenv("NAMES_MAPPING_PATH", "data/names_mapping.json")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL_ID = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash")

    # Нови настройки от плана за рефакторинг
    TARGET_WIDTH = int(os.getenv("TARGET_WIDTH", 1920))
    TARGET_HEIGHT = int(os.getenv("TARGET_HEIGHT", 1080))
    PROCESS_EVERY_N_FRAMES = int(os.getenv("PROCESS_EVERY_N_FRAMES", 10))
    FONT_PATH = os.getenv("FONT_PATH", "ARIAL.TTF")
    
    # Настройки за прецизност на разпознаването
    FACE_RECOGNITION_TOLERANCE = float(os.getenv("FACE_RECOGNITION_TOLERANCE", 0.57))
    FACE_ENCODING_MODEL = os.getenv("FACE_ENCODING_MODEL", "large")
    
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