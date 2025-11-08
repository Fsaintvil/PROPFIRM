from pathlib import Path
from datetime import datetime
p = Path('artifacts') / 'auto_improve' / 'best_lightgbm.txt'
model = str(p.resolve()) if p.exists() else 'None'
s = Path('control')
s.mkdir(parents=True, exist_ok=True)
tmp = s / '.active_model.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(f"loaded_model_path: {model}\n")
    f.write(f"timestamp: {datetime.utcnow().isoformat()}Z\n")
    f.write(f"pid: 30400\n")
try:
    tmp.replace(s / 'active_model.txt')
except Exception:
    tmp.rename(s / 'active_model.txt')
print('WROTE', s / 'active_model.txt')
