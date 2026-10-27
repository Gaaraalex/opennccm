"""Importar equipos desde Zabbix contra un Zabbix simulado (6.0 y 7.2): conexión con token y con usuario, vista previa
(los que ya existen, sin dirección o desactivados no se marcan), plataforma deducida e importación sin credencial
(equipos desactivados) y con credencial (activos)."""
import html
import os
import re
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app.db import Credential, Device, SessionLocal, get_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


PORTS = {"6.0.40": 18160, "7.2.5": 18172}
procs = [subprocess.Popen([sys.executable, os.path.join(HERE, "fakezabbix.py"), str(port), v],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for v, port in PORTS.items()]
s = requests.Session()
try:
    time.sleep(2)
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    with SessionLocal() as db:
        db.add(Device(name="YaExiste-ZBX", host="192.0.2.99", device_type="cisco_ios"))
        cred = Credential(name="zbx-cred", username="ncm")
        db.add(cred)
        db.commit()
        cred_id = cred.id
    dev_page = s.get(B + "/devices").text
    check("Dispositivos › Agregar ofrece «Importar desde Zabbix»", 'href="/devices/import/zabbix"' in dev_page)
    page = s.get(B + "/devices/import/zabbix").text
    check("formulario de conexión con token y con usuario", 'name="token"' in page and 'name="username"' in page)

    r = s.post(B + "/devices/import/zabbix", data={"url": "http://127.0.0.1:18172/zabbix/", "auth": "token", "token": "mal"})
    check("token incorrecto: explica el error de Zabbix", "Not authorized" in r.text and 'name="key"' not in r.text)
    r = s.post(B + "/devices/import/zabbix", data={"url": "http://127.0.0.1:1/zabbix", "auth": "token", "token": "x"})
    check("Zabbix inaccesible: lo dice, sin error del servidor", r.status_code == 200 and "No se pudo conectar" in r.text)

    for ver, port in PORTS.items():
        url = f"http://127.0.0.1:{port}/zabbix/"
        for mode, data in (("token", {"token": "tok-ok"}), ("usuario", {"username": "Admin", "password": "zabbix"})):
            r = s.post(B + "/devices/import/zabbix", data={"url": url, "auth": "token" if mode == "token" else "user", **data})
            check(f"Zabbix {ver} con {mode}: lista los 7 hosts", "Conectado · Zabbix " + ver in r.text
                  and r.text.count('name="sel"') == 7, re.findall(r'class="banner crit"[^<]*<[^>]*>[^<]*<span[^>]*>([^<]*)', r.text))
    page = html.unescape(r.text)
    check("se recuerda la dirección de Zabbix (no el token ni la contraseña)",
          get_setting("zabbix_url", "") == "http://127.0.0.1:18172/zabbix/" and "tok-ok" not in page
          and 'id="z-pass" name="password" type="password" autocomplete="off">' in page)
    key = re.search(r'name="key" value="([^"]+)"', r.text).group(1)
    rows = re.findall(r"<tr data-g=[\s\S]*?</tr>", r.text)
    byname = {re.search(r"<b>([^<]+)</b>", x).group(1): x for x in rows}
    check("RouterOS por la plantilla «Mikrotik by SNMP»", 'data-v="mikrotik_routeros"' in byname["RT-Borde"])
    check("Cisco IOS por la plantilla", 'data-v="cisco_ios"' in byname["SW-Core"])
    check("FortiGate por el inventario y nombre DNS cuando la interfaz no usa IP",
          'data-v="fortinet"' in byname["FW-DMZ"] and "fw-dmz.example.net" in byname["FW-DMZ"])
    check("sin plataforma reconocible: «Genérico» con aviso de revisar",
          'data-v="generic"' in byname["SRV-Web"] and "revisar" in byname["SRV-Web"])
    check("desactivado en Zabbix: se muestra pero sin marcar",
          "desactivado en Zabbix" in byname["SW-Viejo"] and not re.search(r'name="sel"[^>]*checked', byname["SW-Viejo"]))
    check("sin interfaz: no se puede importar", "sin dirección" in byname["Sensor-Temp"] and "disabled" in byname["Sensor-Temp"])
    check("ya existe en OpenNCCM: no se puede importar", "ya existe" in byname["YaExiste-ZBX"] and "disabled" in byname["YaExiste-ZBX"])
    check("grupos de Zabbix para filtrar", "<option>DMZ</option>" in r.text and "Firewalls, DMZ" in byname["FW-DMZ"])

    sel = [re.search(r'name="sel" value="(\d+)"', byname[n]).group(1) for n in ("RT-Borde", "SW-Core", "FW-DMZ", "SRV-Web")]
    idx = dict(zip(("RT-Borde", "SW-Core", "FW-DMZ", "SRV-Web"), sel))
    data = [("key", key)] + [("sel", i) for i in sel] + [(f"t_{i}", "") for i in sel]
    data = [d for d in data if not (d[0] == f"t_{idx['SRV-Web']}")] + [(f"t_{idx['SRV-Web']}", "linux"), ("credential_id", "")]
    r = s.post(B + "/devices/import/zabbix/create", data=data, allow_redirects=False)
    check("importar vuelve a Dispositivos", r.status_code in (302, 303) and r.headers["location"] == "/devices")
    with SessionLocal() as db:
        devs = {d.name: d for d in db.query(Device).filter(Device.name.in_(["RT-Borde", "SW-Core", "FW-DMZ", "SRV-Web"]))}
    check("crea los 4 marcados", len(devs) == 4, sorted(devs))
    rt = devs.get("RT-Borde")
    check("nombre, IP, descripción (notas), modelo, serie y ubicación",
          rt and rt.host == "192.0.2.11" and rt.notes == "Router de borde, sede central" and rt.model == "CCR2004-1G-12S+2XS"
          and rt.serial == "HFD09ABC123" and rt.location == "CPD" and rt.port == 22 and rt.protocol == "ssh")
    check("plataforma deducida y la elegida a mano", rt and rt.device_type == "mikrotik_routeros" and devs["SRV-Web"].device_type == "linux")
    check("sin credencial: desactivados hasta asignarla", all(not d.enabled and d.credential_id is None for d in devs.values()))
    r = s.post(B + "/devices/import/zabbix/create", data=data, allow_redirects=False)
    check("la lista ya usada caduca (no duplica)", r.headers.get("location") == "/devices/import/zabbix")

    # con credencial: activos
    with SessionLocal() as db:
        for d in db.query(Device).filter(Device.name.in_(list(devs))):
            db.delete(d)
        db.commit()
    r = s.post(B + "/devices/import/zabbix", data={"url": "http://127.0.0.1:18160/zabbix", "auth": "token", "token": "tok-ok"})
    key = re.search(r'name="key" value="([^"]+)"', r.text).group(1)
    i = re.search(r'<b>SW-Core</b>[\s\S]*?', r.text) and re.search(r'name="sel" value="(\d+)"[^>]*aria-label="Importar SW-Core"', r.text).group(1)
    s.post(B + "/devices/import/zabbix/create", data={"key": key, "sel": i, "credential_id": str(cred_id)})
    with SessionLocal() as db:
        d = db.query(Device).filter(Device.name == "SW-Core").first()
        check("con credencial para todos: activos y con la credencial", d and d.enabled and d.credential_id == cred_id)
        db.query(Device).filter(Device.name.in_(["SW-Core", "YaExiste-ZBX"])).delete(synchronize_session=False)
        db.query(Credential).filter(Credential.id == cred_id).delete()
        db.commit()
    r = s.post(B + "/devices/import/zabbix/create", data={"key": "inventada", "sel": "0"}, allow_redirects=False)
    check("una lista desconocida no crea nada", r.headers.get("location") == "/devices/import/zabbix")
finally:
    for p in procs:
        p.kill()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
