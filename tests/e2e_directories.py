"""Inicio de sesión contra directorios reales (lo prepara tests/directories-test.sh):
Samba AD (LDAPS, StartTLS, grupos anidados, certificado no confiable, bind simple sin cifrar) y Keycloak (OpenID Connect)."""
import html
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import Event, SessionLocal, User, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
BASE = "DC=empresa,DC=local"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(text):
    return " ".join(re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S))


def role_of(username):
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.username == username)).first()
        return (u.role, u.auth_source) if u else None


def login(user, pw):
    s = requests.Session()
    r = s.post(B + "/login", data={"username": user, "password": pw})
    return s, r


LDAP = {"ldap_enabled": "1", "ldap_url": "ldaps://127.0.0.1", "ldap_starttls": "0", "ldap_verify_tls": "0",
        "ldap_bind_dn": "svc-nccm@empresa.local", "ldap_bind_password": "Servicio-Nccm-2026", "ldap_base_dn": BASE,
        "ldap_user_filter": "(&(objectClass=user)(sAMAccountName={username}))",
        "ldap_group_admin": f"CN=NCCM-Admins,CN=Users,{BASE}", "ldap_group_operator": "NCCM-Operadores",
        "ldap_group_viewer": "", "ldap_default_role": ""}
for k, v in LDAP.items():
    set_setting(k, v)

# ---------------------------------------------------------------- Samba AD
adm, _r = login("admin", "Admin12345")
test = adm.post(B + "/settings/ldap-test", data={**{k: v for k, v in LDAP.items() if k != "ldap_enabled"},
                                                 "ldap_verify_tls": "", "test_user": "oscar"}).json()
check("AD real · Probar conexión encuentra al usuario y su rol", test.get("ok") and "operador" in test.get("msg", ""), test)
s, r = login("ana.admin", "Clave-Ana-2026")
check("AD real · LDAPS: ana.admin entra", r.url.rstrip("/") == B and "ana.admin" in r.text, flashes(r.text))
check("AD real · rol administrador por su grupo (DN)", role_of("ana.admin") == ("admin", "ldap"), role_of("ana.admin"))
s, r = login("oscar", "Clave-Oscar-2026")
check("AD real · rol operador por el nombre del grupo", role_of("oscar") == ("operator", "ldap"), role_of("oscar"))
s, r = login("nico", "Clave-Nico-2026")
check("AD real · grupo anidado (Redes ⊂ NCCM-Admins) da administrador", role_of("nico") == ("admin", "ldap"),
      (role_of("nico"), flashes(r.text)))
s, r = login("ana.admin", "mala")
check("AD real · contraseña incorrecta", "incorrectos" in flashes(r.text), flashes(r.text))
s, r = login("visita", "Clave-Visita-2026")
check("AD real · sin grupo con acceso, se explica", "ningún grupo con acceso" in flashes(r.text), flashes(r.text))
s, r = login("ana.admin", "")
check("AD real · contraseña vacía no entra", r.url.rstrip("/") != B)

set_setting("ldap_verify_tls", "1")
s, r = login("ana.admin", "Clave-Ana-2026")
check("AD real · certificado no confiable con verificación: no entra y se avisa",
      "No se pudo comprobar la cuenta en el directorio" in flashes(r.text), flashes(r.text))
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.category == "Error de Active Directory")).first()
    check("AD real · el error queda como alarma para el administrador", ev is not None)
set_setting("ldap_verify_tls", "0")

set_setting("ldap_url", "ldap://127.0.0.1")
set_setting("ldap_starttls", "1")
s, r = login("oscar", "Clave-Oscar-2026")
check("AD real · StartTLS sobre ldap:// funciona", r.url.rstrip("/") == B, flashes(r.text))
set_setting("ldap_starttls", "0")
s, r = login("oscar", "Clave-Oscar-2026")
check("AD real · sin cifrar, el controlador lo rechaza y OpenNCCM lo informa sin fallar",
      r.status_code == 200 and "No se pudo comprobar" in flashes(r.text), (r.status_code, flashes(r.text)))
set_setting("ldap_enabled", "0")

# ---------------------------------------------------------------- Keycloak (OpenID Connect)
for k, v in {"oidc_enabled": "1", "oidc_tenant": "",
             "oidc_discovery": "http://127.0.0.1:8080/realms/empresa/.well-known/openid-configuration",
             "oidc_client_id": "opennccm", "oidc_client_secret": "Secreto-Keycloak-2026",
             "oidc_group_admin": "NCCM-Admins", "oidc_group_operator": "", "oidc_group_viewer": "",
             "oidc_default_role": "", "oidc_button": "Entrar con Keycloak"}.items():
    set_setting(k, v)


def sso(user, pw):
    s = requests.Session()
    to_idp = s.get(B + "/login/oidc", allow_redirects=False).headers.get("location", "")
    kc = requests.Session()
    page = kc.get(to_idp).text
    m = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', page)
    if not m:
        return s, None, page[:300]
    back = kc.post(html.unescape(m.group(1)), data={"username": user, "password": pw, "credentialId": ""},
                   allow_redirects=False).headers.get("location", "")
    return s, s.get(back) if back.startswith(B) else None, back


check("Keycloak · botón en la pantalla de ingreso", "Entrar con Keycloak" in requests.get(B + "/login").text)
s, r, info = sso("carla", "Clave-Carla-2026")
check("Keycloak · flujo completo (PKCE): carla entra", r is not None and r.url.rstrip("/") == B and "carla" in r.text,
      info if r is None else flashes(r.text))
check("Keycloak · rol administrador por el grupo del token", role_of("carla") == ("admin", "oidc"), role_of("carla"))
s, r, info = sso("pablo", "Clave-Pablo-2026")
check("Keycloak · sin grupo con acceso: no entra", r is not None and "ningún grupo con acceso" in flashes(r.text),
      info if r is None else flashes(r.text))
set_setting("oidc_client_secret", "secreto-equivocado")
s, r, info = sso("carla", "Clave-Carla-2026")
check("Keycloak · secreto de cliente equivocado: se rechaza sin fallar",
      r is not None and "No se pudo validar" in flashes(r.text), info if r is None else flashes(r.text))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
