import uvicorn
import os
from utils.logger import log_system
from utils.config import Config

def main():
    log_system("============================================================")
    log_system(" STARTING CENTRAL BACKEND SERVER (QR BADGE HUD)")
    log_system("============================================================")
    
    # Стартираме FastAPI сървъра директно
    # Портът е 5000 според конфигурацията
    uvicorn.run("web.server:app", host="0.0.0.0", port=5000, log_level="info")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_system(f"Critical error: {e}", "error")
