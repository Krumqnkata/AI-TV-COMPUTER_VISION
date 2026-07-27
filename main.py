import uvicorn

from utils.config import Config
from utils.logger import configure_logging, log_system
from web.database import assert_schema_current


def main():
    configure_logging()
    assert_schema_current()
    log_system("School AI backend starting", event="application.starting")
    uvicorn.run(
        "web.server:app",
        host=Config.SERVER_HOST,
        port=Config.SERVER_PORT,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        schema_outdated = (
            isinstance(exc, RuntimeError)
            and "alembic upgrade head" in str(exc)
        )
        log_system(
            (
                "Database schema is not current; run "
                "`python -m alembic upgrade head` before startup"
                if schema_outdated
                else "School AI backend stopped after a critical error"
            ),
            "critical",
            event="application.failed",
            error_type=type(exc).__name__,
        )
        raise SystemExit(1) from exc
