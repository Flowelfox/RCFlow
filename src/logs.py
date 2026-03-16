import logging
import logging.config
from pathlib import Path
from typing import Any

from src.config import Settings
from src.paths import get_install_dir

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
            if prefix_end > 0:
                prefix = main_message[:prefix_end]
            else:
                prefix = ""

            exc_text = super().formatException(saved_exc_info)
            exc_lines = exc_text.split("\n")

            lines = [main_message]
            for exc_line in exc_lines:
                if exc_line.strip():
                    lines.append(f"{prefix}{exc_line}")

            return "\n".join(lines)

        return main_message


class HandshakeFilter(logging.Filter):
    """Suppress noisy WebSocket handshake failure errors."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.ERROR and "opening handshake failed" in record.msg:
            return False
        return True


def setup_logging(settings: Settings) -> None:
    level = settings.LOG_LEVEL.upper()

    logs_folder = get_install_dir() / "logs"
    logs_folder.mkdir(parents=True, exist_ok=True)

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
        "handlers": {
            "console": {
                "level": "DEBUG",
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "app_file": {
                "level": "DEBUG",
                "formatter": "default",
                "class": "logging.handlers.RotatingFileHandler",
                "maxBytes": 10485760,
                "backupCount": 5,
                "encoding": "utf8",
                "filename": str(logs_folder / "app.log"),
            },
            "err_file": {
                "level": "ERROR",
                "formatter": "default",
                "class": "logging.handlers.RotatingFileHandler",
                "maxBytes": 10485760,
                "backupCount": 5,
                "encoding": "utf8",
                "filename": str(logs_folder / "errors.log"),
            },
        },
        "loggers": {
            "": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "INFO",
                "propagate": True,
            },
            "src": {
                "handlers": ["console", "app_file", "err_file"],
                "level": level,
                "propagate": False,
            },
            "sqlalchemy": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.engine": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.pool": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.dialects": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.orm": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "alembic": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "INFO",
                "propagate": False,
            },
            "websockets": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
                "filters": ["handshake_filter"],
            },
            "httpx": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "uvicorn": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "INFO",
                "propagate": False,
            },
            "anthropic": {
                "handlers": ["console", "app_file", "err_file"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "filters": {
            "handshake_filter": {"()": "src.logs.HandshakeFilter"},
        },
    }
    logging.config.dictConfig(config)
