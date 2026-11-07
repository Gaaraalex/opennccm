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
# SwitchOS no tiene línea de comandos y «Genérico SSH» no es un equipo concreto: todos los demás, 10 recetas o más
poor = [p.key for p in PROFILES if p.key not in ("mikrotik_swos", "generic") and by_os.get(os_of(p.key), 0) < 10]
check("todos los tipos de equipo (salvo SwOS y Genérico SSH) tienen 10 recetas o más", not poor, poor)
check("las claves de las recetas no se repiten", len(recipes.BY_KEY) == len(recipes.BUILTIN))
# pestaña Recetas en tres columnas: nombre sin la plataforma, tarea común y marcas de los tipos de equipo
from app import consolecenter  # noqa: E402
check("nombre corto: sin la plataforma del final", consolecenter._short("Habilitar SSH (VOSS / Fabric Engine)", "VOSS / Fabric Engine") == "Habilitar SSH")
check("nombre corto: también si la plataforma está escrita distinto", consolecenter._short("Crear un usuario (EdgeOS)", "EdgeOS / VyOS") == "Crear un usuario")
check("tarea: las del generador y las escritas a mano con la misma clave", consolecenter._task_of(recipes.BY_KEY["dlink-syslog"]) == "syslog"
      and consolecenter._task_of(recipes.BY_KEY["voss-sysname"]) == "hostname")
tasks = consolecenter.recipe_tasks()
check("tareas: nombre sin «({0})»", all("{" not in t["n"] and not t["n"].endswith(")") for t in tasks.values()), [t["n"] for t in tasks.values() if "{" in t["n"]])
brands = consolecenter.recipe_brands()
check("marcas: una por fabricante de los tipos de equipo, sin «Genérico»", len(brands) >= 70 and all(b["n"] != "Genérico" for b in brands), len(brands))
check("marcas: Calix tiene la CLI tipo Cisco (B6) y EXA", {"ioslike", "calixexa"} <= set(next(b for b in brands if b["n"] == "Calix")["os"]))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
