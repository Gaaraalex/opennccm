"""Pruebas de las funciones nuevas: línea base, diff lado a lado, RBAC por grupos, paquetes de cumplimiento,
EOL/EOS, firmware (SCP + MD5 + arranque + reinicio), análisis, informes y recuperación ante desastres.
Requiere haber ejecutado antes e2e.py (crea R1, SW2 y el usuario admin)."""
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time

import random

import requests

NTP = f"10.{random.randint(1, 250)}.{random.randint(1, 250)}.123"

B = "http://127.0.0.1:8000"
s = requests.Session()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def flashes(html):
    return re.findall(r'<div class="flash[^"]*">(.*?)</div>', html, re.S)


def wait_cr(cr, timeout=90):
    for _ in range(timeout):
        time.sleep(1)
        t = s.get(f"{B}/changes/{cr}").text
        st = re.search(r"badge b-(done|failed|partial)\b", t)
        if st:
            return st.group(1), t
    return "timeout", t


s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})

# ---------------------------------------------------------------- fabricantes
v = s.get(B + "/vendors").text
n = len(re.findall(r"<tr><td>", v))
check(f"página de fabricantes lista ≥120 plataformas ({n})", n >= 120)
for k in ["huawei_olt", "dlink_ds", "watchguard_fireware", "netscaler", "ciena_saos", "generic"]:
    check(f"plataforma {k} disponible", f"<code>{k}</code>" in v)
sysp = s.get(B + "/system").text
check("Netmiko conoce todas las plataformas", "no conoce" not in sysp)

# ---------------------------------------------------------------- línea base
html = s.get(B + "/devices/1").text
latest = re.findall(r'<td>(\d+)</td><td class="small">[^<]*</td><td>running</td>', html)[0]
r = s.post(f"{B}/versions/{re.findall(r'<td>(\d+)</td><td class=\"small\">[^<]*</td><td>startup</td>', html)[0]}/baseline")
check("startup no puede ser línea base", "Solo una configuración en ejecución" in r.text)
r = s.post(f"{B}/versions/{latest}/baseline")
check("marcar línea base", "marcada como línea base" in r.text)
check("versión muestra 'Línea base del equipo'", "Línea base del equipo" in r.text)
from netmiko import ConnectHandler  # noqa: E402
c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
c.send_config_set([f"ntp server {NTP}"], cmd_verify=False)
c.disconnect()
s.post(B + "/devices/1/backup")
html = s.get(B + "/devices/1").text
check("desvío de línea base detectado", "difiere de la línea base" in html)
check("evento 'Desviación de línea base'", "Desviación de línea base" in s.get(B + "/events").text)
check("filtro de equipos fuera de baseline", "R1" in s.get(B + "/devices?status=drift").text)
r = s.get(B + "/devices/1/baseline-diff")
check("comparar con baseline (lado a lado)", 'class="sbs"' in r.text and f"ntp server {NTP}" in r.text)
check("fila agregada resaltada", re.search(r'<tr class="a">.*?ntp server ' + re.escape(NTP), r.text, re.S) is not None)
base_id = re.search(r'/versions/(\d+)/rollback"><button class="sm danger">Volver a la línea base', html)
check("botón 'Volver a la línea base'", base_id is not None)
r = s.post(f"{B}/versions/{base_id.group(1)}/rollback")
check("reversión a baseline genera 'no ntp server …'", f"no ntp server {NTP}" in r.text)
cr = re.search(r"/changes/(\d+)", r.url).group(1)
s.post(f"{B}/changes/{cr}/review", data={"decision": "approve"})
st, _ = wait_cr(cr)
check("reversión a baseline ejecutada", st == "done", st)
time.sleep(4)
check("equipo vuelve a coincidir con baseline", "difiere de la línea base" not in s.get(B + "/devices/1").text)

# ---------------------------------------------------------------- paquetes de cumplimiento
comp = s.get(B + "/compliance").text
for p in ["PCI DSS 4.0 (IOS)", "HIPAA §164.312 (IOS)", "SOX ITGC (IOS)", "Cisco IOS · Buenas prácticas", "PCI DSS 4.0 (Junos)"]:
    check(f"paquete '{p}' cargado", p in comp)
s.post(B + "/devices/1/compliance")
dev = s.get(B + "/devices/1").text
check("PCI: detecta Telnet en VTY", "Req 2.2.7 · Sin Telnet en líneas VTY" in dev)
check("HIPAA: detecta falta de AAA", "164.312(a)(1) · Control de acceso con AAA" in dev)
check("gráfica de cumplimiento en SVG", '<svg class="viz"' in s.get(B + "/compliance").text)

# ---------------------------------------------------------------- EOL / EOS
r = s.post(B + "/eol/entries", data={"vendor": "Cisco", "model_pattern": "^CISCO2911", "end_of_sale": "2017-12-09",
                                     "end_of_support": "2022-12-31", "replacement": "ISR4331/K9"})
check("agregar entrada EOL", "Entrada EOL agregada" in r.text)
csvdata = "vendor,model_pattern,end_of_sale,end_of_sw_maintenance,end_of_support,replacement,url\nHuawei,^S5720,2021-01-01,,2026-12-31,S5735,\nX,([,2020-01-01,,,,\n"
r = s.post(B + "/eol/import", files={"file": ("eol.csv", csvdata)})
check("importar CSV EOL (1 ok, 1 error)", "1 entradas EOL importadas" in r.text and "línea 3" in r.text)
e = s.get(B + "/eol").text
check("R1 marcado 'Sin soporte'", re.search(r"R1</a></td><td>CISCO2911/K9</td><td><span class=\"badge b-critical\">Sin soporte", e) is not None)
check("ficha del equipo muestra ciclo de vida", "Sin soporte" in s.get(B + "/devices/1").text)
check("informe eol.csv", "Sin soporte" in s.get(B + "/reports/eol.csv").content.decode("utf-8-sig"))
r = s.post(B + "/eol/cisco-sync")
check("Cisco EoX sin credenciales da error claro", "Configure el Client ID" in r.text)

# ---------------------------------------------------------------- firmware
blob = os.urandom(300_000)
r = s.post(B + "/firmware/upload", data={"version": "15.2(4)M11", "device_type_filter": "cisco_ios"},
           files={"file": ("c2900-test.bin", blob)})
check("cargar imagen de firmware", "c2900-test.bin cargada" in r.text)
img_id = re.search(r'<option value="(\d+)">c2900-test.bin', r.text).group(1)
r = s.post(B + "/firmware/upload", files={"file": ("../malo.bin", b"x")})
check("nombre de archivo con ruta rechazado", "no válido" in r.text)
r = s.post(B + "/firmware/upgrade", data={"image_id": img_id, "device_ids": "1"})
cr = re.search(r"/changes/(\d+)", r.url).group(1)
check("solicitud de firmware pendiente de aprobación", "badge b-pending" in r.text and "SCP c2900-test.bin" in r.text)
s.post(f"{B}/changes/{cr}/review", data={"decision": "approve"})
st, t = wait_cr(cr, 120)
check("actualización de firmware ejecutada", st == "done", re.findall(r"<pre>([\s\S]*?)</pre>", t)[-1:])
check("transferido y MD5 verificado", "transferida=True, MD5 verificado=True" in t)
time.sleep(5)
cfg = s.get(B + "/search", params={"q": "boot system"}).text
check("arranque configurado (boot system flash:c2900-test.bin)", "boot system flash:c2900-test.bin" in cfg)
r = s.post(B + "/firmware/upgrade", data={"image_id": img_id, "device_ids": "1", "reload": "on"})
cr = re.search(r"/changes/(\d+)", r.url).group(1)
s.post(f"{B}/changes/{cr}/review", data={"decision": "approve"})
st, t = wait_cr(cr, 120)
check("segunda vez: archivo ya existía y se verifica sin retransmitir", "ya existía=True, transferida=False, MD5 verificado=True" in t)
check("reinicio enviado", "Reinicio enviado" in t, re.findall(r"<pre>([\s\S]*?)</pre>", t)[-1:])
r = s.post(B + "/firmware/upgrade", data={"image_id": img_id, "device_ids": "2"})
check("imagen incompatible con Huawei rechazada", "no es compatible" in r.text)

# ---------------------------------------------------------------- RBAC por grupos
groups = s.get(B + "/credentials").text
acceso = re.search(r'/devices\?group=(\d+)">Acceso', groups).group(1)
s.post(B + "/users", data={"username": "op_acceso", "role": "operator", "password": "Operador12345",
                           "active": "on", "set_groups": "1", "group_ids": acceso})
check("usuario restringido listado con su grupo", "Acceso" in s.get(B + "/users").text)
o = requests.Session()
o.post(B + "/login", data={"username": "op_acceso", "password": "Operador12345"})
lst = o.get(B + "/devices").text
check("operador restringido ve SW2", "SW2" in lst)
check("operador restringido NO ve R1", ">R1<" not in lst)
check("ficha de R1 prohibida (403)", o.get(B + "/devices/1").status_code == 403)
check("respaldo de R1 prohibido (403)", o.post(B + "/devices/1/backup").status_code == 403)
check("versión de R1 prohibida (403)", o.get(B + "/versions/1").status_code == 403)
check("descarga de R1 prohibida (403)", o.get(B + "/versions/1?raw=1").status_code == 403)
check("búsqueda no muestra R1", ">R1<" not in o.get(B + "/search", params={"q": "hostname"}).text)
check("API solo devuelve SW2", [d["name"] for d in o.get(B + "/api/devices").json()] == ["SW2"])
cl = s.get(B + "/configlets").text
cid = re.search(r'<b>Estado de interfaces \(IOS\)</b>[\s\S]*?/configlets/(\d+)/run', cl).group(1)
check("configlet sobre R1 prohibido (403)", o.post(f"{B}/configlets/{cid}/run", data={"device_ids": "1"}).status_code == 403)
check("solicitudes de R1 ocultas", "VLAN 20" not in o.get(B + "/changes").text)
check("inventario CSV filtrado", "R1" not in o.get(B + "/reports/inventory.csv").content.decode("utf-8-sig"))
check("panel funciona para restringido", o.get(B + "/").status_code == 200)

# ---------------------------------------------------------------- análisis e informes
a = s.get(B + "/analytics").text
check("análisis con ≥7 gráficas SVG", a.count('<svg class="viz"') >= 7, a.count('<svg class="viz"'))
check("panel con gráfica de cambios", '<svg class="viz"' in s.get(B + "/").text)
for k in ["inventory", "changes", "compliance", "conflicts", "baseline", "eol", "vulns", "changes_requests", "audit"]:
    r = s.get(f"{B}/reports/view/{k}")
    check(f"informe en pantalla {k}", r.status_code == 200 and "<th>" in r.text)
r = s.post(B + "/reports/email")
check("envío de informes sin SMTP avisa", "Configure el servidor SMTP" in r.text)
r = s.post(B + "/schedules", data={"name": "Informe semanal", "action": "report_email", "cron": "0 7 * * 1"})
check("tarea de informe por correo", "Informe semanal" in r.text)

# ---------------------------------------------------------------- recuperación ante desastres
r = s.post(B + "/system/backup", data={"include_key": "on"})
name = re.search(r"Copia creada: (opennccm-\d{8}-\d{6}\.tar\.gz)", r.text)
check("crear copia de recuperación", name is not None, flashes(r.text))
d = s.get(f"{B}/system/backups/{name.group(1)}")
tf = tarfile.open(fileobj=io.BytesIO(d.content))
check("copia contiene base, manifiesto y clave", sorted(tf.getnames()) == ["manifest.json", "opennccm.db", "secret.key"])
bad = s.get(B + "/system/backups/..%2Fsecret.key")
check("descarga con nombre inválido rechazada", bad.status_code == 404 or bad.url.endswith("/system"), bad.status_code)
bad = s.get(B + "/system/backups/opennccm.db")
check("descarga de archivo que no es copia rechazada", bad.url.endswith("/system") and b"SQLite" not in bad.content)
r = s.post(B + "/schedules", data={"name": "DR diario", "action": "system_backup", "cron": "30 1 * * *"})
check("tarea de copia diaria", "DR diario" in r.text)
sid = re.search(r'<td>DR diario</td>[\s\S]*?/schedules/(\d+)/run', r.text).group(1)
s.post(f"{B}/schedules/{sid}/run")
time.sleep(3)
check("copia programada ejecutada", "Copia de recuperación" in s.get(B + "/events").text)

# restaurar en un directorio nuevo (otro servidor)
rest = os.path.join(tempfile.gettempdir(), "ncmrestore")
os.makedirs(rest, exist_ok=True)
for f in os.listdir(rest):
    os.remove(os.path.join(rest, f))
arch = os.path.join(rest, name.group(1))
open(arch, "wb").write(d.content)
env = dict(os.environ, NCM_DATA_DIR=rest, NCM_KEY_FILE=os.path.join(rest, "secret.key"))
code = "import sys; sys.path.insert(0,'.'); from app.cli import main; sys.argv=['x','restore',sys.argv[1]]; main()"
out = subprocess.run([sys.executable, "-c", code, arch], env=env, capture_output=True, text=True)
check("restaurar en servidor nuevo (con clave)", "Base restaurada" in out.stdout and "Clave restaurada" in out.stdout,
      out.stdout + out.stderr)
code2 = ("import sys; sys.path.insert(0,'.'); from app.db import SessionLocal, ConfigVersion, Device, select; "
         "from app.crypto import decrypt; "
         "db=SessionLocal(); v=db.scalars(select(ConfigVersion)).first(); print(len(db.scalars(select(Device)).all()), "
         "'hostname R1' in decrypt(v.content_enc))")
out = subprocess.run([sys.executable, "-c", code2], env=env, capture_output=True, text=True)
check("datos restaurados y descifrables", out.stdout.strip() == "2 True", out.stdout + out.stderr)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
