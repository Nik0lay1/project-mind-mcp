"""Tests for Unicode handling improvements"""
import sys
import os
from pathlib import Path
import tempfile

sys.path.append(os.getcwd())

from config import safe_read_text


def test_utf8_file():
    """Test reading UTF-8 file"""
    print("Testing UTF-8 file...")
    
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.txt') as f:
        f.write("Hello Unicode: \u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430 \u043c\u043e\u0432\u0430 \U0001f600")
        temp_path = Path(f.name)
    
    try:
        content = safe_read_text(temp_path)
        assert "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430 \u043c\u043e\u0432\u0430" in content
        assert "\U0001f600" in content
        print("  [OK] UTF-8 file read successfully")
    finally:
        temp_path.unlink()
    

def test_latin1_file():
    """Test reading Latin-1 file"""
    print("Testing Latin-1 file...")
    
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt') as f:
        f.write(b"Caf\xe9 \xe0 la mode")
        temp_path = Path(f.name)
    
    try:
        content = safe_read_text(temp_path)
        assert "Caf" in content
        print("  [OK] Latin-1 file read successfully")
    finally:
        temp_path.unlink()


def test_windows1252_file():
    """Test reading Windows-1252 file"""
    print("Testing Windows-1252 file...")
    
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt') as f:
        f.write(b'Smart quotes: \x93text\x94')
        temp_path = Path(f.name)
    
    try:
        content = safe_read_text(temp_path)
        assert "Smart quotes" in content
        print("  [OK] Windows-1252 file read successfully")
    finally:
        temp_path.unlink()


def test_utf8_with_bom():
    """Test reading UTF-8 with BOM file"""
    print("Testing UTF-8 with BOM...")
    
    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt') as f:
        f.write(b'\xef\xbb\xbfUTF-8 with BOM')
        temp_path = Path(f.name)
    
    try:
        content = safe_read_text(temp_path)
        assert content.startswith("UTF-8 with BOM") or content.startswith("\ufeffUTF-8 with BOM")
        print("  [OK] UTF-8 with BOM read successfully")
    finally:
        temp_path.unlink()


def test_nonexistent_file():
    """Test that nonexistent files raise appropriate error"""
    print("Testing nonexistent file...")
    
    temp_path = Path("nonexistent_test_file_12345.txt")
    
    try:
        content = safe_read_text(temp_path)
        print("  [FAIL] Nonexistent file should have raised error")
        sys.exit(1)
    except (IOError, FileNotFoundError):
        print("  [OK] Nonexistent file properly rejected")


if __name__ == "__main__":
    print("=" * 50)
    print("UNICODE HANDLING TESTS")
    print("=" * 50)
    print()
    
    try:
        test_utf8_file()
        test_utf8_with_bom()
        test_latin1_file()
        test_windows1252_file()
        test_nonexistent_file()
        
        print()
        print("=" * 50)
        print("[SUCCESS] ALL UNICODE TESTS PASSED!")
        print("=" * 50)
    except Exception as e:
        print(f"\n[ERROR] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
