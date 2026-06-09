"""Fix GBK-mangled UTF-8 file: read garbled UTF-8, encode as GBK, decode as UTF-8."""
import sys

def fix_encoding(input_path: str, output_path: str):
    # Read the garbled UTF-8 file (current state: garbled Chinese chars in UTF-8)
    with open(input_path, 'r', encoding='utf-8') as f:
        garbled = f.read()

    # Encode the garbled string as GBK → gets back the original UTF-8 bytes
    original_bytes = garbled.encode('gbk')

    # Decode those bytes as UTF-8 → gets correct Chinese text
    fixed = original_bytes.decode('utf-8')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(fixed)

    print(f"Fixed {input_path} -> {output_path}")
    print(f"Input size (chars): {len(garbled)}")
    print(f"Output size (chars): {len(fixed)}")

if __name__ == '__main__':
    fix_encoding(sys.argv[1], sys.argv[2])
