"""Actualizaciones de firmware en la aplicación real: la pestaña y la sección se muestran, y los permisos se respetan
(el lector no lanza comprobaciones, solo un administrador descarga o cambia la comprobación diaria). No consulta
internet: la lógica de las fuentes se prueba en unit_fwupdates.py. Requiere haber ejecutado antes e2e.py."""
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import Device, FirmwareUpdate, SessionLocal, get_setting, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(user, pw):
    s = requests.Session()
    s.post(B + "/login", data={"username": user, "password": pw})
    return s


adm = login("admin", "Admin12345")
for u, role in (("fw_lector", "viewer"), ("fw_oper", "operator")):
    adm.post(B + "/users", data={"username": u, "role": role, "password": "Firmware-Prueba-26", "password2": "Firmware-Prueba-26"})
lec, ope = login("fw_lector", "Firmware-Prueba-26"), login("fw_oper", "Firmware-Prueba-26")

# un equipo con una actualización ya comprobada (como la dejaría la tarea diaria)
with SessionLocal() as db:
    dev = db.scalars(select(Device).order_by(Device.id)).first()
    dev_id = dev.id
    db.merge(FirmwareUpdate(device_id=dev_id, status="update", source="nvd", current="15.2(4)M7", target="15.2(4)M11",
                            latest="15.2(4)M11", fixed_cves='[["CVE-2016-1347", "HIGH"]]', fixed_for="15.2(4)M11",
                            open_cves=84, notes_url=None))
    db.commit()

page = adm.get(B + "/firmware").text
check("Firmware: pestaña «Actualizaciones disponibles» con el equipo", 'href="#disponibles"' in page and 'id="disponibles"' in page
      and "15.2(4)M11" in page and "Requiere cuenta del fabricante" in page)
check("Firmware: «Comprobar todos ahora» y «Comprobar a diario» para el administrador",
      "Comprobar todos ahora" in page and 'action="/firmware/updates/settings"' in page)
lp = lec.get(B + "/firmware").text
check("el lector ve la pestaña pero no los botones", 'id="disponibles"' in lp and 'action="/firmware/updates/check"' not in lp
      and 'action="/firmware/updates/settings"' not in lp)

d = adm.get(f"{B}/devices/{dev_id}").text
check("ficha: etiqueta «Firmware 15.2(4)M11 disponible» en la cabecera", "Firmware 15.2(4)M11 disponible" in d)
sec = d[d.find('id="fw-update"'):d.find('id="fw-update"') + 6000]
check("ficha: versión instalada → nueva, CVE corregidos y aviso de cuenta del fabricante",
      "15.2(4)M7" in sec and "Corrige 1 vulnerabilidades" in sec and "CVE-2016-1347" in sec
      and "pide una cuenta de soporte" in sec and "84 CVE conocidos siguen" in sec, re.sub(r"<[^>]+>", " ", sec)[:400])
check("ficha: sin descarga pública no hay botón de descarga", "Descargar a la biblioteca" not in sec)

r = lec.post(B + "/firmware/updates/check", allow_redirects=False)
check("el lector no puede lanzar comprobaciones", r.status_code == 403, r.status_code)
r = ope.post(f"{B}/firmware/updates/{dev_id}/download", allow_redirects=False)
check("el operador no puede descargar firmware", r.status_code == 403, r.status_code)
r = ope.post(B + "/firmware/updates/settings", data={}, allow_redirects=False)
check("el operador no puede cambiar la comprobación diaria", r.status_code == 403, r.status_code)
r = adm.post(f"{B}/firmware/updates/{dev_id}/download")
check("sin descarga pública, el administrador recibe un aviso claro", "no tiene una descarga pública" in r.text)
st = lec.get(f"{B}/firmware/updates/status?device_id={dev_id}").json()
check("estado de la comprobación disponible para la página", set(st) >= {"running", "done", "total", "downloading"}, st)

adm.post(B + "/firmware/updates/settings", data={})
check("el administrador desactiva la comprobación diaria", get_setting("fw_update_check", "1") == "0")
adm.post(B + "/firmware/updates/settings", data={"enabled": "on"})
check("y la vuelve a activar", get_setting("fw_update_check", "1") == "1")

with SessionLocal() as db:
    db.delete(db.get(FirmwareUpdate, dev_id))
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
