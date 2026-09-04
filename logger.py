"""Centralized logging configuration for ProjectMind"""

import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config

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
            key: value for key, value in record.__dict__.items() if key not in self.RESERVED_ATTRS
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

    try:
        _attach_file_handler(logger)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not setup file logging: {e}\n")

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(_formatter())
    logger.addHandler(stderr_handler)

    _logger = logger
    return logger


def _formatter() -> StructuredFormatter:
    return StructuredFormatter(
        fmt="[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _attach_file_handler(logger: logging.Logger) -> None:
    config.AI_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_formatter())
    logger.addHandler(file_handler)


def rebind_log_file() -> None:
    """
    Point the file handler at the *current* project's `.ai/projectmind.log`.

    The logger is a process-wide singleton created at import time, so its file
    handler stayed bound to whichever project was configured first — usually the
    MCP server's own directory. After `set_project_root`, everything the server
    did for the real project was written to a log file in a different project,
    which is why a hung session left no trace where anyone would look for it.
    """
    if _logger is None:
        return
    target = Path(config.LOG_FILE)
    for handler in list(_logger.handlers):
        if not isinstance(handler, RotatingFileHandler):
            continue
        try:
            if Path(handler.baseFilename) == target.resolve():
                return
        except OSError:
            pass
        _logger.removeHandler(handler)
        handler.close()
    try:
        _attach_file_handler(_logger)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not rebind file logging: {e}\n")


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
