import uvicorn

from utils.config import Config
from utils.logger import log_system
from web.database import assert_schema_current


def main():
    assert_schema_current()
    log_system("============================================================")
    log_system(" STARTING CENTRAL BACKEND SERVER (QR BADGE HUD)")
    log_system("============================================================")
    uvicorn.run(
        "web.server:app",
        host=Config.SERVER_HOST,
        port=Config.SERVER_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log_system(f"Critical error: {exc}", "error")
        raise SystemExit(1) from exc
