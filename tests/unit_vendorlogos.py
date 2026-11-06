"""Logos de marca (app/vendorlogos.py): cada perfil tiene logo, el sprite tiene un símbolo por marca y sin texto."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import vendorlogos as vl  # noqa: E402
from app.drivers import PROFILE_MAP  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


missing = sorted(k for k in PROFILE_MAP if k not in vl.PROFILES)
check("los 131 perfiles tienen logo", not missing and len(PROFILE_MAP) >= 131, missing)
check("cada logo de la tabla es una marca", all(v in vl.BRANDS for v in vl.PROFILES.values()))
sprite = open(os.path.join(os.path.dirname(vl.__file__), "static", "vendor-logos.svg"), encoding="utf-8").read()
ids = set(re.findall(r'<symbol id="vl-([\w-]+)"', sprite))
check("el sprite tiene un símbolo por marca", ids == set(vl.BRANDS), sorted(set(vl.BRANDS) ^ ids))
check("el sprite no depende de fuentes (texto convertido a trazos)", "<text" not in sprite and "font-family" not in sprite)
check("extreme_vsp → Extreme", vl.key("extreme_vsp") == "extreme" and vl.brand("extreme_vsp") == "Extreme")
check("perfil desconocido → other", vl.key("nada") == "other" and vl.key(None) == "other")
h = str(vl.logo("mikrotik_routeros", 10))
check("nunca menos de 14 px y decorativo", 'width="14"' in h and 'aria-hidden="true"' in h and "#vl-mikrotik" in h)
check("con nombre accesible", 'aria-label="Extreme"' in str(vl.logo("extreme_exos", 56, label="Extreme")))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
