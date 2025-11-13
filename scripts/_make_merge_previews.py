"""
Create merged preview files grouped by filename prefix.
Usage: python scripts/_make_merge_previews.py --dirs scripts tools improvements tests --out patches/merged_preview
This script is non-destructive: it only writes preview files into the out dir.
"""
import argparse
import os
from pathlib import Path


def compute_prefix(name: str) -> str:
    base = name
    if base.endswith('.py'):
        base = base[:-3]
    if '_' in base:
        return base.split('_', 1)[0]
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dirs', nargs='+', default=['scripts', 'tools', 'improvements', 'tests'])
    ap.add_argument('--out', default='patches/merged_preview')
    ap.add_argument('--root', default='.')
    ap.add_argument('--min-group', type=int, default=2, help='Minimum files to create a preview')
    args = ap.parse_args()

    root = Path(args.root).resolve()
    outdir = (root / args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    files = []
    for d in args.dirs:
        p = root / d
        if not p.exists():
            continue
        for f in p.rglob('*.py'):
            # skip venv inside tree
            if '\\.venv\\' in str(f) or '/.venv/' in str(f):
                continue
            # skip files under patches/merged_preview
            if 'patches' in f.parts and 'merged_preview' in f.parts:
                continue
            files.append(f)

    groups = {}
    for f in files:
        prefix = compute_prefix(f.name)
        groups.setdefault(prefix, []).append(f)

    created = []
    for prefix, flist in sorted(groups.items()):
        if len(flist) < args.min_group:
            continue
        # sort by path then filename
        flist_sorted = sorted(flist, key=lambda p: (str(p.parent), p.name))
        lastfile = flist_sorted[-1]
        out_name = f"{lastfile.stem}.merged_preview.py"
        out_path = outdir / out_name
        with open(out_path, 'w', encoding='utf-8') as out_f:
            out_f.write('# Merged preview for prefix: {}\n'.format(prefix))
            out_f.write('# Generated from {} files\n'.format(len(flist_sorted)))
            out_f.write('\n')
            for src in flist_sorted:
                out_f.write('#' * 80 + '\n')
                out_f.write(f"# FROM: {str(src.relative_to(root))}\n")
                out_f.write('#' * 80 + '\n')
                try:
                    content = src.read_text(encoding='utf-8')
                except Exception:
                    content = f'# ERROR reading {src}\n'
                out_f.write(content)
                out_f.write('\n\n')
            out_f.write('# End of merged preview\n')
        created.append(str(out_path.relative_to(root)))

    print('Groups found:', len(groups))
    print('Preview files created:', len(created))
    for c in created:
        print(' -', c)

if __name__ == '__main__':
    main()
