"""Seguridad: cabeceras, política de contraseñas, bloqueo de cuenta, cierre de sesiones, verificación en dos pasos,
obligación de 2FA para administradores, copias de recuperación cifradas y páginas públicas.
Requiere haber ejecutado antes e2e.py (usuario admin / Admin12345). Usa la misma base que la aplicación
(NCM_DATA_DIR) para leer las copias de recuperación."""
import html as htmlmod
import io
import re
import sys
import tarfile
import time

import pyotp
import requests

sys.path.insert(0, ".")
B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(text):
    return " | ".join(htmlmod.unescape(x) for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S))


def login(user, pw):
    s = requests.Session()
    r = s.post(B + "/login", data={"username": user, "password": pw})
    return s, r


def logged_in(s):
    r = s.get(B + "/account", allow_redirects=True)
    return r.url.rstrip("/").endswith("/account")


adm, _ = login("admin", "Admin12345")

# ---------------------------------------------------------------- cabeceras y páginas públicas
r = requests.get(B + "/login")
check("cabecera Content-Security-Policy", "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""))
check("cabeceras X-Frame-Options y nosniff", r.headers.get("x-frame-options") == "DENY"
      and r.headers.get("x-content-type-options") == "nosniff")
check("páginas con datos sin caché", "no-store" in adm.get(B + "/").headers.get("cache-control", ""))
check("privacidad y cookies sin iniciar sesión", "Cookies" in requests.get(B + "/acerca").text)
check("ayuda del certificado sin iniciar sesión", requests.get(B + "/certificado").status_code == 200)
check("login con enlace a privacidad", "/acerca" in r.text)

# ---------------------------------------------------------------- política de contraseñas
for pw, why in (("corta1", "al menos 10"), ("soloLetrasLargas", "letras y números"), ("seguser1234567", "nombre de usuario")):
    r = adm.post(B + "/users", data={"username": "seguser", "role": "viewer", "password": pw, "password2": pw})
    check(f"rechaza contraseña débil ({why})", why in flashes(r.text), flashes(r.text))
r = adm.post(B + "/users", data={"username": "seguser", "role": "viewer", "password": "Prueba-2026xy",
                                 "password2": "Prueba-2026xy"})
check("acepta contraseña que cumple la política", "creado" in flashes(r.text), flashes(r.text))

# ---------------------------------------------------------------- bloqueo de cuenta
for _ in range(5):
    login("seguser", "mala-clave-123")
_, r = login("seguser", "Prueba-2026xy")
check("tras 5 fallos la cuenta se bloquea (aun con la clave correcta)", "bloqueada temporalmente" in flashes(r.text),
      flashes(r.text))
check("evento de cuenta bloqueada", "Cuenta bloqueada" in adm.get(B + "/events").text)
uid = re.search(r"<b>seguser</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text).group(1)
check("la ficha del usuario muestra el bloqueo", "bloqueado hasta" in adm.get(f"{B}/users/{uid}/edit").text)
adm.post(f"{B}/users/{uid}/edit", data={"role": "viewer", "active": "on", "password": "Nueva-2026xy",
                                         "password2": "Nueva-2026xy"})
s1, _ = login("seguser", "Nueva-2026xy")
check("al cambiar la contraseña el administrador la desbloquea", logged_in(s1))

# ---------------------------------------------------------------- cierre de sesiones al cambiar la contraseña
s2, _ = login("seguser", "Nueva-2026xy")
r = s2.post(B + "/account", data={"current": "Nueva-2026xy", "new": "Otra-clave-2026", "new2": "Otra-clave-2026"})
check("cambio de contraseña propio", "Contraseña actualizada" in flashes(r.text), flashes(r.text))
check("la sesión que cambió la contraseña sigue abierta", logged_in(s2))
check("las demás sesiones de ese usuario se cierran", not logged_in(s1))

# ---------------------------------------------------------------- verificación en dos pasos
page = s2.get(B + "/account/2fa/setup").text
secret = re.search(r"user-select:all\">([A-Z2-7]{16,})</code>", page).group(1)
check("configuración 2FA muestra código QR", "<svg" in page and "Código QR" in page)
r = s2.post(B + "/account/2fa/setup", data={"code": "000000"})
check("código incorrecto no activa 2FA", "no coincide" in flashes(r.text))
r = s2.post(B + "/account/2fa/setup", data={"code": pyotp.TOTP(secret).now()})
check("2FA activada", "activada" in flashes(r.text), flashes(r.text))
s3 = requests.Session()
r = s3.post(B + "/login", data={"username": "seguser", "password": "Otra-clave-2026"})
check("con 2FA, tras la contraseña se pide el código", r.url.endswith("/login/2fa"), r.url)
r = s3.post(B + "/login/2fa", data={"code": "123456"})
check("código 2FA incorrecto rechazado", "Código incorrecto" in flashes(r.text))
code = pyotp.TOTP(secret).at(time.time() + 30)  # el código actual ya se usó al activar (no se acepta dos veces)
r = s3.post(B + "/login/2fa", data={"code": code})
check("código 2FA correcto: entra", logged_in(s3), r.url)
s4 = requests.Session()
s4.post(B + "/login", data={"username": "seguser", "password": "Otra-clave-2026"})
r = s4.post(B + "/login/2fa", data={"code": code})
check("un código ya usado no sirve otra vez", "Código incorrecto" in flashes(r.text))
r = adm.post(f"{B}/users/{uid}/2fa-reset")
check("el administrador puede quitar la 2FA", "quitada" in flashes(r.text), flashes(r.text))
check("quitar la 2FA cierra las sesiones de ese usuario", not logged_in(s3))

# ---------------------------------------------------------------- 2FA obligatoria para administradores
r = adm.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on",
                                    "require_2fa_admins": "on", "console_access": "admin", "update_check": "on"})
check("no se puede exigir 2FA sin tenerla uno mismo (evita quedar bloqueado)", "actívela primero" in flashes(r.text),
      flashes(r.text))
adm.post(B + "/users", data={"username": "admin2", "role": "admin", "password": "Segundo-2026xy",
                             "password2": "Segundo-2026xy"})
from app.db import set_setting  # noqa: E402  (misma base que la aplicación)
set_setting("require_2fa_admins", "1")
time.sleep(31)  # la aplicación relee este ajuste como mucho cada 30 s
a2, _ = login("admin2", "Segundo-2026xy")
r = a2.get(B + "/devices")
check("administrador sin 2FA es llevado a activarla", "/account" in r.url and "dos pasos" in r.text, r.url)
s5, _ = login("seguser", "Otra-clave-2026")
check("un lector no está obligado", s5.get(B + "/devices").url.endswith("/devices"))
set_setting("require_2fa_admins", "0")
time.sleep(31)

# ---------------------------------------------------------------- copias de recuperación cifradas
r = adm.post(B + "/system/backup", data={"include_key": "on", "passphrase": "corta", "passphrase2": "corta"})
check("copia con clave exige contraseña de 12+ caracteres", "12 caracteres" in flashes(r.text), flashes(r.text))
r = adm.post(B + "/system/backup", data={"include_key": "on", "passphrase": "Copia-segura-2026",
                                         "passphrase2": "Copia-segura-2026"})
name = re.search(r"Copia creada: (opennccm-\d{8}-\d{6}\.tar\.gz\.enc)", flashes(r.text))
check("copia con clave queda cifrada (.enc)", name is not None, flashes(r.text))
if name:
    blob = adm.get(f"{B}/system/backups/{name.group(1)}").content
    from app.sysbackup import decrypt_archive
    check("el archivo no es legible sin la contraseña", b"secret.key" not in blob and not blob.startswith(b"\x1f\x8b"))
    try:
        decrypt_archive(blob, "otra-contraseña")
        wrong_ok = False
    except RuntimeError:
        wrong_ok = True
    check("contraseña incorrecta no descifra", wrong_ok)
    with tarfile.open(fileobj=io.BytesIO(decrypt_archive(blob, "Copia-segura-2026")), mode="r:gz") as tar:
        check("con la contraseña se recupera base y clave", {"opennccm.db", "secret.key"} <= set(tar.getnames()))
    adm.post(f"{B}/system/backups/{name.group(1)}/delete")

# limpieza
for u in ("seguser", "admin2"):
    m = re.search(rf"<b>{u}</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
    if m:
        adm.post(f"{B}/users/{m.group(1)}/delete")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
