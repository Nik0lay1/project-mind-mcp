"""Tests for path validation security feature"""

import os
import sys

sys.path.append(os.getcwd())

from config import PROJECT_ROOT, validate_path


def test_valid_paths():
    """Test that valid paths within project are accepted"""
    print("Testing valid paths...")

    valid = validate_path(".")
    assert valid == PROJECT_ROOT
    print("  [OK] Current directory validated")

    valid = validate_path("config.py")
    assert valid == PROJECT_ROOT / "config.py"
    print("  [OK] File in root validated")

    valid = validate_path(".ai")
    assert valid == PROJECT_ROOT / ".ai"
    print("  [OK] Hidden directory validated")

    print("[PASS] Valid paths test passed\n")


def test_invalid_paths():
    """Test that paths outside project are rejected"""
    print("Testing invalid paths...")

    test_cases = [
        ("../../etc/passwd", "Parent directory traversal"),
        ("/etc/passwd", "Absolute path outside project"),
        ("", "Empty path"),
    ]

    if os.name == "nt":
        test_cases.append(("C:\\Windows\\System32", "Windows system path"))

    for path, description in test_cases:
        try:
            validate_path(path)
            print(f"  [FAIL] {description} was not rejected!")
            sys.exit(1)
        except ValueError:
            print(f"  [OK] Blocked: {description}")

    print("[PASS] Invalid paths test passed\n")


def test_edge_cases():
    """Test edge cases"""
    print("Testing edge cases...")

    try:
        validate_path(123)
        print("  [FAIL] Non-string path was not rejected!")
        sys.exit(1)
    except ValueError:
        print("  [OK] Non-string path rejected")

    try:
        validate_path(None)
        print("  [FAIL] None path was not rejected!")
        sys.exit(1)
    except (ValueError, TypeError):
        print("  [OK] None path rejected")

    print("[PASS] Edge cases test passed\n")


if __name__ == "__main__":
    print("=" * 50)
    print("PATH VALIDATION SECURITY TESTS")
    print("=" * 50)
    print(f"Project root: {PROJECT_ROOT}\n")

    try:
        test_valid_paths()
        test_invalid_paths()
        test_edge_cases()
        print("=" * 50)
        print("[SUCCESS] ALL SECURITY TESTS PASSED!")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
