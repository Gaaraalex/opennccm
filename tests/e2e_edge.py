"""Ubiquiti EdgeSwitch (FastPath) simulado: inventario, respaldos sin falsos cambios y reversión completa.
Requiere haber ejecutado antes e2e.py (usuario admin). Levanta su propio equipo simulado (puerto 2226)."""
import html as htmlmod
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import legacy_ssh  # noqa: E402

B = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 2226
s = requests.Session()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(text):
    return [htmlmod.unescape(x) for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S)]


def backup(dev_id):
    return flashes(s.post(f"{B}/devices/{dev_id}/backup").text)


def versions(dev_id):
    page = s.get(f"{B}/devices/{dev_id}").text
    return [int(x) for x in dict.fromkeys(re.findall(r'<li class="[^"]*" data-id="(\d+)"', page))]  # más reciente primero


def wait_cr(cr_id):
    for _ in range(40):
        time.sleep(1)
        t = s.get(f"{B}/changes/{cr_id}").text
        if "b-done" in t or "b-failed" in t or "b-partial" in t:
            return t
    return t


proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakedev.py"), str(PORT), "--edgeswitch", "--legacy",
                         "--enable-pass=cisco"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(3)
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    s.post(B + "/credentials", data={"name": "edge", "username": "admin", "password": "cisco"})
    cred = re.search(r'data-cred="(\d+)" data-name="edge"', s.get(B + "/credentials").text).group(1)
    r = s.post(B + "/devices/new", data={"name": "EdgeSW16", "host": "127.0.0.1", "device_type": "ubiquiti_edgeswitch",
                                         "protocol": "ssh", "port": str(PORT), "credential_id": cred,
                                         "new_group": "Oficina principal", "enabled": "on"})
    dev = re.search(r"/devices/(\d+)", r.url).group(1)

    f = backup(dev)
    check("respaldo del EdgeSwitch (el primero se hace al crearlo)",
          any("Primer respaldo" in x or "sin cambios" in x or "cambios" in x for x in f), f)
    page = htmlmod.unescape(s.get(f"{B}/devices/{dev}").text)
    check("inventario: modelo ES-16-150W", re.search(r"<dd[^>]*>ES-16-150W</dd>", page) is not None)
    check("inventario: serie 7483C2ABCDEF", re.search(r"<dd[^>]*>7483C2ABCDEF</dd>", page) is not None)
    check("inventario: firmware 1.9.3.5263085", "1.9.3.5263085" in page)
    check("grupo creado desde el formulario del equipo", "Oficina principal</a>" in page)

    f = backup(dev)
    check("segundo respaldo sin cambios (se ignora el tiempo encendido)", any("sin cambios" in x for x in f), f)
    v1 = versions(dev)[0]

    # un técnico cambia el switch por su cuenta (VLAN nuevas, puerto, usuario, nombre)
    legacy_ssh.enable()
    from netmiko import ConnectHandler
    c = ConnectHandler(device_type="ubiquiti_edgeswitch", host="127.0.0.1", port=PORT, username="admin",
                       password="cisco", secret="cisco")
    c.send_config_set(["exit", "vlan database", "vlan 20,30", 'vlan name 20 "Camaras"', "exit",
                       "network parms 10.0.0.250 255.255.255.0 10.0.0.1", "configure",
                       'username "tecnico" password 1111 level 1 encrypted', 'snmp-server sysname "SW-Nuevo"',
                       "interface 0/2", "description 'Camara'", "vlan participation include 20", "vlan tagging 20",
                       "vlan pvid 20", "exit", "interface 0/3", "description 'AP'", "shutdown", "exit"])
    c.disconnect()
    f = backup(dev)
    check("respaldo detecta el cambio", any("se detectaron cambios" in x for x in f), f)

    # reversión a la versión anterior
    r = s.post(f"{B}/versions/{v1}/rollback")
    cr = re.search(r"/changes/(\d+)", r.url).group(1)
    body = htmlmod.unescape(r.text)
    check("reversión: «vlan database» fuera de configure", re.search(r"exit\s+vlan database[\s\S]*?configure", body)
          is not None, re.findall(r"<pre[^>]*>([\s\S]*?)</pre>", body)[:1])
    check("reversión: no toca la IP de gestión", "network parms" not in re.findall(r"<pre[^>]*>([\s\S]*?)</pre>", body)[0])
    r = s.post(f"{B}/changes/{cr}/review", data={"decision": "approve"})
    check("el administrador aprueba su propia reversión", "No puede aprobar" not in r.text)
    t = wait_cr(cr)
    check("reversión ejecutada sin errores en el EdgeSwitch", "badge b-done" in t,
          htmlmod.unescape(re.findall(r"<pre[^>]*>([\s\S]*?)</pre>", t)[-1])[-800:] if "<pre" in t else t[-500:])
    time.sleep(3)
    backup(dev)
    raw_v1 = s.get(f"{B}/versions/{v1}?raw=1").text
    raw_now = s.get(f"{B}/versions/{versions(dev)[0]}?raw=1").text
    expected = raw_v1.replace("network parms 10.0.0.223", "network parms 10.0.0.250")  # la IP no se revierte
    check("tras la reversión la configuración vuelve a la versión anterior (salvo la IP de gestión)",
          raw_now == expected, "\n".join(sorted(set(raw_now.splitlines()) ^ set(expected.splitlines()))))

    # cambio que el switch rechaza (VLAN inexistente): debe marcarse como fallido y detenerse en ese comando
    s.post(B + "/configlets/new", data={"name": "VLAN 99 en 0/6", "kind": "config",
                                         "body": "interface 0/6\nvlan participation include 99\nvlan tagging 99\n"
                                                 "exit\ninterface 0/7\ndescription 'NO-DEBE-APLICARSE'\nexit"})
    cl = s.get(B + "/configlets").text
    cid = re.search(r"<b>VLAN 99 en 0/6</b>[\s\S]*?/configlets/(\d+)/run", cl).group(1)
    r = s.post(f"{B}/configlets/{cid}/run", data={"device_ids": dev, "variables": ""})
    crf = re.search(r"/changes/(\d+)", r.url).group(1)
    s.post(f"{B}/changes/{crf}/review", data={"decision": "approve"})
    t = htmlmod.unescape(wait_cr(crf))
    check("«Failed to set VLAN participation» marca la solicitud como fallida", "badge b-failed" in t)
    check("se detiene en el comando rechazado y dice que no se guardó",
          "vlan participation include 99" in t and "NO se guardó" in t, t[t.find("<pre"):][:600])
    time.sleep(4)
    raw_now = s.get(f"{B}/versions/{versions(dev)[0]}?raw=1").text
    check("los comandos posteriores al error no se aplicaron", "NO-DEBE-APLICARSE" not in raw_now)

    # versión incompleta (24 líneas, como un respaldo cortado): no se puede revertir sin confirmar
    from app.crypto import encrypt
    from app.db import ConfigVersion, SessionLocal
    tiny = "\n".join(l for l in raw_now.splitlines() if not l.startswith("!"))[:150] + "\n"
    with SessionLocal() as db:
        cv = ConfigVersion(device_id=int(dev), kind="running", content_enc=encrypt(tiny), sha256="0" * 64,
                           size=len(tiny), added=0, removed=0, trigger="manual", username="prueba")
        db.add(cv)
        db.commit()
        tiny_id = cv.id
    backup(dev)  # la versión actual vuelve a ser la completa
    page = htmlmod.unescape(s.get(f"{B}/versions/{tiny_id}").text)
    check("la versión incompleta muestra el aviso", "Esta versión parece incompleta" in page)
    r = s.post(f"{B}/versions/{tiny_id}/rollback")
    check("sin confirmar no se crea la reversión", "/changes/" not in r.url and "No se creó la reversión" in
          htmlmod.unescape(r.text), r.url)
    r = s.post(f"{B}/versions/{tiny_id}/rollback", data={"confirm": "1"})
    crt = re.search(r"/changes/(\d+)", r.url)
    check("confirmando sí se crea (para revisarla)", crt is not None, r.url)
    if crt:
        s.post(f"{B}/changes/{crt.group(1)}/review", data={"decision": "reject", "comment": "prueba"})

    s.post(f"{B}/devices/{dev}/delete")
finally:
    proc.terminate()

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
