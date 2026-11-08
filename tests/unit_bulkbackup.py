"""«Respaldar todos» con su progreso (app/bulkbackup.py): de a N a la vez, el paso de cada equipo, el motivo de los
errores en palabras, uno solo a la vez («Ya hay un respaldo en curso»), cancelar y reintentar los que fallaron."""
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import bulkbackup, services  # noqa: E402
from app.db import Device, Event, SessionLocal, init_db, now, select, set_setting  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
with SessionLocal() as d:
    for i in range(10):
        d.add(Device(name=f"R{i:02d}", host=f"192.0.2.{i + 1}", device_type="cisco_ios"))
    d.commit()
set_setting("backup_concurrency", "3")
gate = threading.Event()
running, peak = [0], [0]
ERR = {"R03": "Authentication to device failed.", "R07": "TCP connection to device failed.\ntimed out"}


def fake(i, trigger="manual", user=None):
    running[0] += 1
    peak[0] = max(peak[0], running[0])
    services._step("connect")
    gate.wait(5)
    services._step("read")
    with SessionLocal() as d:
        dev = d.get(Device, i)
        bad = ERR.get(dev.name)
        dev.last_status, dev.last_error = ("error", bad) if bad else ("ok", None)
        if not bad:
            dev.last_backup_at = now()
        d.commit()
    running[0] -= 1
    return "error" if bad else ("changed" if dev.name in ("R01", "R05") else "unchanged")


services.backup_device = fake
ok, j = bulkbackup.start(list(range(1, 11)), "admin")
time.sleep(0.4)
s = bulkbackup.state()
check("arranca con todos en cola y 3 a la vez (ajuste backup_concurrency)", ok and s["n"]["run"] == 3 and s["n"]["q"] == 7 and s["conc"] == 3, s["n"])
check("el equipo en curso dice su paso («Conectando…»)", all(d["step"] == "Conectando…" for d in s["devs"] if d["st"] == "run"))
ok2, _j = bulkbackup.start([1, 2], "otro")
check("un segundo «Respaldar todos» mientras corre: no arranca (Ya hay un respaldo en curso)", ok2 is False)
gate.set()
for _ in range(100):
    if bulkbackup.state()["ended"]:
        break
    time.sleep(0.1)
s = bulkbackup.state()
check("termina: 8 correctos y 2 con error, nunca más de 3 a la vez", s["n"]["ok"] == 8 and s["n"]["err"] == 2 and peak[0] <= 3, (s["n"], peak[0]))
why = {d["name"]: d["why"] for d in s["devs"] if d["st"] == "err"}
check("el motivo en palabras", why == {"R03": "Usuario o contraseña rechazados", "R07": "No responde por SSH (tiempo agotado a los 20 s)"}, why)
check("cuenta las configuraciones que cambiaron", s["changed"] == 2 and s["pct"] == 100, (s["changed"], s["pct"]))
with SessionLocal() as d:
    ev = [e.message for e in d.scalars(select(Event).where(Event.category == "Respaldar todos"))]
check("aviso en la campana con el resumen", any("Respaldo terminado: 8 correctos y 2 con error" in m for m in ev), ev)

# reintentar solo los que fallaron
ERR.clear()
gate.set()
ok3, j3 = bulkbackup.start([d["id"] for d in s["devs"] if d["st"] == "err"], "admin", "retry")
for _ in range(100):
    if bulkbackup.state()["ended"]:
        break
    time.sleep(0.1)
s = bulkbackup.state()
check("«Reintentar los 2» respalda solo esos y quedan bien", ok3 and s["total"] == 2 and s["n"]["ok"] == 2, s["n"])

# cancelar: los que están en cola no se lanzan, los que están en curso terminan
gate.clear()
ok4, _j = bulkbackup.start(list(range(1, 11)), "admin")
time.sleep(0.3)
with bulkbackup._lock:
    bulkbackup._job["cancel"] = True
    for d in bulkbackup._job["devs"]:
        if d["st"] == "q":
            d["st"] = "cancel"
gate.set()
for _ in range(100):
    if bulkbackup.state()["ended"]:
        break
    time.sleep(0.1)
s = bulkbackup.state()
check("cancelar: terminan los 3 que estaban en curso y los 7 en cola no se lanzan", s["n"]["ok"] == 3 and s["n"]["cancel"] == 7, s["n"])
check("frases en singular y plural", bulkbackup.summary(1, 1) == "Respaldo terminado: 1 correcto y 1 con error"
      and bulkbackup.summary(25, 0) == "Respaldo terminado: 25 correctos" and bulkbackup.dur(108) == "1 min 48 s")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
