"""Fix GBK-mangled UTF-8 - robust byte-level approach."""
import sys

def decode_raw_as_gbk(input_path: str, output_path: str):
    """Read raw bytes of garbled UTF-8 file, decode as GBK directly."""
    with open(input_path, 'rb') as f:
        raw_bytes = f.read()

    # The file on disk is UTF-8 encoded garbled chars.
    # Try decoding those raw bytes as GBK directly.
    try:
        fixed = raw_bytes.decode('gbk')
        print(f"Direct GBK decode: {len(raw_bytes)} bytes -> {len(fixed)} chars")
    except UnicodeDecodeError as e:
        print(f"Direct GBK decode failed at byte {e.start}: {e}")
        # Use replace for problematic bytes
        fixed = raw_bytes.decode('gbk', errors='replace')
        replacements = fixed.count('�')
        print(f"Used replace mode, {replacements} replacements")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed)
    print(f"Written to {output_path}")

    # Verify by showing first few lines
    lines = fixed.split('\n')
    for line in lines[:3]:
        print(repr(line))

def decode_utf8_then_reinterpret(input_path: str, output_path: str):
    """Read garbled UTF-8, get raw bytes of garbled string as if it were Latin-1,
    then decode as GBK."""
    import codecs

    with open(input_path, 'r', encoding='utf-8') as f:
        garbled = f.read()

    # Step 1: Encode garbled string as Latin-1 (byte-per-byte for BMP chars)
    # This gives us the UTF-8 bytes that are the garbled string on disk
    utf8_bytes = garbled.encode('utf-8')

    # Step 2: Try to decode those bytes as GBK
    try:
        fixed = utf8_bytes.decode('gbk')
        print(f"UTF8->Latin1->GBK roundtrip OK: {len(fixed)} chars")
    except UnicodeDecodeError as e:
        print(f"UTF8->GBK decode failed: {e}")
        fixed = utf8_bytes.decode('gbk', errors='replace')
        print(f"Used replace, {fixed.count('�')} replacements")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed)
    print(f"Written to {output_path}")

    lines = fixed.split('\n')
    for line in lines[:3]:
        print(repr(line))

if __name__ == '__main__':
    input_file = sys.argv[1]
    base = input_file.rsplit('.', 1)[0]

    print("=== Method 1: Decode raw bytes as GBK ===")
    decode_raw_as_gbk(input_file, f"{base}-fixed-raw.md")

    print("\n=== Method 2: UTF-8 string -> UTF-8 bytes -> decode as GBK ===")
    decode_utf8_then_reinterpret(input_file, f"{base}-fixed-v2.md")
