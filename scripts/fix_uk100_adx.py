"""Fix UK100.cash adx_thresh comment — was copy-pasted from SOLUSD."""
with open('engine_simple/strategy.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_uk100 = False
for i, line in enumerate(lines):
    if '"UK100.cash"' in line:
        in_uk100 = True
    if in_uk100 and 'adx_thresh' in line and 'SOLUSD' in line:
        lines[i] = '        "adx_thresh": 22,  # Aligné YAML — indice UK, standard 22\n'
        print(f'Fixed line {i+1}: UK100.cash comment corrected')
        break

with open('engine_simple/strategy.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
