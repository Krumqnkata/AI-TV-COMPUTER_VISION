import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 30))
    CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", 0))
    FACES_DATA_PATH = os.getenv("FACES_DATA_PATH", "data/faces")
    JOKES_FILE_PATH = os.getenv("JOKES_FILE_PATH", "jokes.json")


    # Логове
    LOGS_DIR = "logs"
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)