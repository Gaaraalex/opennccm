"""Consola SSH web: permisos, verificación de origen, ingreso manual, inicio automático y auditoría.
Requiere haber ejecutado antes e2e.py (usuario admin y el equipo R1 = id 1 en el puerto 2222)."""
import json
import re
import sys
import time

import requests
from websockets.sync.client import connect

B = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/devices/1/console/ws?cols=100&rows=30"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(user, pw):
    s = requests.Session()
    s.post(B + "/login", data={"username": user, "password": pw})
    return s


def cookie(s):
    return "; ".join(f"{k}={v}" for k, v in s.cookies.items())


def read_until(ws, pattern, timeout=15):
    buf, end = "", time.time() + timeout
    while time.time() < end:
        try:
            buf += ws.recv(timeout=max(0.1, end - time.time()))
        except TimeoutError:
            break
        except Exception:  # noqa: BLE001 - conexión cerrada
            break
        if re.search(pattern, buf):
            return buf
    return buf


def send(ws, text):
    ws.send(json.dumps({"t": "d", "d": text}))


adm = login("admin", "Admin12345")
check("botón de consola en la lista de equipos", "/devices/1/console" in adm.get(B + "/devices").text)
page = adm.get(B + "/devices/1/console").text
check("página de consola", "xterm.js" in page)
check("la consola se muestra sin el menú (ventana propia)", 'aria-label="Menú principal"' not in page)
lista = adm.get(B + "/devices").text
check("la consola se abre en un diálogo, con opción de ventana aparte redimensionable",
      'data-console="1"' in lista and "resizable=yes" in lista)
emb = adm.get(B + "/devices/1/console?embed=1")
check("consola embebida: solo puede mostrarse dentro de OpenNCCM",
      "frame-ancestors 'self'" in emb.headers.get("content-security-policy", "") and emb.headers.get("x-frame-options") == "SAMEORIGIN"
      and "frame-ancestors 'none'" in adm.get(B + "/devices/1/console").headers.get("content-security-policy", ""))
check("botón de interfaz web en cada equipo", "Interfaz web de R1" in lista and "https://127.0.0.1/" in lista)
form = adm.get(B + "/devices/1/edit").text
data = dict(re.findall(r'name="(name|host|device_type|protocol|port|credential_id|group_id)"[^>]*value="([^"]*)"', form))
data.update({"name": "R1", "host": "127.0.0.1", "device_type": "cisco_ios", "protocol": "ssh", "port": "2222",
             "credential_id": "1", "group_id": "1", "enabled": "on", "web_url": "192.168.1.1:8443"})
adm.post(B + "/devices/1/edit", data=data)
check("la URL web sin esquema se completa con https://", "https://192.168.1.1:8443" in adm.get(B + "/devices/1").text)
data["web_url"] = ""
adm.post(B + "/devices/1/edit", data=data)

# ---- ingreso manual (por defecto no hay inicio automático)
adm.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on", "console_access": "admin",
                                 "update_check": "on"})
with connect(WS, additional_headers={"Cookie": cookie(adm), "Origin": "http://127.0.0.1:8000"}) as ws:
    out = read_until(ws, "Usuario: ")
    check("pide el usuario del equipo", "Usuario:" in out, out)
    send(ws, "admin\r")
    out = read_until(ws, "Contraseña: ")
    check("pide la contraseña (sin mostrarla)", "Contraseña:" in out, out)
    send(ws, "cisco\r")
    out = read_until(ws, r"R1[#>]")
    check("conecta por SSH y muestra el prompt", re.search(r"R1[#>]", out) is not None, out[-300:])
    send(ws, "show version\r")
    out = read_until(ws, "FTX1234ABCD")
    check("ejecuta comandos en el equipo", "Cisco IOS Software" in out, out[-300:])

with connect(WS, additional_headers={"Cookie": cookie(adm), "Origin": "http://127.0.0.1:8000"}) as ws:
    read_until(ws, "Usuario: ")
    send(ws, "admin\r")
    read_until(ws, "Contraseña: ")
    send(ws, "equivocada\r")
    out = read_until(ws, "incorrectos")
    check("contraseña incorrecta: mensaje claro", "incorrectos" in out, out[-200:])

# ---- inicio automático con la credencial del equipo
adm.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on", "console_access": "admin",
                                 "console_autologin": "on", "update_check": "on"})
with connect(WS, additional_headers={"Cookie": cookie(adm), "Origin": "http://127.0.0.1:8000"}) as ws:
    out = read_until(ws, r"R1[#>]")
    check("inicio automático: entra sin pedir usuario", "Usuario:" not in out and re.search(r"R1[#>]", out), out[-300:])

# ---- seguridad: se rechaza explicando el motivo en el terminal (antes solo salía «[Desconectado]») y sin abrir sesión
def refused(headers):
    """(texto recibido, código de cierre) de una consola que el servidor rechaza."""
    out, code = "", None
    try:
        with connect(WS, additional_headers=headers) as ws:
            while True:
                out += ws.recv(timeout=5)
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "rcvd", None), "code", None)
    return out, code


out, code = refused({"Cookie": cookie(adm), "Origin": "https://sitio-malicioso.example"})
check("otro origen no puede abrir la consola", code == 1008 and "Usuario:" not in out and "R1" not in out, (code, out))
check("y se explica: la dirección de la página no coincide con la del servidor",
      "sitio-malicioso.example" in out and "127.0.0.1:8000" in out, out)
out, code = refused({})
check("sin sesión no se puede abrir la consola", code == 1008 and "Usuario:" not in out, (code, out))
check("y se explica: la sesión caducó", "la sesión caducó" in out, out)

# operador: por defecto la consola es solo para administradores
adm.post(B + "/users", data={"username": "opcon", "role": "operator", "password": "Operador2026x",
                             "password2": "Operador2026x"})
op = login("opcon", "Operador2026x")
check("operador no ve el botón de consola (solo administradores)", "/devices/1/console" not in op.get(B + "/devices").text)
check("operador no puede abrir la página de consola", op.get(B + "/devices/1/console").status_code == 403)
out, code = refused({"Cookie": cookie(op), "Origin": "http://127.0.0.1:8000"})
check("operador tampoco por la conexión directa, y se le explica que no tiene permiso",
      code == 1008 and "no tiene permiso" in out and "Usuario:" not in out, (code, out))

audit = adm.get(B + "/audit").text
check("auditoría registra apertura y cierre", "console_open" in audit and "console_close" in audit)
check("auditoría marca el inicio automático", "inicio automático" in audit)

# dejar los ajustes como estaban
adm.post(B + "/settings", data={"require_approval": "on", "allow_self_approval": "on", "console_access": "admin",
                                 "update_check": "on"})
uid = re.search(r"<b>opcon</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
if uid:
    adm.post(f"{B}/users/{uid.group(1)}/delete")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
