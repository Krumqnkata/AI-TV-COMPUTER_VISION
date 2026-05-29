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


    # Логове
    LOGS_DIR = "logs"
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)