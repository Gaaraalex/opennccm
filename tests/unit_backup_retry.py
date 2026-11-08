"""Respaldo fallido: OpenNCCM comprueba qué pasó y, si tiene arreglo, lo reintenta solo (equipos en línea que quedaban con
error hasta que alguien le daba a «Respaldar» a mano)."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.db import Device, Event, SessionLocal, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
with SessionLocal() as d:
    d.add(Device(name="R1", host="192.0.2.9", device_type="mikrotik_routeros"))
    d.commit()
services.RETRY_WAITS = (0, 0)
services.time.sleep = lambda s: None


def fake(errors):
    """_backup_device que falla con los errores dados y después responde «unchanged»; cuenta los intentos y las alarmas."""
    calls = []

    def f(i, trigger, user, report=True):
        calls.append(report)
        if len(calls) <= len(errors):
            with SessionLocal() as d:
                dev = d.get(Device, i)
                dev.last_status, dev.last_error = "error", errors[len(calls) - 1]
                d.commit()
            if report:
                services.add_event("major", "Respaldo fallido", errors[len(calls) - 1], i)
            return "error"
        return "unchanged"
    return f, calls


def events(cat):
    with SessionLocal() as d:
        return [e.message for e in d.scalars(select(Event).where(Event.category == cat))]


POOL = "No se pudo conectar. QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00"
services._backup_device, calls = fake([POOL])
r = services.backup_device(1, "manual")
check("«QueuePool limit … reached» (falla de OpenNCCM): se reintenta solo y queda bien", r == "unchanged" and len(calls) == 2, (r, calls))
check("sin alarma de «Respaldo fallido», con el aviso de que se reintentó", not events("Respaldo fallido")
      and any("se reintentó solo" in m and "2.º intento: correcto" in m for m in events("Respaldo reintentado")), events("Respaldo reintentado"))

services._port_open = lambda h, p, timeout=6: True
services._backup_device, calls = fake(["Paramiko: 'No existing session' error"])
r = services.backup_device(1, "scheduled")
check("SSH que se cortó con el puerto respondiendo: se reintenta", r == "unchanged" and len(calls) == 2, calls)

services._port_open = lambda h, p, timeout=6: False
services._backup_device, calls = fake(["TCP connection to device failed. timed out"])
r = services.backup_device(1, "scheduled")
check("equipo que no responde en su puerto: no se reintenta y da la alarma con la comprobación",
      r == "error" and len(calls) == 1 and any("no responde en 192.0.2.9:22" in m for m in events("Respaldo fallido")),
      (calls, events("Respaldo fallido")))

services._backup_device, calls = fake(["Authentication to device failed."])
r = services.backup_device(1, "manual")
check("contraseña rechazada: no se reintenta (necesita a una persona)", r == "error" and len(calls) == 1, calls)

services._port_open = lambda h, p, timeout=6: True
services._backup_device, calls = fake([POOL, POOL, POOL])
r = services.backup_device(1, "manual")
check("si sigue fallando: 3 intentos, una sola alarma (la del último) y la comprobación en el error del equipo",
      r == "error" and calls == [False, False, True], calls)
with SessionLocal() as d:
    err = d.get(Device, 1).last_error
check("el error del equipo dice qué se comprobó y cada intento", "1.er intento" in err and "3.º intento" in err, err)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
