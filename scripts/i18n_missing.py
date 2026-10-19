"""Textos de la interfaz que faltan (o sobran) en el catálogo de un idioma.

Uso (desde la raíz del repositorio):
    python scripts/i18n_missing.py en            # lista lo que falta traducir al inglés
    python scripts/i18n_missing.py en --tsv      # lo mismo en formato «texto<TAB>» para completar a mano
    python scripts/i18n_missing.py pt --new      # crea app/locales/pt.json vacío para empezar un idioma nuevo

Para añadir un idioma: créelo con --new, traduzca los textos, y agregue su código a LANGS en app/i18n.py.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.i18n import LOCALES, extract  # noqa: E402

lang = sys.argv[1] if len(sys.argv) > 1 else "en"
path = LOCALES / f"{lang}.json"
if "--new" in sys.argv and not path.exists():
    path.write_text("{}\n", encoding="utf-8")
cat = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
ids = extract()
missing = [k for k in sorted(ids) if k not in cat]
unused = [k for k in sorted(cat) if k not in ids]
if "--tsv" in sys.argv:
    for k in missing:
        print(f"{k}\t")
else:
    for k in missing:
        print(f"FALTA [{ids[k]}] {k}")
    for k in unused:
        print(f"SOBRA {k}")
    print(f"{lang}: {len(ids) - len(missing)}/{len(ids)} textos traducidos, {len(unused)} sin uso")
sys.exit(1 if missing else 0)
