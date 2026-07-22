import logging
from datetime import datetime

from utils.config import Config


_logger: logging.Logger | None = None


def _system_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    Config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("school_ai")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(Config.LOGS_DIR / "system.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
    _logger = logger
    return logger


def log_system(message, level="info"):
    logger = _system_logger()
    if level == "error":
        logger.error(message)
    else:
        logger.info(message)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
