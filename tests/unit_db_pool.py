"""Muchos respaldos a la vez no agotan las conexiones a la base: cada uno suelta la suya mientras habla con el equipo por
SSH (en producción: «QueuePool limit of size 5 overflow 10 reached» en equipos que estaban en línea)."""
import os
import sys
import tempfile
import threading

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.config import WORKERS  # noqa: E402
from app.db import Device, SessionLocal, engine, init_db  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
with SessionLocal() as d:
    d.add(Device(name="A", host="192.0.2.1", device_type="mikrotik_routeros"))
    d.commit()

check("el grupo de conexiones de SQLite alcanza para los trabajos a la vez", engine.pool.size() >= WORKERS, engine.pool.size())

with SessionLocal() as db:
    dev = db.get(Device, 1)
    before = engine.pool.checkedout()
    services._release_db(dev)
    check("antes del SSH la sesión devuelve su conexión", engine.pool.checkedout() == before - 1, (before, engine.pool.checkedout()))
    check("y el equipo se sigue leyendo", dev.name == "A")
    dev.name = "B"
    services._release_db(dev)
    check("con cambios sin guardar no se toca la sesión", db.is_modified(dev))
    db.rollback()

# 60 «respaldos» a la vez que retienen el equipo mientras «hablan» con él: ninguno se queda sin conexión.
# Las conexiones se cuentan con los 60 dentro del «SSH» a la vez (barrera), no por el pico mientras arrancan los hilos:
# ese pico dependía de los tiempos de la máquina (en la CI de v2.45.2 dio 10 con el mismo commit que en main pasó).
N = 60
errs = []
in_ssh = threading.Barrier(N + 1)
go = threading.Event()


def job():
    try:
        with SessionLocal() as db:
            dev = db.get(Device, 1)
            services._release_db(dev)          # lo que hace connect()
            in_ssh.wait(timeout=60)            # el SSH: todos a la vez
            go.wait(timeout=60)
            db.get(Device, 1).name             # después vuelve a usar la base
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:120])


ts = [threading.Thread(target=job) for _ in range(N)]
for t in ts:
    t.start()
try:
    in_ssh.wait(timeout=60)
    during = engine.pool.checkedout()
except threading.BrokenBarrierError:
    during = -1
go.set()
for t in ts:
    t.join()
check("60 a la vez sin «QueuePool limit … reached»", not errs, errs[:2])
check("y con los 60 dentro del «SSH» ninguno tiene una conexión tomada", during == 0, during)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
