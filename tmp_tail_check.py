from pathlib import Path

# Path to file
p = Path("scripts/restart_optimized.py")
b = p.read_bytes()
print("FILE LEN", len(b))
TAIL = b[-64:]
print("TAIL HEX", TAIL.hex())
print("TAIL BYTES", list(TAIL))
# count trailing newline like bytes
i = len(b) - 1
count = 0
while i >= 0 and b[i] in (10, 13):
    count += 1
    i -= 1
print("TRAILING_NEWLINE_BYTES", count)
print("LAST_NON_NL_INDEX", i)
print("LAST_NON_NL_BYTE", b[i] if i >= 0 else None)
