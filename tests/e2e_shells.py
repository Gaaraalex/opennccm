"""pfSense, OPNsense, Alpine Linux y Extreme EXOS simulados: respaldo, inventario (modelo y serie) y la
opción «¿tiene interfaz web?» del formulario de equipos.
Requiere haber ejecutado antes e2e.py (usuario admin). Levanta sus propios equipos simulados (puertos 2231-2234)."""
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


def flashes(text):
    return [htmlmod.unescape(x) for x in re.findall(r'<div class="flash[^"]*">(.*?)</div>', text, re.S)]


def field(page, label):
    m = re.search(rf"<dt>{label}</dt><dd[^>]*>(.*?)</dd>", page, re.S)
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else None


EQUIPOS = [  # (nombre, device_type, opción de fakedev, puerto, modelo, serie, versión, texto del respaldo, web)
    ("FW-pfSense", "pfsense", "--shell=pfsense", 2231, "Netgate 6100", "2134567890", "2.7.2-RELEASE",
     "<hostname>pfSense</hostname>", "1"),
    ("FW-OPNsense", "opnsense", "--shell=opnsense", 2232, "DEC740", "DEC7401234", "24.7.5_1",
     "<hostname>OPNsense</hostname>", "1"),
    ("SRV-Alpine", "alpine_linux", "--shell=alpine", 2233, "Standard PC (Q35 + ICH9, 2009)", "—", "3.20.3",
     "iface eth0 inet dhcp", "0"),
    ("SW-EXOS", "extreme_exos", "--exos", 2234, "EXOS-VM", "SIME6B7-0000", "33.6.1.14",
     'create vlan "Datos"', "0"),
]
procs = [subprocess.Popen([sys.executable, os.path.join(HERE, "fakedev.py"), str(port), opt],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _, _, opt, port, *_ in EQUIPOS]
try:
    time.sleep(3)
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    s.post(B + "/credentials", data={"name": "shells", "username": "admin", "password": "cisco"})
    cred = re.search(r'<option value="(\d+)">shells</option>', s.get(B + "/credentials").text).group(1)

    vendors = s.get(B + "/vendors").text
    for label in ("pfSense", "OPNsense", "Alpine Linux"):
        check(f"{label} aparece en Plataformas", f">{label}<" in vendors)
    form = s.get(B + "/devices/new").text
    check("el alta pregunta si el equipo tiene interfaz web", "El equipo tiene interfaz web" in form
          and 'name="web_enabled" value="0"' in form)
    for label in ("pfSense", "OPNsense", "Alpine Linux"):
        check(f"{label} se puede elegir al crear un equipo", f">{label}</option>" in form)

    for name, dtype, _, port, model, serial, version, text, web in EQUIPOS:
        r = s.post(B + "/devices/new", data={"name": name, "host": "127.0.0.1", "device_type": dtype,
                                             "protocol": "ssh", "port": str(port), "credential_id": cred,
                                             "enabled": "on", "web_enabled": web})
        dev = re.search(r"/devices/(\d+)", r.url).group(1)
        for _ in range(15):  # el alta lanza su primer respaldo en segundo plano: puede estar ocupado
            msgs = flashes(s.post(f"{B}/devices/{dev}/backup").text)
            if not any("ocupado" in m for m in msgs):
                break
            time.sleep(2)
        check(f"{name}: respaldo correcto", any(m.startswith(("Respaldo realizado", "Primer respaldo"))
                                                for m in msgs), msgs)
        page = s.get(f"{B}/devices/{dev}").text
        check(f"{name}: modelo «{model}»", field(page, "Modelo") == model, field(page, "Modelo"))
        check(f"{name}: n.º de serie «{serial}»", field(page, "Nº de serie") == serial, field(page, "Nº de serie"))
        check(f"{name}: versión «{version}»", field(page, "Versión firmware") == version,
              field(page, "Versión firmware"))
        vid = re.search(r'class="tl-row[^"]*" data-id="(\d+)"', page)
        check(f"{name}: guardó la configuración", vid and htmlmod.escape(text, quote=False)
              in s.get(f"{B}/versions/{vid.group(1)}").text.replace("&#34;", '"').replace("&quot;", '"'))
        has_btn = 'aria-label="Interfaz web del equipo (ventana nueva)"' in page
        check(f"{name}: botón de interfaz web {'visible' if web == '1' else 'oculto'}", has_btn == (web == "1"))
        edit = s.get(f"{B}/devices/{dev}/edit").text
        check(f"{name}: el formulario de edición recuerda la opción web",
              (re.search(r'name="web_enabled" value="1" checked', edit) is not None) == (web == "1"))

    lista = s.get(B + "/devices").text
    check("lista: botón web solo en los equipos con web",
          "Interfaz web de FW-pfSense" in lista and "Interfaz web de SW-EXOS" not in lista)
    # sin el campo (API o formularios anteriores) el botón sigue apareciendo, como antes
    r = s.post(B + "/devices/new", data={"name": "SinCampoWeb", "host": "127.0.0.9", "device_type": "cisco_ios",
                                         "protocol": "ssh", "port": "22", "credential_id": cred})
    check("sin el campo web_enabled el botón se muestra", 'aria-label="Interfaz web del equipo (ventana nueva)"' in s.get(r.url).text)
    s.post(r.url + "/delete")
finally:
    for p in procs:
        p.kill()

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
