"""Versión instalada leída del equipo sin respaldar («Comprobar ahora») y comprobación después de actualizar el
firmware: si el equipo quedó en la versión nueva lo dice; si no, guarda el final de su registro en Eventos y en la
solicitud de cambio. Con un equipo simulado (sin red). También: la serie de los Ubiquiti airMAX y la «S/N»."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import fwupdates, fwverify, services  # noqa: E402
from app.db import ChangeRequest, Device, Event, SessionLocal, init_db, select  # noqa: E402
from app.drivers import get_profile, parse_inventory  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
STATE = {"version": "XW.v8.5.4", "down": 0, "cmds": []}


class FakeConn:
    def __enter__(self):
        if STATE["down"] > 0:
            STATE["down"] -= 1
            raise OSError("sin respuesta")
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, cmd, read_timeout=0):
        STATE["cmds"].append(cmd)
        if cmd == "cat /etc/version; cat /etc/board.info":  # el inventario del perfil airOS, en una línea
            return STATE["version"] + "\nboard.name=PowerBeam 5AC\nboard.hwaddr=24A43C112233\n"
        if cmd.startswith("tail"):
            return "\n".join(f"línea {i}" for i in range(100)) + "\nfwupdate: firmware signature check failed"
        return ""


services.connect = lambda dev: FakeConn()
services.backup_device = lambda *a, **k: "ok"          # el respaldo real necesita la red
fwupdates.check_all = lambda ids=None: 0               # el catálogo del fabricante, también

with SessionLocal() as db:
    d = Device(name="SECTORIAL-4", host="192.0.2.4", device_type="ubiquiti_airos", os_version="8.5.4")
    db.add(d)
    cr = ChangeRequest(title="Firmware", body="", device_ids="[]", requested_by="admin")
    db.add(cr)
    db.commit()
    dev_id, cr_id = d.id, cr.id

# ---- «Comprobar ahora»: la versión se lee del equipo, sin respaldar
STATE["version"] = "XW.v8.7.25"
r = fwverify.read_inventory(dev_id)
with SessionLocal() as db:
    d = db.get(Device, dev_id)
check("comprobar: lee la versión del equipo sin respaldar", r["ok"] and d.os_version == "8.7.25" and r["before"] == "8.5.4", r)
check("comprobar: solo comandos de consulta (los del inventario del perfil)", STATE["cmds"] == [get_profile("ubiquiti_airos").inventory], STATE["cmds"])
check("airMAX: la serie es la MAC del equipo (board.hwaddr)", d.serial == "24A43C112233", d.serial)
check("«HW S/N:» se reconoce como número de serie",
      parse_inventory("HW S/N:       F09FC2A1B2C3\n", get_profile("ubiquiti_edgerouter"))["serial"] == "F09FC2A1B2C3")

# ---- después de actualizar: quedó en la versión nueva
with SessionLocal() as db:
    db.get(Device, dev_id).os_version = "8.5.4"
    db.commit()
STATE.update(version="XW.v8.7.25", down=2, cmds=[])  # tarda dos intentos en volver del reinicio
res = fwverify.verify_after_upgrade(dev_id, "8.5.4", "8.7.25", True, cr_id, wait_first=0, every=0, give_up=60)
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc())).first()
    cr = db.get(ChangeRequest, cr_id)
check("actualizado: espera a que el equipo vuelva y dice la versión nueva", res.get("applied") and "quedó en la versión 8.7.25" in ev.message, ev.message)
check("actualizado: queda en la solicitud de cambio", "Verificación: SECTORIAL-4 quedó en la versión 8.7.25" in (cr.result or ""), cr.result)

# ---- después de actualizar: sigue en la anterior → registro del equipo
STATE.update(version="XW.v8.5.4", down=0, cmds=[])
res = fwverify.verify_after_upgrade(dev_id, "8.5.4", "8.7.25", True, cr_id, wait_first=0, every=0, give_up=60)
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc())).first()
    cr = db.get(ChangeRequest, cr_id)
check("sin aplicar: avisa que sigue en la versión anterior", not res.get("applied") and ev.severity == "major" and "sigue en la versión 8.5.4" in ev.message, ev.message)
check("sin aplicar: con el final del registro del equipo (el motivo)", "firmware signature check failed" in ev.message and "$ tail -n 80 /var/log/messages" in ev.message)
check("sin aplicar: el registro también en la solicitud de cambio", "signature check failed" in (cr.result or ""))
check("sin aplicar: solo comandos de consulta", all(c.startswith(("cat ", "tail ")) for c in STATE["cmds"]), STATE["cmds"])

# ---- no vuelve
STATE.update(down=99, cmds=[])
res = fwverify.verify_after_upgrade(dev_id, "8.5.4", "8.7.25", True, cr_id, wait_first=0, every=0, give_up=0)
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc())).first()
check("no vuelve: lo avisa en Eventos", not res.get("ok") and "no volvió a responder" in ev.message, ev.message)

# ---- comando del registro para las familias comunes
for t, cmd in (("mikrotik_routeros", "/log print"), ("cisco_ios", "show logging"), ("huawei", "display logbuffer"), ("extreme_vsp", "show logging")):
    check(f"registro de {t}: {cmd}", (fwverify.log_command(t) or "").startswith(cmd), fwverify.log_command(t))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
