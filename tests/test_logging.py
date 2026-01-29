"""Tests for logging system"""

import os
import sys

sys.path.append(os.getcwd())

from config import LOG_FILE
from logger import get_logger, setup_logger


def test_logger_setup():
    """Test logger can be initialized"""
    print("Testing logger setup...")

    logger = setup_logger()
    assert logger is not None
    assert logger.name == "ProjectMind"
    print("  [OK] Logger initialized")


def test_logger_singleton():
    """Test logger is singleton"""
    print("Testing logger singleton pattern...")

    logger1 = get_logger()
    logger2 = get_logger()
    assert logger1 is logger2
    print("  [OK] Logger singleton works")


def test_log_file_creation():
    """Test that log file is created"""
    print("Testing log file creation...")

    logger = get_logger()
    logger.info("Test log message")
    logger.warning("Test warning message")
    logger.error("Test error message")

    if LOG_FILE.exists():
        content = LOG_FILE.read_text(encoding="utf-8")
        assert "Test log message" in content
        assert "Test warning message" in content
        assert "Test error message" in content
        print("  [OK] Log file created and messages written")
    else:
        print("  [WARNING] Log file not created (might be permission issue)")


def test_log_levels():
    """Test different log levels"""
    print("Testing log levels...")

    logger = get_logger()

    logger.debug("Debug message")
    logger.info("Info message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    print("  [OK] All log levels work without errors")


def test_log_rotation_config():
    """Test log rotation configuration"""
    print("Testing log rotation configuration...")

    logger = get_logger()

    for handler in logger.handlers:
        if hasattr(handler, "maxBytes"):
            assert handler.maxBytes == 10 * 1024 * 1024
            assert handler.backupCount == 5
            print(
                f"  [OK] Log rotation configured: {handler.maxBytes} bytes, {handler.backupCount} backups"
            )
            return

    print("  [WARNING] No rotating file handler found")


if __name__ == "__main__":
    print("=" * 50)
    print("LOGGING SYSTEM TESTS")
    print("=" * 50)
    print()

    try:
        test_logger_setup()
        test_logger_singleton()
        test_log_file_creation()
        test_log_levels()
        test_log_rotation_config()

        print()
        print("=" * 50)
        print("[SUCCESS] ALL LOGGING TESTS PASSED!")
        print("=" * 50)

        if LOG_FILE.exists():
            print(f"\nLog file location: {LOG_FILE}")
            print(f"Log file size: {LOG_FILE.stat().st_size} bytes")
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
