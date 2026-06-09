"""Fix GBK-mangled UTF-8 file - try multiple approaches."""
import sys

def try_codec(input_path: str, output_path: str, codec: str = 'gbk'):
    with open(input_path, 'r', encoding='utf-8') as f:
        garbled = f.read()

    try:
        original_bytes = garbled.encode(codec)
    except UnicodeEncodeError as e:
        print(f"Failed with {codec}: {e}")
        print(f"Problem char: U+{ord(e.object[e.start]):04X}")
        return False

    fixed = original_bytes.decode('utf-8')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed)

    print(f"OK with {codec}: {len(garbled)} chars -> {len(fixed)} chars")
    return True

def try_byte_by_byte(input_path: str, output_path: str):
    """Read raw bytes, try to interpret as both GBK and UTF-8."""
    with open(input_path, 'rb') as f:
        raw = f.read()

    # Try decoding raw bytes as GBK
    try:
        decoded_as_gbk = raw.decode('gbk')
        print(f"Raw bytes decoded as GBK: {len(decoded_as_gbk)} chars")
        # If this looks right, write it out as UTF-8
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(decoded_as_gbk)
        print("Byte-level GBK decode worked!")
        return True
    except Exception as e:
        print(f"Raw bytes -> GBK failed: {e}")

    # Try with errors='replace'
    decoded_as_gbk = raw.decode('gbk', errors='replace')
    print(f"Raw bytes decoded as GBK (replace): {len(decoded_as_gbk)} chars")
    # Check for replacement characters
    replace_count = decoded_as_gbk.count('�')
    print(f"Replacement chars: {replace_count}")
    if replace_count < len(decoded_as_gbk) * 0.1:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(decoded_as_gbk)
        print(f"Byte-level GBK decode (replace) worked with {replace_count} replacements")
        return True

    return False

if __name__ == '__main__':
    input_file = sys.argv[1]
    base = input_file.rsplit('.', 1)[0]

    # Method 1: Try different codecs for string-level roundtrip
    for codec in ['gbk', 'gb18030', 'cp936']:
        out = f"{base}-fixed-{codec}.md"
        if try_codec(input_file, out, codec):
            print(f"SUCCESS with {codec} -> {out}")
            break
    else:
        print("\nString-level approaches failed. Trying byte-level...")

    # Method 2: Byte-level approach
    out2 = f"{base}-fixed-raw.md"
    if try_byte_by_byte(input_file, out2):
        print(f"SUCCESS with raw byte decode -> {out2}")
