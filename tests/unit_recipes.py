"""Recetas incluidas: cada familia de CLI con recetas tiene al menos 10, las variables de los comandos existen y cada
receta tiene un paso de comprobar al final."""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import recipes  # noqa: E402
from app.consolecore import OS_LABEL, os_of  # noqa: E402
from app.drivers import PROFILES  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


by_os = collections.Counter(r["os"] for r in recipes.BUILTIN)
check("claves únicas", len(recipes.BY_KEY) == len(recipes.BUILTIN))
check("cada receta es de una familia conocida", all(r["os"] in OS_LABEL for r in recipes.BUILTIN),
      sorted({r["os"] for r in recipes.BUILTIN} - set(OS_LABEL)))
low = {k: n for k, n in by_os.items() if n < 10}
check("toda familia con recetas tiene al menos 10", not low, low)
bad = []
for r in recipes.BUILTIN:
    names = {v["n"] for v in r["vars"]}
    used = set(re.findall(r"\{\{(\w+)\}\}", "\n".join(s["c"] for s in r["steps"])))
    if used - names:
        bad.append((r["key"], sorted(used - names)))
check("las variables de los comandos están definidas", not bad, bad)
check("el último paso de cada receta es de comprobar", all(r["steps"][-1].get("check") for r in recipes.BUILTIN),
      [r["key"] for r in recipes.BUILTIN if not r["steps"][-1].get("check")])
for fam, keys in {"Ubiquiti": [p.key for p in PROFILES if p.vendor == "Ubiquiti"],
                  "ZTE": [p.key for p in PROFILES if p.vendor == "ZTE"]}.items():
    poor = [k for k in keys if by_os.get(os_of(k), 0) < 10]
    check(f"{fam}: todos sus equipos tienen 10 recetas o más", not poor, poor)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
