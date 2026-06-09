"""Fix AA.MD encoding - more robust approach"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open(r"E:\Projects\DeepseekAss\plans\AA.MD", "r", encoding="utf-8") as f:
    garbled = f.read()

print(f"Read {len(garbled)} chars")

# Method: use gb18030 on the whole text, then decode the bytes as UTF-8 with error handling
raw_bytes = garbled.encode('gb18030')
fixed = raw_bytes.decode('utf-8', errors='replace')

# Check for replacement chars
replace_count = fixed.count('�')
print(f"UTF-8 decode replacements: {replace_count}")

# Show first lines
for i, line in enumerate(fixed.split('\n')[:10]):
    print(repr(line))

if replace_count == 0:
    print("\nAll bytes were valid UTF-8!")
elif replace_count < 100:
    print(f"\nOnly {replace_count} replacements - acceptable")
else:
    print(f"\n{replace_count} replacements - may have issues")
