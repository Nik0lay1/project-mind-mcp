"""Centralized logging configuration for ProjectMind"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from config import AI_DIR, LOG_BACKUP_COUNT, LOG_FILE, LOG_MAX_BYTES

_logger: logging.Logger | None = None


def setup_logger(name: str = "ProjectMind") -> logging.Logger:
    """
    Sets up a rotating file logger with both file and stderr output.

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

    formatter = logging.Formatter(
        fmt='[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    try:
        AI_DIR.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding='utf-8'
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
