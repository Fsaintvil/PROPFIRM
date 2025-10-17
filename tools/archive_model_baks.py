"""Archive .bak files from MT5_FTMO_IA/models into models/archives/<timestamp>/ and create a zip."""
from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'MT5_FTMO_IA' / 'models'
ARCH_ROOT = MODELS / 'archives'
ARCH_ROOT.mkdir(exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
dest = ARCH_ROOT / ts
dest.mkdir()

bak_files = list(MODELS.glob('*.bak*'))
for b in bak_files:
    shutil.move(str(b), str(dest / b.name))

zip_path = str(ARCH_ROOT / f'model_baks_{ts}.zip')
shutil.make_archive(str(ARCH_ROOT / f'model_baks_{ts}'), 'zip', root_dir=str(dest))
print('Archived', len(bak_files), 'files to', zip_path)
