"""Set all min_score to 0.70."""
import re

for fname in ["engine_simple/signal_validator.py", "engine_simple/strategy.py"]:
    with open(fname, "r", encoding="utf-8") as f:
        content = f.read()
    if "signal_validator" in fname:
        content = re.sub(r'"(\w+(?:\.\w+)?)":\s*0\.75', r'"\1": 0.70', content)
    else:
        content = content.replace('"min_score": 0.75', '"min_score": 0.70')
    with open(fname, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"{fname}: done")
