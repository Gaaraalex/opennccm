"""Idiomas: todas las plantillas siguen siendo válidas tras marcarlas para traducir, cada texto de la interfaz tiene
traducción en cada catálogo (con los mismos huecos {0}, {1}…) y las funciones de traducción escapan bien el HTML."""
import json
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from jinja2 import Environment  # noqa: E402

from app import i18n  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# --- plantillas: el texto marcado sigue siendo Jinja válido
env = Environment()
bad = []
for f in sorted((i18n.LOCALES.parent / "templates").glob("*.html")):
    try:
        env.parse(i18n.preprocess(f.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        bad.append(f"{f.name}: {e}")
check("las 40+ plantillas compilan tras marcarlas", not bad, bad)

# --- el marcado: frases con valores, atributos, scripts y confirmaciones
out = i18n.preprocess('<p class="x">Hay {{ n }} equipos</p><input placeholder="Buscar…" title="{{ t }}">'
                      "<script>var a = 'no tocar';</script><button onclick=\"return confirm('¿Eliminar?')\">Borrar</button>"
                      '<a href="#" onclick="[1].forEach(x => x)">Todos</a>')
check("frase con valor como una sola unidad", '_h("Hay {0} equipos", (n))' in out, out)
check("atributo placeholder traducible", 'placeholder="{{ _h("Buscar…") }}"' in out, out)
check("atributo solo con valor: sin cambios", 'title="{{ t }}"' in out, out)
check("el contenido de <script> no se toca", "var a = 'no tocar';" in out, out)
check("confirm() traducible", "confirm('{{ _(\"¿Eliminar?\")|jsq }}')" in out, out)
check("atributo con «=>» no rompe la etiqueta", '[1].forEach(x => x)">{{ _h("Todos") }}' in out, out)

# --- catálogos completos
ids = i18n.extract()
check("hay textos para traducir", len(ids) > 900, len(ids))
for lang in i18n.LANGS:
    if lang == i18n.DEFAULT:
        continue
    cat = json.loads((i18n.LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
    missing = [k for k in ids if k not in cat]
    check(f"{lang}: todos los textos traducidos ({len(ids) - len(missing)}/{len(ids)})", not missing,
          "; ".join(f"[{ids[k]}] {k}" for k in missing[:15]) + " … (scripts/i18n_missing.py)")
    holes = [k for k, v in cat.items() if sorted(re.findall(r"\{\d\}", k)) != sorted(re.findall(r"\{\d\}", v))]
    check(f"{lang}: mismos huecos {{0}}… en origen y traducción", not holes, holes[:10])
    empty = [k for k, v in cat.items() if not v.strip()]
    check(f"{lang}: sin traducciones vacías", not empty, empty[:10])

# --- traducción en tiempo de ejecución
tok = i18n.set_lang("en")
check("traduce al inglés", i18n._("Dispositivos") == "Devices", i18n._("Dispositivos"))
check("frase con huecos", i18n._f("Tarea «{0}» creada: {1}", "X", "Y") == 'Task "X" created: Y',
      i18n._f("Tarea «{0}» creada: {1}", "X", "Y"))
check("_h escapa los valores", str(i18n._h("Usuario «{0}» eliminado.", "<b>x</b>")) == 'User "&lt;b&gt;x&lt;/b&gt;" deleted.',
      str(i18n._h("Usuario «{0}» eliminado.", "<b>x</b>")))
check("texto desconocido queda en español", i18n._("Texto que no existe") == "Texto que no existe")
i18n._lang.reset(tok)
check("por omisión, español", i18n._("Dispositivos") == "Dispositivos")
check("jsq escapa comillas para JavaScript en atributos", str(i18n.jsq("it's")) == "it\\&#39;s", str(i18n.jsq("it's")))

# --- elección del idioma
check("preferencia guardada primero", i18n.pick_lang("en", "es-ES,es;q=0.9") == "en")
check("si no, la del navegador", i18n.pick_lang(None, "en-US,en;q=0.9,es;q=0.8") == "en")
check("idioma no disponible: el predeterminado", i18n.pick_lang(None, "fr-FR,de;q=0.8", "en") == "en")
check("sin nada: español", i18n.pick_lang(None, "") == "es")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
