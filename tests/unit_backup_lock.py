"""Varios respaldos a la vez (SQLite): guardar las tablas MAC/ARP no puede fallar con «database is locked» porque otro
respaldo escribió en ese momento (visto en el laboratorio: «POST /devices/1/backup» → 500 Internal Server Error)."""
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import macfind, services  # noqa: E402
from app.db import Device, Event, MacSeen, SessionLocal, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
with SessionLocal() as d:
    for n in ("A", "B"):
        d.add(Device(name=n, host="10.9.9." + str(len(n)), device_type="mikrotik_routeros"))
    d.commit()
RAW = {"mac": "0c:2c:84:eb:00:00  Default(0001) 0014  d m            1\n"}


def other_writer(delay):
    time.sleep(delay)
    with SessionLocal() as d:  # otro respaldo que escribe y confirma mientras este guarda sus tablas
        d.add(Event(severity="info", category="x", message="otro respaldo", device_id=2))
        d.commit()


def store_tables(lock_first):
    """Lo que hace el respaldo después de leer el equipo: (bloqueo) y las tablas en un SAVEPOINT que lee y escribe."""
    with SessionLocal() as db:
        if lock_first:
            services._take_write_lock(db, 1)
        t = threading.Thread(target=other_writer, args=(0.3,))
        t.start()
        try:
            with db.begin_nested():
                db.scalars(select(MacSeen).where(MacSeen.device_id == 1)).all()  # lee lo que ya había…
                time.sleep(1.0)            # …(el otro respaldo escribe y confirma aquí)…
                macfind.store(1, RAW, db)  # …y escribe
                db.flush()
            db.commit()
            err = None
        except Exception as e:  # noqa: BLE001
            err = str(e)
        t.join()
        return err


err_old = store_tables(lock_first=False)
check("sin el arreglo se reproduce el fallo («database is locked» al instante)", err_old and "locked" in err_old, err_old)
err_new = store_tables(lock_first=True)
check("empezando la transacción con una escritura, las dos escrituras terminan bien", err_new is None, err_new)
with SessionLocal() as d:
    check("y las dos quedaron guardadas", d.query(Event).filter(Event.message == "otro respaldo").count() == 2)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
