#!/usr/bin/env python3
"""
Correcteur automatique pour les erreurs de qualité de code communes.
"""

from pathlib import Path
from typing import Dict


class CodeQualityFixer:
    """Corrige automatiquement les erreurs de lint communes."""

    def __init__(self):
        self.fixes_applied = 0
        self.line_length_limit = 79

    def fix_trailing_whitespace(self, content: str) -> str:
        """Supprime les espaces en fin de ligne."""
        lines = content.split('\n')
        fixed_lines = [line.rstrip() for line in lines]
        return '\n'.join(fixed_lines)

    def fix_long_lines(self, content: str) -> str:
        """Corrige les lignes trop longues via des techniques sûres."""
        lines = content.split('\n')
        fixed_lines = []

        for line in lines:
            if len(line) <= self.line_length_limit:
                fixed_lines.append(line)
                continue

            # Cas 1: Lignes de commentaires longues
            if line.strip().startswith('#'):
                fixed_lines.append(line)  # Garder tel quel pour maintenant
                continue

            # Cas 2: Chaînes de caractères longues
            if '"' in line or "'" in line:
                if 'f"' in line or "f'" in line:
                    # F-string - diviser prudemment
                    indent = len(line) - len(line.lstrip())
                    if '(' in line and ')' in line:
                        # Essayer de diviser aux virgules
                        parts = line.split(',')
                        limit = self.line_length_limit
                        if len(parts) > 1 and len(parts[0]) < limit:
                            fixed_lines.append(parts[0] + ',')
                            for part in parts[1:-1]:
                                fixed_lines.append(
                                    ' ' * (indent + 4) + part.strip() + ','
                                )
                            if parts[-1].strip():
                                fixed_lines.append(
                                    ' ' * (indent + 4) + parts[-1].strip()
                                )
                            continue

            # Cas 3: Conditions longues avec 'and'/'or'
            if ' and ' in line or ' or ' in line:
                indent = len(line) - len(line.lstrip())
                if 'if (' in line:
                    # Déjà dans une condition multi-ligne
                    fixed_lines.append(line)
                    continue

                # Diviser aux opérateurs logiques
                for op in [' and ', ' or ']:
                    if op in line:
                        parts = line.split(op)
                        if len(parts) == 2:
                            part1 = parts[0] + op.rstrip()
                            part2 = (' ' * (indent + 8) +
                                     parts[1].strip() + '):')
                            # Parenthèse non fermée
                            if part1.count('(') == part1.count(')') + 1:
                                fixed_lines.append(part1)
                                fixed_lines.append(part2)
                                continue

            # Si aucune technique ne fonctionne, garder tel quel
            fixed_lines.append(line)

        return '\n'.join(fixed_lines)

    def fix_indentation_issues(self, content: str) -> str:
        """Corrige les problèmes d'indentation communes."""
        lines = content.split('\n')
        fixed_lines = []

        for i, line in enumerate(lines):
            # Continuation line under-indented
            if (i > 0 and
                (lines[i-1].endswith('(') or lines[i-1].endswith(',') or
                 lines[i-1].strip().endswith('and') or
                 lines[i-1].strip().endswith('or'))):

                prev_indent = len(lines[i-1]) - len(lines[i-1].lstrip())
                current_indent = len(line) - len(line.lstrip())

                if current_indent <= prev_indent and line.strip():
                    # Ajouter 4 espaces d'indentation supplémentaires
                    fixed_lines.append(' ' * (prev_indent + 4) + line.lstrip())
                else:
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)

        return '\n'.join(fixed_lines)

    def fix_file(self, file_path: str) -> Dict[str, int]:
        """Corrige un fichier et retourne les statistiques."""
        path = Path(file_path)
        if not path.exists():
            return {"error": "Fichier non trouvé"}

        try:
            with open(path, 'r', encoding='utf-8') as f:
                original_content = f.read()

            # Appliquer les corrections
            content = original_content

            # 1. Espaces en fin de ligne
            before_whitespace = content.count(' \n') + content.count('\t\n')
            content = self.fix_trailing_whitespace(content)
            after_whitespace = content.count(' \n') + content.count('\t\n')
            whitespace_fixes = before_whitespace - after_whitespace

            # 2. Lignes trop longues
            lines_before = [
                line for line in content.split('\n')
                if len(line) > self.line_length_limit
            ]
            content = self.fix_long_lines(content)
            lines_after = [
                line for line in content.split('\n')
                if len(line) > self.line_length_limit
            ]
            long_line_fixes = len(lines_before) - len(lines_after)

            # 3. Problèmes d'indentation
            content = self.fix_indentation_issues(content)

            # Sauvegarder si des changements ont été apportés
            if content != original_content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)

            return {
                "whitespace_fixes": whitespace_fixes,
                "long_line_fixes": long_line_fixes,
                "total_fixes": whitespace_fixes + long_line_fixes
            }

        except Exception as e:
            return {"error": str(e)}


def main():
    """Fonction principale pour test."""
    fixer = CodeQualityFixer()

    # Tester sur le fichier de trading
    file_path = ("c:\\Users\\saint\\Documents\\PROPFIRM\\scripts\\"
                 "live_trading_engine.py")
    results = fixer.fix_file(file_path)

    print("🔧 Résultats de correction automatique:")
    for key, value in results.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
