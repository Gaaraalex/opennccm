"""Respaldo de un equipo antiguo que solo ofrece ssh-rsa y kex SHA-1 (simulado en el puerto 2223 con --legacy).
Requiere haber ejecutado antes e2e.py (usuario admin y la credencial 1)."""
import re
import sys

import requests

B = "http://127.0.0.1:8000"
s = requests.Session()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
r = s.post(B + "/devices/new", data={"name": "EdgeSW-antiguo", "host": "127.0.0.1", "device_type": "cisco_ios",
                                     "protocol": "ssh", "port": "2223", "credential_id": "1", "enabled": "on"})
m = re.search(r"/devices/(\d+)", r.url)
check("crear equipo antiguo", m is not None, r.url)
if m:
    r = s.post(f"{B}/devices/{m.group(1)}/backup")
    f = re.findall(r'<div class="flash[^"]*">(.*?)</div>', r.text, re.S)
    check("respaldo de equipo con ssh-rsa / kex SHA-1", any("Respaldo realizado" in x or "Primer respaldo" in x for x in f), f)
    s.post(f"{B}/devices/{m.group(1)}/delete")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
