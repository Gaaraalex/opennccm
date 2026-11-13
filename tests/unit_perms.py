"""Permisos por usuario (app/perms.py), sin red.
- Toda ruta que pide un rol tiene su función, y sin lista propia cada rol puede exactamente lo mismo que antes de 2.39.
- La lista propia manda; el maestro lo tiene todo; quien no es maestro no da ni quita funciones críticas."""
import ast
import glob
import json
import os
import re
import sys
import tempfile
from types import SimpleNamespace

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app import perms  # noqa: E402
from app.db import init_db  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1) cada ruta con rol -> su función, con el mismo rol mínimo que antes
routes = []
for f in glob.glob(os.path.join(ROOT, "app", "*.py")):
    for fn in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = [d.args[0].value for d in fn.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                 and d.func.attr in ("get", "post") and d.args and isinstance(d.args[0], ast.Constant)]
        roles = {n.args[1].value for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "require"
                 and len(n.args) > 1 and isinstance(n.args[1], ast.Constant)}
        routes += [(p, r) for p in paths for r in roles if r in ("operator", "admin")]
missing, wrong = [], []
for path, role in routes:
    concrete = re.sub(r"\{[^}]*:int\}|\{(?:\w*id|tid|cid|gid|sid|nid|eid|rid|pid)\}", "1", path)
    concrete = re.sub(r"\{[^}]+\}", "abc", concrete)
    key = perms.for_path(concrete)
    if key is None:
        missing.append(path)
    elif perms.ROLE_MIN[key] != role:
        wrong.append((path, role, key, perms.ROLE_MIN[key]))
check(f"las {len(routes)} rutas con rol tienen su función", not missing, missing)
check("y la función pide el mismo rol que antes (sin lista propia nada cambia)", not wrong, wrong)

# 2) roles sin lista propia
op = SimpleNamespace(role="operator", perms=None, is_master=False)
vw = SimpleNamespace(role="viewer", perms=None, is_master=False)
ad = SimpleNamespace(role="admin", perms=None, is_master=False)
check("operador: respalda, envía cambios y revierte; no restaura ni administra",
      perms.has(op, "backup") and perms.has(op, "push") and perms.has(op, "rollback")
      and not perms.has(op, "restore") and not perms.has(op, "users") and not perms.has(op, "credentials"))
check("lector: no hace nada que cambie", not (perms.of(vw) - {"console_ro", "console_rw"}))
check("administrador: todo lo de su rol", all(perms.has(ad, k) for k in perms.KEYS if not k.startswith("console")))

# 3) lista propia
cust = SimpleNamespace(role="operator", perms=json.dumps(["backup", "audit"]), is_master=False)
check("lista propia: solo lo marcado (operador sin «enviar configuraciones»)",
      perms.has(cust, "backup") and not perms.has(cust, "push") and perms.console_mode(cust) == "none")
vw2 = SimpleNamespace(role="viewer", perms=json.dumps(["backup"]), is_master=False)
check("a un lector se le puede dar «respaldar»", perms.has(vw2, "backup"))
check("lo que se guarda es None si coincide con el rol", perms.encode(perms.role_defaults("admin")
                                                                       | perms._console_default(ad), "admin") is None)
m = SimpleNamespace(role="admin", perms=json.dumps([]), is_master=True)
check("el maestro lo tiene todo aunque su lista esté vacía", perms.of(m) == set(perms.KEYS))

# 4) quién da qué
nm = SimpleNamespace(role="admin", perms=None, is_master=False)
new = perms.from_form(nm, None, "operator", ["backup", "push", "rollback", "audit"], True)
check("un admin que no es maestro no da funciones críticas a un usuario nuevo",
      set(json.loads(new)) == {"backup", "audit"}, new)
tgt = SimpleNamespace(role="operator", perms=None, is_master=False)
kept = set(json.loads(perms.from_form(nm, tgt, "operator", ["backup"], False)))
check("…ni se las quita a uno existente (las críticas quedan como estaban)",
      {"push", "rollback", "firmware_install"} <= kept and "audit" not in kept, kept)
given = perms.from_form(m, tgt, "operator", ["backup", "users"], False)
check("el maestro da y quita lo que quiera", set(json.loads(given)) == {"backup", "users"}, given)
check("las críticas son las que cambian equipos o dan acceso",
      {"push", "rollback", "restore", "console_rw", "firmware_install", "approve", "credentials", "users", "settings"}
      <= perms.CRITICAL and "backup" not in perms.CRITICAL)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
