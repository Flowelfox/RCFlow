import logging
import logging.config
import sys
from typing import Any

from src.config import Settings
from src.paths import get_data_dir

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d,%H:%M:%S"


class MultiLineExceptionFormatter(logging.Formatter):
    """Formatter that gives each traceback line its own log prefix."""

    def format(self, record: logging.LogRecord) -> str:
        saved_exc_info = record.exc_info
        saved_exc_text = record.exc_text
        record.exc_info = None
        record.exc_text = None

        main_message = super().format(record)

        record.exc_info = saved_exc_info
        record.exc_text = saved_exc_text

        if saved_exc_info:
            prefix_end = main_message.find(record.getMessage())
            prefix = main_message[:prefix_end] if prefix_end > 0 else ""

            exc_text = super().formatException(saved_exc_info)
            exc_lines = exc_text.split("\n")

            lines = [main_message]
            for exc_line in exc_lines:
                if exc_line.strip():
                    lines.append(f"{prefix}{exc_line}")

            return "\n".join(lines)

        return main_message


def setup_logging(settings: Settings) -> None:
    level = settings.LOG_LEVEL.upper()

    logs_folder = get_data_dir() / "logs"
    file_logging = True
    try:
        logs_folder.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        file_logging = False

    handlers: dict[str, Any] = {
        "console": {
            "level": "DEBUG",
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    }
    if file_logging:
        handlers["app_file"] = {
            "level": "DEBUG",
            "formatter": "default",
            "class": "logging.handlers.RotatingFileHandler",
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf8",
            "filename": str(logs_folder / "app.log"),
        }
        handlers["err_file"] = {
            "level": "ERROR",
            "formatter": "default",
            "class": "logging.handlers.RotatingFileHandler",
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf8",
            "filename": str(logs_folder / "errors.log"),
        }

    active = list(handlers.keys())

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "src.logs.MultiLineExceptionFormatter",
                "format": _LOG_FORMAT,
                "datefmt": _DATE_FORMAT,
            },
        },
        "handlers": handlers,
        "loggers": {
            "": {
                "handlers": active,
                "level": "INFO",
                "propagate": True,
            },
            "src": {
                "handlers": active,
                "level": level,
                "propagate": False,
            },
            "sqlalchemy": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.engine": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.pool": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.dialects": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.orm": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "alembic": {
                "handlers": active,
                "level": "INFO",
                "propagate": False,
            },
            "httpx": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn": {
                "handlers": active,
                "level": "INFO",
                "propagate": False,
            },
            "anthropic": {
                "handlers": active,
                "level": "WARNING",
                "propagate": False,
            },
        },
    }
    try:
        logging.config.dictConfig(config)
    except OSError as exc:
        # File handlers could not be opened (e.g. a race-condition permission
        # issue that slipped past the mkdir check).  Degrade gracefully to
        # console-only so the application can still start.
        logging.basicConfig(
            level=level,
            format=_LOG_FORMAT,
            datefmt=_DATE_FORMAT,
            stream=sys.stderr,
        )
        logging.warning(
            "Could not configure file logging (%s: %s); falling back to console only",
            type(exc).__name__,
            exc,
        )
