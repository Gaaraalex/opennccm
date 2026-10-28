"""Firmware en remoto, métodos propios de fabricante, con el airOS simulado (tests/fakedev.py --shell=airos):
respaldo previo, copia por SCP a /tmp/fwupdate.bin con tamaño comprobado y «fwupdate -m» solo si se pide reiniciar.
También la solicitud de MikroTik «en línea» y su vista previa. (MikroTik real: tests/mikrotik_fw.py en la CI.)"""
import io
import os
import re
import subprocess
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from app import firmware  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal  # noqa: E402

B, PORT = "http://127.0.0.1:8000", 2239
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakedev.py"), str(PORT), "--shell=airos"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(2)
    with SessionLocal() as db:
        cred = Credential(name="fw-ubnt", username="admin", password_enc=encrypt("cisco"))
        db.add(cred)
        db.flush()
        dev = Device(name="NS-Torre", host="127.0.0.1", port=PORT, device_type="ubiquiti_airos", credential_id=cred.id)
        mt = Device(name="MK-FW", host="192.0.2.90", device_type="mikrotik_routeros", credential_id=cred.id)
        db.add_all([dev, mt])
        db.commit()
        dev_id, mt_id, cred_id = dev.id, mt.id, cred.id
    img = firmware.save_image("XW.v6.3.16.bin", io.BytesIO(b"\x27\x05" + os.urandom(200_000)), "admin", "6.3.16",
                              "ubiquiti_airos")
    check("airOS admite actualización remota (y UniFi AP no: la hace el controlador)",
          firmware.supports_upgrade("ubiquiti_airos") and not firmware.supports_upgrade("ubiquiti_unifi_ap"))
    log = firmware.upgrade_device(dev_id, img.id, reload=False)
    check("sin reiniciar: respaldo previo y copia comprobada, pero no graba", "Respaldo previo: first" in log
          and "/tmp/fwupdate.bin" in log and "200002" in log and "Done, rebooting" not in log, log)
    log = firmware.upgrade_device(dev_id, img.id, reload=True)
    check("reiniciando: «fwupdate -m» graba y reinicia", "Done, rebooting" in log and "Respaldo previo: unchanged" in log, log)
    with SessionLocal() as db:
        n = db.query(ConfigVersion).filter(ConfigVersion.device_id == dev_id).count()
    check("el respaldo previo quedó guardado", n >= 1)

    s = requests.Session()
    s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
    page = s.get(B + "/firmware").text
    check("la página ofrece la actualización en línea de MikroTik", 'action="/firmware/routeros-online"' in page and "MK-FW" in page)
    r = s.post(B + "/firmware/routeros-online", data={"device_ids": str(mt_id), "channel": "long-term", "reload": "on"})
    check("crea la solicitud (requiere aprobación) con la vista previa de los pasos",
          "/system package update set channel=long-term" in r.text and "/system package update install" in r.text, r.url)
    r = s.post(B + "/firmware/routeros-online", data={"device_ids": str(dev_id)}, allow_redirects=True)
    check("en línea solo para MikroTik", "No son MikroTik RouterOS" in r.text)
    cr = re.search(r"/changes/(\d+)", s.get(B + "/changes?status=pending").text)
    with SessionLocal() as db:
        from app.db import ChangeRequest
        db.query(ChangeRequest).filter(ChangeRequest.title.like("RouterOS en línea%")).delete(synchronize_session=False)
        db.query(ConfigVersion).filter(ConfigVersion.device_id == dev_id).delete()
        db.query(Device).filter(Device.id.in_([dev_id, mt_id])).delete(synchronize_session=False)
        db.query(Credential).filter(Credential.id == cred_id).delete()
        db.commit()
    firmware.delete_image(img.id)
finally:
    proc.kill()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
