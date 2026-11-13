"""Permisos por usuario y administrador maestro, de punta a punta (servidor de run-tests.sh: admin / Admin12345, que
es el maestro por ser el primer administrador; configlets de ejemplo y el equipo 1 de e2e.py)."""
import re
import sys

import requests

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return " | ".join(re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S))


def login(user, pw):
    s = requests.Session()
    r = s.post(B + "/login", data={"username": user, "password": pw})
    return s, r.url.rstrip("/") == B


def uid(s, name):
    m = re.search(rf"<b>{name}</b>[\s\S]*?/users/(\d+)/edit", s.get(B + "/users").text)
    return int(m.group(1)) if m else None


PW = "Clave-Permisos-1"
adm, ok = login("admin", "Admin12345")
check("admin ingresa", ok)
html = adm.get(B + "/users").text
check("el primer administrador es el maestro", re.search(r"<b>admin</b>[\s\S]*?Maestro", html) is not None)

for name, role in (("perm-op", "operator"), ("perm-jefe", "admin")):
    adm.post(B + "/users", data={"username": name, "role": role, "password": PW, "password2": PW})
op_id, jefe_id, adm_id = uid(adm, "perm-op"), uid(adm, "perm-jefe"), uid(adm, "admin")
check("usuarios de prueba creados", op_id and jefe_id and adm_id, (op_id, jefe_id, adm_id))
op, _ok = login("perm-op", PW)
jefe, _ok2 = login("perm-jefe", PW)

# --- interruptor «Enviar configuraciones»
check("el operador puede abrir «Ejecutar configlet»", op.get(B + "/configlets/1/run").status_code == 200)
adm.post(f"{B}/users/{op_id}/push")
check("con el interruptor apagado ya no puede (403)", op.get(B + "/configlets/1/run").status_code == 403)
check("…ni crear una solicitud desde la consola", op.post(B + "/console/change-request", data={}).status_code == 403)
check("pero sigue pudiendo respaldar", op.post(B + "/devices/1/backup", allow_redirects=False).status_code in (302, 303))
check("un admin que no es maestro no usa el interruptor", jefe.post(f"{B}/users/{op_id}/push").status_code == 403)
adm.post(f"{B}/users/{op_id}/push")
check("el maestro lo vuelve a encender", op.get(B + "/configlets/1/run").status_code == 200)

# --- lista a medida: un lector que puede respaldar
adm.post(f"{B}/users/{op_id}/edit", data={"role": "viewer", "active": "on", "perms_form": "1", "perm": ["backup"]})
check("lector con «respaldar»: respalda", op.post(B + "/devices/1/backup", allow_redirects=False).status_code in (302, 303))
check("…y no ejecuta configlets", op.get(B + "/configlets/1/run").status_code == 403)
check("la lista lo marca como personalizado", re.search(r"<b>perm-op</b>[\s\S]*?personalizado", adm.get(B + "/users").text) is not None)

# --- un administrador que no es maestro
r = jefe.post(f"{B}/users/{op_id}/edit", data={"role": "viewer", "active": "on", "perms_form": "1",
                                                "perm": ["backup", "users", "push", "audit"]})
check("no da funciones críticas (usuarios, enviar configuraciones)",
      op.get(B + "/configlets/1/run").status_code == 403 and op.get(B + "/users").status_code == 403)
check("…pero sí las normales (auditar)", op.post(B + "/compliance/run", allow_redirects=False).status_code in (302, 303))
r = jefe.post(f"{B}/users/{adm_id}/delete")
check("no puede eliminar al maestro", "maestro no se puede eliminar" in flashes(r.text), flashes(r.text))
r = jefe.post(f"{B}/users/{adm_id}/edit", data={"role": "viewer"})
check("ni editarlo", "solo lo puede editar él mismo" in flashes(r.text), flashes(r.text))
check("ni quitarle la verificación en dos pasos", jefe.post(f"{B}/users/{adm_id}/2fa-reset").status_code == 403)
r = jefe.post(B + "/users", data={"username": "perm-otro", "role": "admin", "password": PW, "password2": PW})
check("ni crear administradores", "Solo el administrador maestro puede crear administradores" in flashes(r.text), flashes(r.text))
r = jefe.post(f"{B}/users/{jefe_id}/edit", data={"role": "admin", "active": "on", "perms_form": "1", "perm": []})
check("ni quitarse ni darse permisos a sí mismo", jefe.get(B + "/users").status_code == 200)
check("no ve «Hacerlo administrador maestro»", "make_master" not in jefe.get(f"{B}/users/{op_id}/edit").text)
r = adm.post(f"{B}/users/{adm_id}/edit", data={"role": "admin"})
check("el maestro no se puede bloquear a sí mismo", "<b>admin</b>" in adm.get(B + "/users").text and login("admin", "Admin12345")[1])

# --- pasar el rol de maestro y devolverlo
adm.post(f"{B}/users/{jefe_id}/edit", data={"role": "admin", "active": "on", "make_master": "on"})
html = adm.get(B + "/users").text
check("el maestro pasa el rol a otro", re.search(r"<b>perm-jefe</b>[\s\S]*?Maestro", html) is not None
      and re.search(r"<b>admin</b>[^\n]*\n[^\n]*Maestro", html) is None)
check("el anterior ya no usa el interruptor", adm.post(f"{B}/users/{op_id}/push").status_code == 403)
jefe.post(f"{B}/users/{adm_id}/edit", data={"role": "admin", "active": "on", "make_master": "on"})
check("y se lo devuelven", adm.post(f"{B}/users/{op_id}/push", allow_redirects=False).status_code in (302, 303))
adm.post(f"{B}/users/{op_id}/push")

for i in (op_id, jefe_id):
    adm.post(f"{B}/users/{i}/delete")
check("limpieza", "perm-op" not in adm.get(B + "/users").text)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
