"""Ver las credenciales de los equipos: Ajustes decide quién (nadie, solo administradores o también operadores), hace
falta la verificación en dos pasos activa y cada vista se confirma con el usuario y la contraseña propios.
Requiere haber ejecutado antes e2e.py (usuario admin / Admin12345). Usa la misma base que la aplicación (NCM_DATA_DIR)."""
import html as htmlmod
import re
import sys
import time

import pyotp
import requests

sys.path.insert(0, ".")
from app.db import AuditLog, Credential, Device, SessionLocal, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []
PW = {"cvadmin": "Cv-Admin-2026x", "cvoper": "Cv-Oper-2026x", "cvlector": "Cv-Lector-2026x"}
SECRET_PW, SECRET_EN = "Equipo-Clave-9!x", "Enable-Clave-7?"


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(user, pw, code=None):
    s = requests.Session()
    s.post(B + "/login", data={"username": user, "password": pw})
    if code:
        s.post(B + "/login/2fa", data={"code": code})
    return s


def logged_in(s):
    return s.get(B + "/account").url.rstrip("/").endswith("/account")


def enable_mfa(s):
    page = s.get(B + "/account/2fa/setup").text
    secret = re.search(r"user-select:all\">([A-Z2-7]{16,})</code>", page).group(1)
    r = s.post(B + "/account/2fa/setup", data={"code": pyotp.TOTP(secret).now()})
    check("2FA activada para la prueba", "activada" in r.text, r.url)
    return secret


def who(value):
    set_setting("cred_view", value)
    time.sleep(31)  # la aplicación relee los ajustes como mucho cada 30 s


def audits(action):
    with SessionLocal() as db:
        return list(db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)))


adm = login("admin", "Admin12345")
for u, role in (("cvadmin", "admin"), ("cvoper", "operator"), ("cvlector", "viewer")):
    adm.post(B + "/users", data={"username": u, "role": role, "password": PW[u], "password2": PW[u]})
adm.post(B + "/credentials", data={"name": "cv-equipo", "username": "netadmin", "password": SECRET_PW, "secret": SECRET_EN})
with SessionLocal() as db:
    cred = db.scalar(select(Credential).where(Credential.name == "cv-equipo"))
    dev = Device(name="cv-sw1", host="192.0.2.77", device_type="cisco_ios", credential_id=cred.id, enabled=False)
    db.add(dev)
    db.commit()
    dev_id, cid = dev.id, cred.id
DEV, LST = f"{B}/devices/{dev_id}", f"{B}/credentials/{cid}/view"
VIEW = f"{DEV}/credential/view"

# ---------------------------------------------------------------- de fábrica: nadie
r = adm.get(B + "/settings")
check("Ajustes: opción «quién puede ver las credenciales» con Nadie por defecto",
      'name="cred_view"' in r.text and re.search(r'<option value="off" selected>', r.text))
a = login("cvadmin", PW["cvadmin"])
o = login("cvoper", PW["cvoper"])
v = login("cvlector", PW["cvlector"])
check("nadie: la ficha no ofrece «Ver»", "/credential/view" not in a.get(DEV).text)
check("nadie: ni un administrador puede abrirla", a.get(VIEW).status_code == 403 and a.get(LST).status_code == 403)
check("nadie: tampoco con usuario y contraseña", a.post(VIEW, data={"username": "cvadmin", "password": PW["cvadmin"]}).status_code == 403)

# ---------------------------------------------------------------- solo administradores
who("admin")
check("solo administradores: la ficha ofrece «Ver» al administrador", "/credential/view" in a.get(DEV).text)
check("solo administradores: la lista de credenciales también", f"/credentials/{cid}/view" in a.get(B + "/credentials").text)
check("solo administradores: el operador no ve el botón", "/credential/view" not in o.get(DEV).text)
check("solo administradores: el operador no puede abrirla", o.get(VIEW).status_code == 403)
r = a.get(VIEW)
check("sin MFA: se le dice que primero debe activarla", "Primero debes activar la verificación en dos pasos" in htmlmod.unescape(r.text)
      and "/account/2fa/setup" in r.text and 'name="password"' not in r.text)
r = a.post(VIEW, data={"username": "cvadmin", "password": PW["cvadmin"]})
check("sin MFA: ni con la contraseña correcta se muestra", SECRET_PW not in r.text and "Primero debes activar" in htmlmod.unescape(r.text))
enable_mfa(a)
r = a.get(VIEW)
check("con MFA: pide usuario y contraseña", 'name="username"' in r.text and 'type="password"' in r.text and SECRET_PW not in r.text)
r = a.post(VIEW, data={"username": "cvadmin", "password": "mala-clave-1"})
check("contraseña incorrecta: no se muestra y lo dice", SECRET_PW not in r.text and "incorrectos" in r.text)
r = a.post(VIEW, data={"username": "admin", "password": "Admin12345"})
check("con el usuario y la contraseña de otra cuenta: no se muestra", SECRET_PW not in r.text)
r = a.post(VIEW, data={"username": "cvadmin", "password": PW["cvadmin"]})
check("usuario y contraseña correctos: muestra usuario, contraseña y enable",
      "netadmin" in r.text and htmlmod.escape(SECRET_PW) in r.text and htmlmod.escape(SECRET_EN) in r.text)
check("la respuesta no se guarda en el navegador", "no-store" in r.headers.get("cache-control", ""))
r = a.get(VIEW)
check("cada vez que se pulsa «Ver» vuelve a pedirlos", SECRET_PW not in r.text and 'name="password"' in r.text)
r = a.post(LST, data={"username": "cvadmin", "password": PW["cvadmin"]})
check("también desde la lista de credenciales", htmlmod.escape(SECRET_PW) in r.text)
seen = audits("credential_view")
check("cada vista queda en la auditoría", len(seen) == 2 and seen[0].username == "cvadmin" and "cv-equipo" in seen[0].detail,
      [(x.username, x.detail) for x in seen])
check("los intentos fallidos también", len([x for x in audits("credential_view_failed") if x.username == "cvadmin"]) == 2)
check("el lector nunca", v.get(VIEW).status_code == 403)

# ---------------------------------------------------------------- administradores y operadores
who("operator")
check("operadores: la ficha le ofrece «Ver»", "/credential/view" in o.get(DEV).text)
r = o.get(VIEW)
check("operador sin MFA: debe activarla primero", "Primero debes activar" in htmlmod.unescape(r.text))
enable_mfa(o)
r = o.post(VIEW, data={"username": "cvoper", "password": PW["cvoper"]})
check("operador con MFA y su contraseña: la ve", htmlmod.escape(SECRET_PW) in r.text)
check("operador: la lista de credenciales sigue siendo solo de administradores", o.get(LST).status_code == 403)
check("operadores: el lector sigue sin poder", v.get(VIEW).status_code == 403)
check("operadores: el administrador sigue pudiendo", htmlmod.escape(SECRET_PW) in
      a.post(VIEW, data={"username": "cvadmin", "password": PW["cvadmin"]}).text)

# ---------------------------------------------------------------- intentos fallidos: bloqueo como en el inicio de sesión
for _ in range(5):
    r = o.post(VIEW, data={"username": "cvoper", "password": "no-es-esta-1"})
check("5 intentos fallidos: la cuenta se bloquea y se cierran sus sesiones", not logged_in(o))
r = requests.Session().post(B + "/login", data={"username": "cvoper", "password": PW["cvoper"]})
check("bloqueada: no puede volver a entrar enseguida", "bloqueada" in htmlmod.unescape(r.text), r.url)

who("off")
check("de vuelta a nadie: el administrador ya no puede", a.get(VIEW).status_code == 403)
with SessionLocal() as db:
    db.delete(db.get(Device, dev_id))
    db.commit()

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
