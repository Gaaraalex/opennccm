"""Modo privilegiado (enable) con contraseña, como un Ubiquiti EdgeSwitch: el driver entra a enable al conectar.
Requiere haber ejecutado antes e2e.py (usuario admin). Levanta sus propios equipos simulados (puertos 2224 y 2225)."""
import html as htmlmod
import os
import re
import subprocess
import sys
import time

import requests

B = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
s = requests.Session()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def cred(name, secret=""):
    s.post(B + "/credentials", data={"name": name, "username": "admin", "password": "cisco", "secret": secret})
    return re.search(rf'<option value="(\d+)">{name}</option>', s.get(B + "/credentials").text).group(1)


def device(name, port, cred_id):
    r = s.post(B + "/devices/new", data={"name": name, "host": "127.0.0.1", "device_type": "ubiquiti_edgeswitch",
                                         "protocol": "ssh", "port": str(port), "credential_id": cred_id,
                                         "enabled": "on"})
    return re.search(r"/devices/(\d+)", r.url).group(1)


def backup(dev_id):
    r = s.post(f"{B}/devices/{dev_id}/backup")
    ok = any("Respaldo realizado" in x or "Primer respaldo" in x
             for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', r.text, re.S))
    return ok, htmlmod.unescape(s.get(f"{B}/devices/{dev_id}").text)


procs = [subprocess.Popen([sys.executable, os.path.join(HERE, "fakedev.py"), port, "--legacy", f"--enable-pass={pw}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
         for port, pw in (("2224", "cisco"), ("2225", "Enable123"))]
try:
    time.sleep(3)
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    sin = cred("edge-sin-enable")

    # 1) enable con la misma contraseña de ingreso y la credencial sin enable -> se reintenta solo
    d1 = device("Edge-misma-clave", 2224, sin)
    ok, page = backup(d1)
    check("enable con la contraseña de ingreso (credencial sin enable)", ok, page[page.find("Último error"):][:300])

    # 2) enable con otra contraseña y sin enable en la credencial -> mensaje claro en español
    d2 = device("Edge-otra-clave", 2225, sin)
    ok, page = backup(d2)
    check("sin contraseña de enable: falla", not ok)
    check("sin contraseña de enable: mensaje claro con el nombre de la credencial",
          "Escriba la contraseña de enable en la credencial «edge-sin-enable»" in page,
          page[page.find("Último error"):][:300])

    # 3) contraseña de enable incorrecta
    mala = cred("edge-enable-mala", "Equivocada1")
    s.post(f"{B}/devices/{d2}/edit", data={"name": "Edge-otra-clave", "host": "127.0.0.1",
                                           "device_type": "ubiquiti_edgeswitch", "protocol": "ssh", "port": "2225",
                                           "credential_id": mala, "enabled": "on"})
    ok, page = backup(d2)
    check("enable incorrecto: mensaje claro", not ok and "no aceptó la contraseña de enable" in page,
          page[page.find("Último error"):][:300])

    # 4) contraseña de enable correcta
    buena = cred("edge-enable-buena", "Enable123")
    s.post(f"{B}/devices/{d2}/edit", data={"name": "Edge-otra-clave", "host": "127.0.0.1",
                                           "device_type": "ubiquiti_edgeswitch", "protocol": "ssh", "port": "2225",
                                           "credential_id": buena, "enabled": "on"})
    ok, page = backup(d2)
    check("enable con la contraseña correcta", ok, page[page.find("Último error"):][:300])

    for d in (d1, d2):
        s.post(f"{B}/devices/{d}/delete")
finally:
    for p in procs:
        p.terminate()

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
