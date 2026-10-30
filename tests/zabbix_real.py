"""Importación desde un Zabbix real (lo levanta tests/zabbix-real-test.sh): crea hosts por la API de Zabbix, un token de
API, y los importa en OpenNCCM con token y con usuario, como lo haría un administrador."""
import html
import re
import sys

import requests

sys.path.insert(0, ".")
from app import zabbix  # noqa: E402
from app.db import Device, SessionLocal  # noqa: E402

ZBX, B = sys.argv[1], "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1. preparar Zabbix: grupo, tres hosts y un token de API para Admin
zabbix.TIMEOUT = 300  # en un Zabbix 7 recién creado, enlazar la plantilla de Linux tarda más de 30 s
z = zabbix.Client(ZBX, verify=False)
z.connect(username="Admin", password="zabbix")
print(f"Zabbix {z.version}")
gid = z.call("hostgroup.create", {"name": "NCM-Pruebas"})["groupids"][0]
tpl = z.call("template.get", {"output": ["templateid"], "filter": {"host": "Linux by Zabbix agent"}})
snmp = {"type": 2, "main": 1, "useip": 1, "dns": "", "port": "161", "details": {"version": 2, "community": "public"}}
agent = {"type": 1, "main": 1, "port": "10050"}
z.call("host.create", {"host": "rt-borde", "name": "RT-Borde", "description": "Router de borde, sede central",
                       "groups": [{"groupid": gid}], "interfaces": [{**snmp, "ip": "192.0.2.11"}],
                       "inventory_mode": 0, "inventory": {"os": "RouterOS 7.16", "model": "CCR2004-1G-12S+2XS",
                                                          "serialno_a": "HFD09ABC123", "location": "CPD"}})
z.call("host.create", {"host": "fw-dmz", "name": "FW-DMZ", "groups": [{"groupid": gid}],
                       "interfaces": [{**agent, "useip": 0, "ip": "", "dns": "fw-dmz.example.net"}],
                       "inventory_mode": 0, "inventory": {"os": "FortiOS v7.4.4"}})
z.call("host.create", {"host": "srv-linux", "name": "SRV-Linux", "description": "Servidor de monitorización",
                       "groups": [{"groupid": gid}], "interfaces": [{**agent, "useip": 1, "ip": "192.0.2.13", "dns": ""}],
                       "templates": [{"templateid": t["templateid"]} for t in tpl]})
uid = z.call("user.get", {"output": ["userid"], "filter": {"username": "Admin"}})[0]["userid"]
tid = z.call("token.create", {"name": "opennccm", "userid": uid})["tokenids"][0]
token = z.call("token.generate", [tid])[0]["token"]
z.close()
check("plantilla «Linux by Zabbix agent» presente", bool(tpl))

# 2. importar en OpenNCCM
s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
r = s.post(B + "/devices/import/zabbix", data={"url": ZBX + "/", "auth": "user", "username": "Admin", "password": "zabbix"})
check("con usuario y contraseña: conecta y lista los hosts", "Conectado · Zabbix" in r.text and "RT-Borde" in r.text,
      re.findall(r'role="alert">.*?<span class="grow">([^<]*)', r.text))
r = s.post(B + "/devices/import/zabbix", data={"url": ZBX + "/zabbix.php?action=dashboard.view", "auth": "token",
                                               "token": token})
check("con token de API (y la dirección copiada del navegador)", "Conectado · Zabbix" in r.text and "RT-Borde" in r.text,
      re.findall(r'role="alert">.*?<span class="grow">([^<]*)', r.text))
rows = {re.search(r"<b>([^<]+)</b>", x).group(1): x for x in re.findall(r"<tr data-g=[\s\S]*?</tr>", r.text)}
check("los 3 hosts de prueba y el «Zabbix server» propio", {"RT-Borde", "FW-DMZ", "SRV-Linux", "Zabbix server"} <= set(rows), sorted(rows))
check("«Zabbix server» (127.0.0.1) sin dirección utilizable", "sin dirección" in rows.get("Zabbix server", ""))
check("RouterOS por el inventario", 'data-v="mikrotik_routeros"' in rows.get("RT-Borde", ""))
check("FortiGate por el inventario y nombre DNS", 'data-v="fortinet"' in rows.get("FW-DMZ", "") and "fw-dmz.example.net" in rows.get("FW-DMZ", ""))
check("Linux por la plantilla", 'data-v="linux"' in rows.get("SRV-Linux", ""))
check("grupo de Zabbix", "NCM-Pruebas" in rows.get("RT-Borde", "") and 'name="zg" value="NCM-Pruebas"' in r.text)
key = re.search(r'name="key" value="([^"]+)"', r.text).group(1)
sel = [re.search(r'name="sel" value="(\d+)"', rows[n]).group(1) for n in ("RT-Borde", "FW-DMZ", "SRV-Linux")]
zg = [("zg", html.unescape(g)) for g in re.findall(r'name="zg" value="([^"]+)"', r.text)]  # todos los grupos marcados
r = s.post(B + "/devices/import/zabbix/create", data=[("key", key)] + zg + [("sel", i) for i in sel], allow_redirects=False)
check("importar", r.status_code in (302, 303))
with SessionLocal() as db:
    d = {x.name: x for x in db.query(Device)}
rt = d.get("RT-Borde")
check("RT-Borde con IP, descripción e inventario", rt and rt.host == "192.0.2.11" and rt.notes == "Router de borde, sede central"
      and rt.model == "CCR2004-1G-12S+2XS" and rt.serial == "HFD09ABC123" and rt.location == "CPD"
      and rt.device_type == "mikrotik_routeros")
check("FW-DMZ por nombre DNS", d.get("FW-DMZ") and d["FW-DMZ"].host == "fw-dmz.example.net")
check("sin credencial: desactivados", all(not x.enabled for x in d.values()) and len(d) == 3, sorted(d))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
