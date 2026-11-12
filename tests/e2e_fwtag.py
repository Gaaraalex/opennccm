"""Versión de firmware en cada copia de seguridad (app/fwtag.py), con el Cisco simulado (tests/fakedev.py, puerto 2222):
el respaldo la guarda con la copia; la línea de tiempo la muestra (azul la de hoy, ámbar otra, «sin registrar»), con un
separador al cambiar; el inventario, la ventana de la versión (aviso y nombre del archivo) y la comparación (aviso
«Firmware distinto» y filas «por el firmware»). También la lectura desde la configuración."""
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sqlalchemy import select  # noqa: E402

from app import fwtag  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# --- lectura desde la configuración
check("EdgeSwitch: «! System Version»", fwtag.config_fw("!Current Configuration:\n! System Version: v1.11.1\n") == "1.11.1")
check("RouterOS: la cabecera del export", fwtag.config_fw("# 2026-11-11 10:00:02 by RouterOS 7.20.4\n# software id = X\n") == "7.20.4")
check("Cisco IOS: «version 15.2»", fwtag.config_fw("!\nversion 15.2\nhostname R1\n") == "15.2")
check("Junos: «version 22.4R2;»", fwtag.config_fw("## Last commit\nversion 22.4R2;\nsystem {\n") == "22.4R2")
check("FortiGate: «#config-version»", fwtag.config_fw("#config-version=FGT60F-7.2.8-FW-build1639-240313:opmode=0\n") == "7.2.8")
check("sin versión: None", fwtag.config_fw("interface Gi0/1\n shutdown\n") is None)
check("nombre del archivo: «EdgeSwitch-1.11.1»", fwtag.file_tag("Ubiquiti EdgeSwitch", "Ubiquiti", "v1.11.1") == "EdgeSwitch-1.11.1")

# --- el respaldo la guarda
with SessionLocal() as db:
    cred = Credential(name="fw-cred", username="admin", password_enc=encrypt("cisco"), secret_enc=encrypt("cisco"))
    db.add(cred)
    db.flush()
    d = Device(name="FW-SW1", host="127.0.0.1", port=2222, device_type="cisco_ios", credential_id=cred.id)
    db.add(d)
    db.commit()
    dev_id = d.id
s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
s.post(f"{B}/devices/{dev_id}/backup")
for _ in range(20):
    with SessionLocal() as db:
        vs = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id, ConfigVersion.kind == "running")).all()
        dev = db.get(Device, dev_id)
        if vs:
            break
    time.sleep(1)
check("el respaldo guarda el firmware con la copia", vs and vs[0].fw_version and fwtag.same(vs[0].fw_version, dev.os_version), vs and (vs[0].fw_version, dev.os_version))
cur = dev.os_version
v1 = vs[0].id

# dos copias más, para ver «otra versión» y «sin registrar»
with SessionLocal() as db:
    base = db.get(ConfigVersion, v1)
    old = ConfigVersion(device_id=dev_id, kind="running", content_enc=encrypt("version 15.0\nboot system flash:old.bin\n!\nhostname FW-SW1\n"),
                        sha256="a" * 64, size=40, added=3, removed=0, trigger="manual", fw_version="15.0(1)M1")
    none = ConfigVersion(device_id=dev_id, kind="running", content_enc=encrypt("hostname FW-SW1\n"), sha256="b" * 64, size=16,
                         added=1, removed=0, trigger="manual", fw_version=None)
    db.add_all([old, none])
    db.commit()
    # orden: la más vieja primero (ids menores): sin registrar → 15.0 → actual
    ids = sorted([v1, old.id, none.id])
    for vid, fw in zip(ids, [None, "15.0(1)M1", cur]):
        db.get(ConfigVersion, vid).fw_version = fw
    db.commit()
    contents = {vid: db.get(ConfigVersion, vid).content_enc for vid in ids}
    # el contenido de cada una acorde: la de 15.0 con su arranque viejo, la actual con la del respaldo
    db.get(ConfigVersion, ids[1]).content_enc = encrypt("version 15.0\nboot system flash:old.bin\n!\nhostname FW-SW1\n")
    db.get(ConfigVersion, ids[2]).content_enc = base.content_enc
    db.commit()
page = s.get(f"{B}/devices/{dev_id}").text
check("la línea de tiempo muestra el firmware de hoy en azul", '<span class="fwt" title' in page and fwtag.label(cur) in page)
check("…otra versión en ámbar", 'class="fwt old"' in page and "v15.0(1)M1" in page)
check("…y «sin registrar» en las copias viejas", 'class="fwt none"' in page and "sin registrar" in page)
check("separador «Firmware actualizado: v15.0(1)M1 → …»", "Firmware actualizado: v15.0(1)M1 → " + fwtag.label(cur) in page.replace("&gt;", ">"), re.findall(r"Firmware actualizado[^<]*", page)[:2])
check("inventario: el último respaldo con su etiqueta", re.search(r"<dt>Último respaldo</dt><dd[^>]*>[^<]*<span class=\"fwt", page) is not None)

# --- la ventana de la versión
vp = s.get(f"{B}/versions/{ids[1]}").text
check("versión vieja: subtítulo «firmware v15.0(1)M1»", "firmware v15.0(1)M1" in vp)
check("…aviso «Esta copia se hizo con v15.0(1)M1 y el equipo hoy tiene …»", "Esta copia se hizo con v15.0(1)M1" in vp)
vc = s.get(f"{B}/versions/{ids[2]}").text
check("versión actual: sin aviso", "Esta copia se hizo con" not in vc)
check("…y resalta la línea de donde sale el firmware", 'class="fwl"' in vc and "de aquí sale el firmware" in vc, "fwl" in vc)
r = s.get(f"{B}/versions/{ids[2]}?raw=1")
cd = r.headers.get("content-disposition", "")
check("descargar: el firmware en el nombre del archivo", re.search(r"FW-SW1_v\d+_IOS-[\w.()-]+\.txt", cd) is not None, cd)
check("«sin registrar» en el subtítulo de una copia sin dato", "firmware sin registrar" in s.get(f"{B}/versions/{ids[0]}").text)

# --- la comparación
dp = s.get(f"{B}/diff", params={"a": ids[1], "b": ids[2]}).text
check("comparar firmware distinto: aviso", "Firmware distinto: A es v15.0(1)M1" in dp)
check("…las filas que trajo la actualización «por el firmware»", 'dl fwd' in dp and "por el firmware" in dp)
dp2 = s.get(f"{B}/devices/{dev_id}", params={"a": ids[1], "b": ids[2]}).text
check("…también en la comparación de la ficha", "Firmware distinto" in dp2)
# la API de la app la recibe
H = {"Authorization": "Bearer " + requests.post(B + "/api/mobile/login", json={"username": "admin", "password": "Admin12345"}).json()["token"]}
j = requests.get(f"{B}/api/mobile/device/{dev_id}", headers=H).json()
run_fw = [v.get("fw") for v in j["versions"] if v.get("kind") == "running"][:3]
check("app: cada versión lleva su firmware", run_fw == [cur, "15.0(1)M1", None], run_fw)
dj = requests.get(f"{B}/api/mobile/diff", headers=H, params={"a": ids[1], "b": ids[2]}).json()
check("app: la comparación dice el firmware de A y B", dj.get("fw_a") == "15.0(1)M1" and fwtag.same(dj.get("fw_b"), cur), dj)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
