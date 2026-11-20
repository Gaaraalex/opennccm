"""Respaldos que fallaron porque el equipo no respondía: cada hora se mira si ya responde (ping o su puerto SSH) y, a
medida que van volviendo, se respaldan solos."""
import os
import sys
import tempfile
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.db import Device, Event, SessionLocal, init_db, now, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


OFF = ("TCP connection to device failed.\n\n1.er intento: TCP connection to device failed.\n"
       "Comprobación: el equipo no responde en {h}:22; no se reintenta ahora.")
init_db()
with SessionLocal() as d:
    def dev(name, host, err, **kw):
        d.add(Device(name=name, host=host, device_type="cisco_ios", last_status="error" if err else "ok",
                     last_error=err.format(h=host) if err else None, **kw))
    dev("VUELVE", "192.0.2.1", OFF)                          # estaba fuera de línea y ya responde
    dev("SIGUE", "192.0.2.2", OFF)                           # sigue sin responder
    dev("TIMEOUT", "192.0.2.3", "Connection to 192.0.2.3 timed out")  # responde solo al puerto SSH (ping filtrado)
    dev("CLAVE", "192.0.2.4", "Authentication failed.")      # necesita a una persona: no entra
    dev("BIEN", "192.0.2.5", None)                           # sin error: no entra
    dev("APAGADO", "192.0.2.6", OFF, enabled=False)          # desactivado: no entra
    dev("MANT", "192.0.2.7", OFF, maintenance_until=now() + timedelta(minutes=30))
    dev("SEDE", "192.0.2.8", OFF, recolector_id=1)           # lo respalda el recolector de su sede
    dev("FALLA", "192.0.2.9", OFF)                           # responde pero el respaldo vuelve a fallar
    # OLT ZTE C300 por telnet: el puerto 23 responde, pero corta la sesión en los tres intentos (error real)
    dev("CORTA", "192.0.2.10", "[Errno 104] Connection reset by peer\n\n1.er intento: [Errno 104] Connection reset by "
        "peer\nComprobación: el puerto 23 responde; se reintenta.\n2.º intento: [Errno 104] Connection reset by peer\n"
        "3.er intento: [Errno 104] Connection reset by peer")
    dev("CORTA-WIN", "192.0.2.11", "[WinError 10054] Se ha forzado la interrupción de una conexión existente por el host remoto")
    d.commit()
    ids = {x.name: x.id for x in d.scalars(select(Device))}

names = {r[1] for r in services.offline_devices()}
check("entran solo los que fallaron por no responder o porque cortaron la sesión (activos, sin mantenimiento, sin recolector)",
      names == {"VUELVE", "SIGUE", "TIMEOUT", "FALLA", "CORTA", "CORTA-WIN"}, names)
with SessionLocal() as d:  # el resto de la prueba sigue con los equipos de antes
    for x in d.scalars(select(Device).where(Device.name.in_(["CORTA", "CORTA-WIN"]))):
        x.last_status, x.last_error = "ok", None
    d.commit()

pinged, probed, backed = [], [], []
services._ping = lambda h: pinged.append(h) or h in ("192.0.2.1", "192.0.2.9")
services._port_open = lambda h, p, t=6: probed.append(h) or h == "192.0.2.3"


def fake_backup(i, trigger, user):
    backed.append((i, trigger))
    with SessionLocal() as d:
        x = d.get(Device, i)
        if x.name == "FALLA":
            return "error"
        x.last_status, x.last_error = "ok", None
        d.commit()
    return "changed"


services.backup_device = fake_backup
r = services.retry_offline()
check("4 fuera de línea, 3 ya responden, 2 respaldados", r == {"offline": 4, "back": 3, "ok": 2}, r)
check("se respaldan los que responden, como respaldo programado",
      sorted(backed) == sorted([(ids["VUELVE"], "scheduled"), (ids["TIMEOUT"], "scheduled"), (ids["FALLA"], "scheduled")]), backed)
check("si el ping no responde se prueba el puerto SSH", "192.0.2.3" in probed and "192.0.2.2" in probed)
check("no se toca a los que no entran", not {"192.0.2.4", "192.0.2.5", "192.0.2.6", "192.0.2.7", "192.0.2.8"}
      & set(pinged + probed), (pinged, probed))
with SessionLocal() as d:
    ev = [(e.device_id, e.severity) for e in d.scalars(select(Event).where(Event.category == "Respaldo al volver a responder"))]
check("un aviso por cada equipo que volvió y se respaldó", sorted(ev) == sorted([(ids["VUELVE"], "info"), (ids["TIMEOUT"], "info")]), ev)

backed.clear()
r = services.retry_offline()
check("a la hora siguiente solo quedan los que siguen sin respaldo", r["offline"] == 2 and [b[0] for b in backed] == [ids["FALLA"]], (r, backed))

with SessionLocal() as d:
    for x in d.scalars(select(Device).where(Device.last_status == "error")):
        x.last_status, x.last_error = "ok", None
    d.commit()
backed.clear()
check("sin equipos fuera de línea no se hace nada", services.retry_offline() == {"offline": 0, "back": 0, "ok": 0} and not backed)

# el ping de verdad
import importlib  # noqa: E402
real = importlib.reload(services)
check("ping de verdad: 127.0.0.1 responde", real._ping("127.0.0.1"))
check("ping de verdad: 192.0.2.250 (TEST-NET) no responde", not real._ping("192.0.2.250"))

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "scheduler.py"), encoding="utf-8").read()
check("la tarea corre cada hora", 'scheduler.add_job(_svc.retry_offline, IntervalTrigger(hours=1), id="offline-retry"' in src)
check("el error del equipo dice que se mira cada hora", "Cada hora se" in open(real.__file__, encoding="utf-8").read())

print("\nTODO OK" if not fails else f"\n{len(fails)} FALLAS: {fails}")
sys.exit(1 if fails else 0)
