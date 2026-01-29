"""Centralized logging configuration for ProjectMind"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import AI_DIR, LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES

_logger: logging.Logger | None = None


class StructuredFormatter(logging.Formatter):
    """Formatter that includes extra fields as JSON."""

    RESERVED_ATTRS = {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "exc_info",
        "exc_text",
        "thread",
        "threadName",
        "taskName",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self.RESERVED_ATTRS
        }

        if extra:
            try:
                extra_str = json.dumps(extra, default=str, ensure_ascii=False)
                return f"{base} | {extra_str}"
            except (TypeError, ValueError):
                return base

        return base


def setup_logger(name: str = "ProjectMind") -> logging.Logger:
    """
    Sets up a rotating file logger with both file and stderr output.
    Supports structured logging with extra fields.

    Args:
        name: Logger name

    Returns:
        Configured logger instance
    """
    global _logger

    if _logger is not None:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    logger.handlers.clear()

    formatter = StructuredFormatter(
        fmt="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        AI_DIR.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not setup file logging: {e}\n")

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """
    Gets the configured logger instance.
    Creates it if it doesn't exist.

    Returns:
        Logger instance
    """
    if _logger is None:
        return setup_logger()
    return _logger
