"""Inicio de sesión con Active Directory (directorio LDAP simulado de ldap3) y reglas comunes de los directorios externos:
rol según los grupos, contraseñas vacías, cuentas locales con el mismo nombre y cuentas desactivadas."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server  # noqa: E402

from app import extauth  # noqa: E402
from app.crypto import hash_password  # noqa: E402
from app.db import SessionLocal, User, init_db  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
BASE = "DC=empresa,DC=local"
SVC = f"CN=svc-nccm,OU=Servicios,{BASE}"
G_ADMIN, G_OPS = f"CN=NCCM-Admins,OU=Grupos,{BASE}", f"CN=NCCM-Operadores,OU=Grupos,{BASE}"
server = Server("dc-simulado", get_info=OFFLINE_AD_2012_R2)
seed = Connection(server, user=SVC, password="Servicio-123", client_strategy=MOCK_SYNC)
seed.strategy.add_entry(SVC, {"objectClass": ["user"], "sAMAccountName": "svc-nccm", "userPassword": "Servicio-123"})
for sam, pw, groups, mail in [("ana.admin", "Clave-Ana-1", [G_ADMIN], "ana@empresa.local"),
                              ("oscar", "Clave-Oscar-1", [G_OPS], None),
                              ("visita", "Clave-Visita-1", [f"CN=Otros,OU=Grupos,{BASE}"], None),
                              ("doble", "Clave-Doble-1", [G_OPS, G_ADMIN], None),
                              ("local", "Clave-Local-1", [G_ADMIN], None)]:
    seed.strategy.add_entry(f"CN={sam},OU=Usuarios,{BASE}", {"objectClass": ["user"], "sAMAccountName": sam,
                                                             "userPassword": pw, "memberOf": groups,
                                                             **({"mail": mail} if mail else {})})
seed.bind()
extauth.LDAP_STRATEGY = MOCK_SYNC
CFG = {"url": "ldaps://dc-simulado", "starttls": "0", "verify_tls": "1", "bind_dn": SVC, "bind_password": "Servicio-123",
       "base_dn": BASE, "user_filter": "(&(objectClass=user)(sAMAccountName={username}))",
       "group_admin": G_ADMIN, "group_operator": "NCCM-Operadores", "group_viewer": "", "default_role": ""}


def auth(user, pw, **over):
    return extauth.ldap_authenticate(user, pw, {**CFG, **over}, server=server)


u = auth("ana.admin", "Clave-Ana-1")
check("AD: usuario y contraseña correctos", u is not None and u.username == "ana.admin")
check("AD: rol administrador por su grupo (DN)", u and u.role == "admin", u and u.role)
check("AD: se toma el correo del directorio", u and u.email == "ana@empresa.local", u and u.email)
check("AD: contraseña incorrecta", auth("ana.admin", "otra") is None)
check("AD: contraseña vacía no se acepta (bind anónimo)", auth("ana.admin", "") is None)
check("AD: usuario inexistente", auth("nadie", "x") is None)
check("AD: filtro protegido contra inyección", auth("*", "Clave-Ana-1") is None and auth("ana.admin)(cn=*", "Clave-Ana-1") is None)
o = auth("oscar", "Clave-Oscar-1")
check("AD: rol operador por el nombre del grupo (CN)", o and o.role == "operator", o and o.role)
v = auth("visita", "Clave-Visita-1")
check("AD: sin grupo con acceso → sin rol", v is not None and v.role is None, v and v.role)
v = auth("visita", "Clave-Visita-1", default_role="viewer")
check("AD: rol por omisión si se configura", v and v.role == "viewer", v and v.role)
d = auth("doble", "Clave-Doble-1")
check("AD: en varios grupos gana el rol más alto", d and d.role == "admin", d and d.role)
try:
    auth("ana.admin", "Clave-Ana-1", bind_password="mala")
    check("AD: cuenta de servicio mal configurada da error claro", False)
except extauth.ExtAuthError as e:
    check("AD: cuenta de servicio mal configurada da error claro", "cuenta de servicio" in str(e), str(e))
check("probar conexión: cuenta de servicio", extauth.ldap_test(CFG, server=server) == "ok")
check("probar conexión: usuario y su rol", extauth.ldap_test(CFG, "oscar", server=server).startswith("found:operator:"),
      extauth.ldap_test(CFG, "oscar", server=server))
check("probar conexión: usuario no encontrado", extauth.ldap_test(CFG, "nadie", server=server) == "notfound")

# ---- alta y actualización del usuario de OpenNCCM
with SessionLocal() as db:
    db.add(User(username="local", password_hash=hash_password("Local-OpenNCCM-1"), role="viewer"))
    db.commit()
with SessionLocal() as db:
    user, why = extauth.upsert_user(db, auth("ana.admin", "Clave-Ana-1"), "ldap")
    db.commit()
    check("alta: se crea la cuenta de AD con su rol", user and user.role == "admin" and user.auth_source == "ldap", why)
    uid = user.id
with SessionLocal() as db:
    ext = auth("ana.admin", "Clave-Ana-1")
    ext.role = "operator"  # cambió de grupo en el directorio
    user, _ = extauth.upsert_user(db, ext, "ldap")
    db.commit()
    check("actualización: el rol sigue al directorio en cada ingreso", user.id == uid and user.role == "operator")
with SessionLocal() as db:
    _u, why = extauth.upsert_user(db, auth("local", "Clave-Local-1"), "ldap")
    db.rollback()
    check("una cuenta local con el mismo nombre no se toma desde el directorio", _u is None and why == "local", why)
    _u, why = extauth.upsert_user(db, auth("visita", "Clave-Visita-1"), "ldap")
    db.rollback()
    check("sin rol no se crea la cuenta", _u is None and why == "norole", why)
with SessionLocal() as db:
    db.get(User, uid).active = False
    db.commit()
with SessionLocal() as db:
    _u, why = extauth.upsert_user(db, auth("ana.admin", "Clave-Ana-1"), "ldap")
    db.rollback()
    check("una cuenta desactivada en OpenNCCM no entra", _u is None and why == "inactive", why)
check("rol: el grupo no se confunde con otro de nombre parecido",
      extauth._role_from(["CN=NCCM-Admins-Viejo,OU=Grupos,DC=x"], {"admin": "NCCM-Admins"}, "") is None)

# ---- ingreso real por el formulario de OpenNCCM con una cuenta de AD (la aplicación en este mismo proceso)
import re  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import requests  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import set_setting  # noqa: E402
from app.main import app  # noqa: E402

for k, v in {"ldap_enabled": "1", "ldap_url": CFG["url"], "ldap_bind_dn": SVC, "ldap_bind_password": "Servicio-123",
             "ldap_base_dn": BASE, "ldap_group_admin": G_ADMIN, "ldap_group_operator": "NCCM-Operadores"}.items():
    set_setting(k, v)
extauth._ldap_server = lambda cfg: server  # el directorio simulado
srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8011, log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(50):
    if srv.started:
        break
    time.sleep(0.2)
W = "http://127.0.0.1:8011"


def login(user, pw):
    s = requests.Session()
    r = s.post(W + "/login", data={"username": user, "password": pw})
    return s, r, re.findall(r'<div class="flash[^"]*">(.*?)</div>', r.text, re.S)


check("pantalla de ingreso: indica que sirven las cuentas de la red", "Active Directory" in requests.get(W + "/login").text)
s, r, fl = login("oscar", "Clave-Oscar-1")
check("ingreso con AD por el formulario: entra al Panel", r.url.rstrip("/") == W and "oscar" in r.text, (r.url, fl))
with SessionLocal() as db:
    o = db.scalars(select(User).where(User.username == "oscar")).first()
    check("ingreso con AD: cuenta creada como operador", o and o.auth_source == "ldap" and o.role == "operator",
          o and (o.auth_source, o.role))
_s, r, fl = login("oscar", "mala")
check("ingreso con AD: contraseña incorrecta", "incorrectos" in " ".join(fl), fl)
_s, r, fl = login("visita", "Clave-Visita-1")
check("ingreso con AD: sin grupo con acceso, se explica", "ningún grupo con acceso" in " ".join(fl), fl)
_s, r, fl = login("local", "Clave-Local-1")
check("una cuenta local no entra con la contraseña del directorio", "incorrectos" in " ".join(fl), fl)
_s, r, fl = login("local", "Local-OpenNCCM-1")
check("la cuenta local sigue entrando con su contraseña de OpenNCCM", r.url.rstrip("/") == W, fl)
srv.should_exit = True

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
