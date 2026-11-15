"""Puertas del núcleo para «Recolectores por sede» (app/prohooks.py y services.store_remote): un equipo de una sede no
se conecta desde aquí (el módulo devuelve "queued"); lo que lee el recolector se guarda igual que un respaldo de aquí
(primera versión, sin cambios, cambio con su aviso); un error del recolector deja el equipo en error con su aviso; y la
reversión automática no se intenta desde aquí. Sin módulo, todo sigue como siempre."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update({"NCM_DATA_DIR": tmp, "NCM_KEY_FILE": os.path.join(tmp, "secret.key"), "NCM_USO_URL": "http://127.0.0.1:9/x"})
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import prohooks, services  # noqa: E402
from app.db import ConfigVersion, Device, Event, SessionLocal, init_db, select  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


with SessionLocal() as db:
    d = Device(name="SW-SEDE-1", host="10.99.0.1", port=1, device_type="cisco_ios", auto_rollback=True)
    db.add(d)
    db.commit()
    did = d.id

CONF = "version 15.2\nhostname SW-SEDE-1\ninterface Gi0/1\n description uplink\nend\n"
calls = []
prohooks.remote_backup = lambda i, t, u: (calls.append((i, t, u)) or "queued") if i == did else None
prohooks.remote_site = lambda i: "Sucursal Norte" if i == did else None
check("equipo de una sede: no se conecta desde aquí, lo pide al recolector", services.backup_device(did, "manual", "ana") == "queued"
      and calls == [(did, "manual", "ana")])
r = services.store_remote(did, {"running": CONF, "inventory": None}, "scheduled")
with SessionLocal() as db:
    vs = list(db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == did)))
    st = db.get(Device, did).last_status
check("lo que lee el recolector se guarda como un respaldo de aquí: primera versión", r == "first" and len(vs) == 1 and st == "ok", (r, len(vs), st))
check("…igual otra vez: sin cambios", services.store_remote(did, {"running": CONF}, "scheduled") == "unchanged")
r = services.store_remote(did, {"running": CONF.replace("uplink", "uplink-nuevo")}, "syslog")
with SessionLocal() as db:
    ev = [(e.category, e.message) for e in db.scalars(select(Event).where(Event.device_id == did))]
check("un cambio: versión nueva y su aviso", r == "changed" and any(c == "Cambio de configuración" for c, _m in ev), ev)
check("…sin reversión automática desde aquí (avisa que se revise a mano)",
      any(c == "Reversión automática no disponible" and "Sucursal Norte" in m for c, m in ev), ev)
r = services.store_remote(did, {"error": "No responde por SSH (tiempo agotado a los 30 s)"}, "scheduled")
with SessionLocal() as db:
    d = db.get(Device, did)
    ev = [e.message for e in db.scalars(select(Event).where(Event.device_id == did, Event.category == "Respaldo fallido"))]
check("el recolector no pudo leer el equipo: queda en error con el motivo y su aviso", r == "error" and d.last_status == "error"
      and "No responde por SSH" in (d.last_error or "") and ev, ev)
check("lo que no es texto no se guarda", services.store_remote(did, {"running": 123}) == "error")
prohooks.remote_backup = lambda i, t, u: (_ for _ in ()).throw(RuntimeError("módulo roto"))
prohooks.remote_site = None
check("si el módulo falla, el respaldo sigue desde aquí (y falla como siempre: no hay equipo)", services.backup_device(did, "manual") == "error")
prohooks.remote_backup = None
check("sin módulo: como siempre", services.backup_device(did, "manual") == "error" and prohooks.site_of(did) is None)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
