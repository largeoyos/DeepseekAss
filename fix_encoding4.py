"""Fix GBK-mangled UTF-8 using Windows API for proper CP936 encoding."""
import sys
import ctypes
from ctypes import wintypes

# Windows code page constants
CP_UTF8 = 65001
CP_GBK = 936

def win_wide_to_multi(password, code_page=CP_GBK):
    """Convert UTF-16 string to multi-byte string using specified code page."""
    if password is None:
        return None

    # Get required buffer size
    flags = 0
    size = ctypes.windll.kernel32.WideCharToMultiByte(
        code_page, flags, password, -1, None, 0, None, None)
    if size == 0:
        raise ctypes.WinError()

    # Allocate buffer and convert
    buffer = ctypes.create_string_buffer(size)
    result = ctypes.windll.kernel32.WideCharToMultiByte(
        code_page, flags, password, -1, buffer, size, None, None)
    if result == 0:
        raise ctypes.WinError()

    return buffer.value  # bytes (null-terminated, but .value strips the null)

def win_multi_to_wide(data_bytes, code_page=CP_GBK):
    """Convert multi-byte string to UTF-16 string using specified code page."""
    if data_bytes is None:
        return None

    # Get required buffer size (in wide chars)
    size = ctypes.windll.kernel32.MultiByteToWideChar(
        code_page, 0, data_bytes, -1, None, 0)
    if size == 0:
        raise ctypes.WinError()

    # Allocate buffer and convert
    buffer = ctypes.create_unicode_buffer(size)
    result = ctypes.windll.kernel32.MultiByteToWideChar(
        code_page, 0, data_bytes, -1, buffer, size)
    if result == 0:
        raise ctypes.WinError()

    return buffer.value  # str (null-terminated)

def fix_via_winapi(input_path: str, output_path: str):
    """Use Windows APIs for the encoding roundtrip."""
    # Read the garbled UTF-8 file as raw bytes
    with open(input_path, 'rb') as f:
        raw_bytes = f.read()

    print(f"Input file: {len(raw_bytes)} bytes")

    # Step 1: Decode raw bytes as UTF-8 to get the garbled string
    garbled = raw_bytes.decode('utf-8')
    print(f"UTF-8 decode OK: {len(garbled)} chars")

    # Step 2: Encode garbled string as CP936 (Windows GBK) using Windows API
    original_utf8_bytes = win_wide_to_multi(garbled, CP_GBK)
    print(f"CP936 encode OK: {len(original_utf8_bytes)} bytes")

    # Step 3: Decode as UTF-8 to get the correct text
    fixed = original_utf8_bytes.decode('utf-8')
    print(f"UTF-8 decode OK: {len(fixed)} chars")

    # Write output
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed)
    print(f"Written to {output_path}")

    # Show first line
    first_line = fixed.split('\n')[0]
    print(f"First line: {first_line}")

if __name__ == '__main__':
    fix_via_winapi(sys.argv[1], sys.argv[2])
