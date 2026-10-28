"""Firmware en un MikroTik real (después de tests/mikrotik_real.py, mismo equipo y base de datos):
  * 7.23.8: actualización sin conexión con el paquete .npk real de 7.25, reinicio y comprobación de la versión;
  * 7.25 y 6.49.23: actualización «en línea» desde MikroTik, solo comprobando y descargando (sin reiniciar)."""
import sys
import time
import urllib.request

import paramiko

sys.path.insert(0, ".")
from app import firmware  # noqa: E402
from app.db import Device, SessionLocal, select  # noqa: E402

PORT, PASSWORD, VER = int(sys.argv[1]), sys.argv[2], sys.argv[3]
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def version():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("127.0.0.1", PORT, "admin", PASSWORD, look_for_keys=False, allow_agent=False, timeout=15)
    _, so, _ = c.exec_command("/system resource print", timeout=30)
    out = so.read().decode(errors="replace")
    c.close()
    for line in out.splitlines():
        if "version:" in line and "build" not in line:
            return line.split("version:")[1].strip().split()[0]
    return ""


with SessionLocal() as db:
    dev_id = db.scalar(select(Device.id).where(Device.name == f"CHR-{VER}"))

if VER.startswith("7.23"):
    target = "7.25"
    path = f"/tmp/routeros-{target}.npk"
    urllib.request.urlretrieve(f"https://download.mikrotik.com/routeros/{target}/routeros-{target}.npk", path)
    with open(path, "rb") as f:
        img = firmware.save_image(f"routeros-{target}.npk", f, "ci", target, "mikrotik_routeros")
    log = firmware.upgrade_device(dev_id, img.id, reload=True)
    print(log)
    check("paquete .npk copiado completo y reinicio enviado", "completo" in log and "Reinicio enviado" in log)
    got = ""
    for _ in range(40):  # el reinicio con instalación tarda 1–2 minutos
        time.sleep(10)
        try:
            got = version()
            if got:
                break
        except Exception as e:  # noqa: BLE001
            print("esperando a RouterOS:", type(e).__name__)
    check(f"RouterOS {VER} → {target}: arranca con la versión nueva", got == target, got)
else:
    channel = "long-term" if VER.startswith("6.") else "stable"
    before = version()
    log = firmware.upgrade_device(dev_id, 0, reload=False, channel=channel)
    print(log)
    check(f"en línea (canal {channel}): consulta a MikroTik y responde (al día o descargado)",
          "última versión del canal" in log or "descargados" in log, log)
    time.sleep(3)
    check("sin «reiniciar», el equipo sigue con su versión", version() == before)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
