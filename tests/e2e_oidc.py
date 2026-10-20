"""«Entrar con Microsoft» (OpenID Connect) contra un proveedor simulado: alta con rol por grupo, PKCE, state, nonce,
firma y audiencia del token, cuentas sin grupo, choque con una cuenta local y que la contraseña local no sirva.
Requiere haber ejecutado antes e2e.py (usuario admin)."""
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakeoidc import CLIENT_ID, SECRET, Provider  # noqa: E402

from app.db import SessionLocal, User, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S)


prov = Provider(8765)
ADM_GROUP, OPS_GROUP = "11111111-aaaa-bbbb-cccc-000000000001", "11111111-aaaa-bbbb-cccc-000000000002"
for k, v in {"oidc_enabled": "1", "oidc_discovery": prov.base + "/.well-known/openid-configuration", "oidc_tenant": "",
             "oidc_client_id": CLIENT_ID, "oidc_client_secret": SECRET, "oidc_group_admin": ADM_GROUP,
             "oidc_group_operator": OPS_GROUP, "oidc_group_viewer": "", "oidc_default_role": ""}.items():
    set_setting(k, v)


def sso(user, groups="", mode="", tamper_state=False):
    """Recorre el flujo como un navegador y devuelve (sesión, respuesta final)."""
    requests.get(prov.base + "/set", params={"user": user, "groups": groups, "mode": mode})
    s = requests.Session()
    r = s.get(B + "/login/oidc", allow_redirects=False)
    to_idp = r.headers.get("location", "")
    q = parse_qs(urlparse(to_idp).query)
    back = requests.get(to_idp, allow_redirects=False).headers.get("location", "")
    if tamper_state:
        back = re.sub(r"state=[^&]+", "state=falso", back)
    return s, s.get(back), q


page = requests.get(B + "/login").text
check("pantalla de ingreso: botón «Entrar con Microsoft»", 'href="/login/oidc"' in page and "Entrar con Microsoft" in page)
s, r, q = sso("ana@empresa.com", ADM_GROUP)
check("al proveedor: código con PKCE (S256), state y nonce",
      q.get("code_challenge_method") == ["S256"] and q.get("state") and q.get("nonce") and q.get("client_id") == [CLIENT_ID])
check("la dirección de vuelta es /login/oidc/callback", q.get("redirect_uri", [""])[0].endswith("/login/oidc/callback"), q)
check("ingreso con Microsoft: entra al Panel", r.url.rstrip("/") == B and "ana@empresa.com" in r.text, r.url)
with SessionLocal() as db:
    u = db.scalars(select(User).where(User.username == "ana@empresa.com")).first()
    check("se crea la cuenta con origen Microsoft y rol según su grupo", u and u.auth_source == "oidc" and u.role == "admin",
          u and (u.auth_source, u.role))
check("auditoría registra el ingreso con Microsoft", "login_oidc" in s.get(B + "/audit").text)
_s, r, _q = sso("oper@empresa.com", OPS_GROUP)
with SessionLocal() as db:
    u = db.scalars(select(User).where(User.username == "oper@empresa.com")).first()
    check("otro grupo → rol operador", u and u.role == "operator", u and u.role)
_s, r, _q = sso("nadie@empresa.com", "99999999-0000-0000-0000-000000000000")
check("sin grupo con acceso: no entra y se explica", "ningún grupo con acceso" in " ".join(flashes(r.text)), flashes(r.text))
_s, r, _q = sso("ana@empresa.com", ADM_GROUP, tamper_state=True)
check("state alterado: se rechaza", "caducó o no es válido" in " ".join(flashes(r.text)), flashes(r.text))
for mode, name in [("sig", "token firmado con otra clave"), ("aud", "token para otra aplicación"),
                   ("nonce", "token de otro inicio de sesión (nonce)")]:
    _s, r, _q = sso("ana@empresa.com", ADM_GROUP, mode=mode)
    check(f"{name}: se rechaza", "No se pudo validar" in " ".join(flashes(r.text)) and "/login" in r.url, flashes(r.text))
_s, r, _q = sso("admin", ADM_GROUP)
check("no se adueña de una cuenta local con el mismo nombre", "cuenta local" in " ".join(flashes(r.text)), flashes(r.text))
r = requests.Session().post(B + "/login", data={"username": "ana@empresa.com", "password": "cualquier-cosa-123"})
check("una cuenta de Microsoft no entra con contraseña local", "incorrectos" in " ".join(flashes(r.text)), flashes(r.text))
acc = s.get(B + "/account").text
check("Mi cuenta: la contraseña se cambia en Microsoft", "cuenta de Microsoft 365" in acc and 'name="current"' not in acc)
users = s.get(B + "/users").text
check("Usuarios: se marca el origen de la cuenta", re.search(r"ana@empresa\.com.*?>Microsoft</span>", users, re.S) is not None)
set_setting("oidc_enabled", "0")
check("desactivado: el botón desaparece", 'href="/login/oidc"' not in requests.get(B + "/login").text)
check("desactivado: la ruta vuelve al ingreso", requests.get(B + "/login/oidc", allow_redirects=False).headers.get(
    "location", "").endswith("/login"))
st = s.get(B + "/settings").text
check("Ajustes: sección «Inicio de sesión» con AD y Microsoft", 'id="inicio"' in st and 'name="ldap_url"' in st
      and 'name="oidc_client_id"' in st and "/login/oidc/callback" in st)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
