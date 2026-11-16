"""Comprobación de la CI para la excepción de módulos (LICENSE-EXCEPTION): el archivo existe, nombra la Interfaz de
Módulos (app/promod.py y app/prohooks.py) y esa interfaz sigue siendo la única puerta por la que el núcleo carga código
de afuera. Fuera de app/promod.py no puede haber carga de código desde un archivo o un paquete (spec_from_file_location,
exec_module, SourceFileLoader, zipimport…) ni imports dinámicos con un nombre que no esté escrito en el código.
Uso: python scripts/check_module_door.py  (sale con 1 y dice qué falla)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOOR = "app/promod.py"
LOADERS = re.compile(r"spec_from_file_location|module_from_spec|exec_module|SourceFileLoader|zipimport|load_source|imp\.load_")
DYNAMIC = re.compile(r"(?:__import__|import_module)\(\s*(?![\"'])")
fails = []

exc = ROOT / "LICENSE-EXCEPTION"
if not exc.is_file():
    fails.append("Falta LICENSE-EXCEPTION")
else:
    text = exc.read_text(encoding="utf-8")
    for need in ("app/promod.py", "app/prohooks.py", "GPLv3", "sección 7", "section 7"):
        if need not in text:
            fails.append(f"LICENSE-EXCEPTION no menciona «{need}»")
for f in ("app/promod.py", "app/prohooks.py"):
    if not (ROOT / f).is_file():
        fails.append(f"Falta {f}, la Interfaz de Módulos que nombra LICENSE-EXCEPTION")
for p in sorted((ROOT / "app").rglob("*.py")):
    rel = p.relative_to(ROOT).as_posix()
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if rel != DOOR and LOADERS.search(code):
            fails.append(f"{rel}:{n}: carga código fuera del cargador de módulos ({DOOR}): {line.strip()[:100]}")
        if DYNAMIC.search(code):
            fails.append(f"{rel}:{n}: import dinámico con un nombre que no está escrito en el código: {line.strip()[:100]}")
print("\n".join(fails) if fails else "La Interfaz de Módulos sigue siendo la única puerta y LICENSE-EXCEPTION está en su lugar.")
sys.exit(1 if fails else 0)
