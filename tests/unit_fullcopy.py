"""Copias completas «al cambiar» y copias protegidas por cambio de firmware (app/fullcopy.py), para la imagen .fxi de
WatchGuard y el .backup/.rsc de MikroTik:

* «Al cambiar» (por omisión): después de un respaldo con cambios, el primero o con otro firmware, sí; sin cambios, no,
  salvo que no haya ninguna copia (la primera) o la última tenga 30 días (la de seguridad); nunca dos en menos de
  10 minutos (las que llegan en ese rato quedan en una sola, después); «Semanal», «Diario» y «Solo manual», no;
  con una cuenta sin permiso no se insiste en cada respaldo; la corrida de la noche cubre la primera y la de 30 días;
* al cambiar el firmware: la última copia de texto de la versión anterior queda etiquetada y la última copia
  completa de esa versión protegida; las protegidas no cuentan para «Guardar los N últimos» ni se borran solas;
* la migración pasa una sola vez «Semanal» (la de antes por omisión) a «Al cambiar». Sin red."""
import os
import sys
import tempfile
import time
from datetime import timedelta

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"), NCM_FW_CHECK_NEW="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import db as dbm  # noqa: E402
from app import fullcopy, fxi, mtfiles, services  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import (ConfigVersion, Credential, Device, Event, FxiImage, MtBackup, SessionLocal, init_db, now,  # noqa: E402
                    select)
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
with SessionLocal() as db:
    cr = Credential(name="lab", username="adm", password_enc=encrypt("x"), secret_enc=encrypt(""))
    db.add(cr)
    db.flush()
    mt = Device(name="MT", host="10.0.0.9", device_type="mikrotik_routeros", credential_id=cr.id, os_version="7.20.4 (stable)")
    wg = Device(name="WG", host="10.0.0.8", device_type="watchguard_fireware", credential_id=cr.id, fxi_cred_id=cr.id,
                fxi_key_enc=encrypt("Clave12345"), os_version="12.10.4")
    wgoff = Device(name="WG-SIN", host="10.0.0.7", device_type="watchguard_fireware", credential_id=cr.id)
    cis = Device(name="CIS", host="10.0.0.6", device_type="cisco_ios", credential_id=cr.id)
    db.add_all([mt, wg, wgoff, cis])
    db.commit()
    ID = {d.name: d.id for d in db.scalars(select(Device))}


def dev(name):
    with SessionLocal() as db:
        d = db.get(Device, ID[name])
        _ = d.credential
        db.expunge(d)
        return d


def setf(name, **kw):
    with SessionLocal() as db:
        d = db.get(Device, ID[name])
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def add_mt(ver="7.20.4 (stable)", days=0, protected=False):
    with SessionLocal() as db:
        r = MtBackup(device_id=ID["MT"], backup_size=10, rsc_size=5, ros_version=ver, trigger="manual",
                     created_at=now() - timedelta(days=days), protected=protected)
        db.add(r)
        db.commit()
        return r.id


# --- 1) cuándo toca, MikroTik
check("por omisión «Al cambiar»", mtfiles.cfg(dev("MT"))["freq"] == "change" and fxi.cfg(dev("WG"))["freq"] == "change")
check("con cambios: sí", fullcopy.reason(dev("MT"), "changed", False) == "cambio")
check("el primer respaldo: sí", fullcopy.reason(dev("MT"), "first", False) == "cambio")
check("sin cambios y sin ninguna copia: la primera", fullcopy.reason(dev("MT"), "unchanged", False) == "primera")
add_mt(days=1)
check("sin cambios y con una reciente: no", fullcopy.reason(dev("MT"), "unchanged", False) is None)
check("otro firmware sin cambios de texto: sí", fullcopy.reason(dev("MT"), "unchanged", True) == "firmware")
with SessionLocal() as db:
    for r in db.scalars(select(MtBackup)):
        r.created_at = now() - timedelta(days=31)
    db.commit()
check("sin cambios y la última de 31 días: la de seguridad", fullcopy.reason(dev("MT"), "unchanged", False) == "seguridad")
for f in ("weekly", "daily", "manual"):
    setf("MT", mtb_freq=f)
    check(f"«{f}»: no después del respaldo", fullcopy.reason(dev("MT"), "changed", False) is None)
setf("MT", mtb_freq=None)
setf("MT", mtb_perm='{"level": "none", "cred_id": %d, "at": "%s"}' % (dev("MT").credential_id, now().isoformat(timespec="seconds")))
check("cuenta sin permiso: no insiste sin cambios", fullcopy.reason(dev("MT"), "unchanged", False) is None)
check("cuenta sin permiso: con cambios se vuelve a probar", fullcopy.reason(dev("MT"), "changed", False) == "cambio")
setf("MT", mtb_perm=None, protocol="telnet")
check("Telnet: no", fullcopy.reason(dev("MT"), "changed", False) is None)
setf("MT", protocol="ssh", enabled=False)
check("desactivado: no", fullcopy.reason(dev("MT"), "changed", False) is None)
setf("MT", enabled=True)

# --- 2) WatchGuard
check("WatchGuard configurado, con cambios: sí", fullcopy.reason(dev("WG"), "changed", False) == "cambio")
check("WatchGuard sin configurar la .fxi: no", fullcopy.reason(dev("WG-SIN"), "changed", False) is None)
check("otras marcas: no", fullcopy.reason(dev("CIS"), "changed", False) is None)

# --- 3) nunca dos en menos de 10 minutos
calls = []
mtfiles.start = lambda i, trigger="manual", *a, **k: calls.append((i, trigger, time.monotonic())) or True  # noqa: E731
fxi.start = lambda i, trigger="manual", *a, **k: calls.append((i, trigger, time.monotonic())) or True  # noqa: E731
fullcopy.GAP = 1.0
fullcopy.request(ID["MT"], "cambio")
fullcopy.request(ID["MT"], "cambio")
fullcopy.request(ID["MT"], "cambio")
check("ráfaga: una enseguida", len(calls) == 1 and calls[0][1] == "change:cambio", calls)
check("ráfaga: las demás quedan en espera", fullcopy.pending(ID["MT"]))
time.sleep(1.4)
check("ráfaga: una sola más, después del intervalo", len(calls) == 2 and calls[1][2] - calls[0][2] >= 0.9, calls)
check("ráfaga: ya no queda nada en espera", not fullcopy.pending(ID["MT"]))

# --- 4) el respaldo llama a las copias completas, también con otro firmware
seen = []
real_after = fullcopy.after_backup
fullcopy.after_backup = lambda i, r, fw=False, other=False: seen.append((i, r, fw, other))  # noqa: E731
services._backup_device = lambda i, t, u, report=False: "changed"  # noqa: E731
services.backup_device(ID["MT"], "manual", "admin")
check("después de cada respaldo se decide la copia completa", seen == [(ID["MT"], "changed", False, False)], seen)
services.FW_CHANGED.add(ID["MT"])
services._backup_device = lambda i, t, u, report=False: "unchanged"  # noqa: E731
services.backup_device(ID["MT"], "manual", "admin")
check("con otro firmware se avisa a las copias completas", seen[-1] == (ID["MT"], "unchanged", True, False), seen)
fullcopy.after_backup = real_after
calls.clear()
fullcopy._last.clear()
fullcopy.after_backup(ID["WG"], "changed")
check("WatchGuard: después de un respaldo con cambios se crea la .fxi", calls and calls[0][0] == ID["WG"], calls)
calls.clear()
fullcopy._last.clear()
fullcopy.after_backup(ID["MT"], "error")
check("respaldo fallido: no", not calls)

# --- 5) cambio de firmware en un respaldo de verdad (services._store_raw): etiqueta y protección
with SessionLocal() as db:
    db.query(MtBackup).delete()
    db.commit()
old1, old2 = add_mt(days=5), add_mt(days=2)
with SessionLocal() as db:
    db.add(ConfigVersion(device_id=ID["MT"], kind="running", content_enc=encrypt("/system identity\nset name=MT\n"),
                         sha256="x", size=30, trigger="manual", fw_version="7.20.4"))
    db.commit()
INV = """                   uptime: 1d
                  version: 7.25.1 (stable)
               build-time: 2026-10-01 10:00:00
              free-memory: 50.0MiB
             total-memory: 256.0MiB
                      cpu: ARM64
        architecture-name: arm64
               board-name: CCR2216-1G-12XS-2XQ
                 platform: MikroTik
"""
RUN = "# 2026-11-21 18:00:00 by RouterOS 7.25.1\n# model = CCR2216-1G-12XS-2XQ\n/system identity\nset name=MT\n"
services.FW_CHANGED.clear()
with SessionLocal() as db:
    d = db.get(Device, ID["MT"])
    res = services._store_raw(db, d, get_profile("mikrotik_routeros"), {"running": RUN, "inventory": INV}, "manual", "admin", True)
    db.commit()
with SessionLocal() as db:
    d = db.get(Device, ID["MT"])
    vers = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == ID["MT"]).order_by(ConfigVersion.id)).all()
    rows = {r.id: r for r in db.scalars(select(MtBackup))}
    ev = db.scalars(select(Event).where(Event.device_id == ID["MT"], Event.category == "Copias protegidas (firmware)")).all()
    check("firmware nuevo registrado", "7.25.1" in (d.os_version or ""), d.os_version)
    check("se avisa a las copias completas", ID["MT"] in services.FW_CHANGED)
    check("la última copia de texto de la versión anterior queda etiquetada",
          vers[0].label and "Antes de firmware 7.25.1" in vers[0].label, [(v.id, v.label, v.fw_version) for v in vers])
    check("la copia de texto nueva no se etiqueta", len(vers) == 1 or not vers[-1].label, [(v.id, v.label) for v in vers])
    check("la última copia completa de la versión anterior queda protegida", rows[old2].protected and "7.25.1" in rows[old2].protect_note)
    check("la anterior a esa no", not rows[old1].protected)
    check("evento informativo", len(ev) == 1, [e.message for e in ev])

# --- 6) las protegidas no cuentan para «Guardar los N últimos» ni se borran
setf("MT", mtb_keep=2)
for _ in range(4):
    mtfiles._store(ID["MT"], {"backup": {"data": b"\x88\xac\xa1\xb1" + b"\0" * 20, "enc": ""}, "rsc": {"data": b"# x by RouterOS"}},
                   "manual", "admin", "", "7.25.1 (stable)")
with SessionLocal() as db:
    rows = db.scalars(select(MtBackup).where(MtBackup.device_id == ID["MT"])).all()
    check("MikroTik: la protegida sigue + las 2 últimas", len(rows) == 3 and any(r.id == old2 and r.protected for r in rows),
          [(r.id, r.protected) for r in rows])
# .fxi
setf("WG", fxi_keep=2)
p = fxi.FXI_DIR / str(ID["WG"])
p.mkdir(parents=True, exist_ok=True)
with SessionLocal() as db:
    keep_id = None
    for i in range(2):
        img = FxiImage(device_id=ID["WG"], filename=f"img-x{i}.fxi", size=1, sha256="a", key_enc=encrypt("k"),
                       created_at=now() - timedelta(days=9 - i), fw_version="12.10.4", protected=(i == 0))
        db.add(img)
        db.flush()
        keep_id = keep_id or img.id
    db.commit()
for i in range(3):
    t = p / f".r{i}"
    t.write_bytes(b"x")
    fxi._store(ID["WG"], {"path": t, "size": 1, "sha256": "b", "name": "x.fxi"}, "manual", "admin", "Clave12345")
with SessionLocal() as db:
    imgs = db.scalars(select(FxiImage).where(FxiImage.device_id == ID["WG"])).all()
    check(".fxi: la protegida sigue + las 2 últimas", len(imgs) == 3 and any(i.id == keep_id for i in imgs), [(i.id, i.protected) for i in imgs])
    check(".fxi: guarda la versión de Fireware", all(i.fw_version for i in imgs))

# --- 7) WatchGuard: cambio de Fireware protege la última imagen de la versión anterior
with SessionLocal() as db:
    d = db.get(Device, ID["WG"])
    latest = db.scalars(select(FxiImage).where(FxiImage.device_id == ID["WG"]).order_by(FxiImage.id.desc())).first()
    got = fullcopy.on_firmware_change(db, d, "12.10.4", "12.11.1")
    db.commit()
    check(".fxi: la última de la versión anterior queda protegida", got["files"] == latest.id and db.get(FxiImage, latest.id).protected)

# --- 8) la corrida de la noche con «Al cambiar»: solo la primera y la de 30 días
setf("MT", mtb_last_at=now() - timedelta(days=3))
check("noche, «Al cambiar» reciente: no", ID["MT"] not in mtfiles.due())
setf("MT", mtb_last_at=now() - timedelta(days=31))
check("noche, «Al cambiar» de 31 días: sí", ID["MT"] in mtfiles.due())

# --- 9) migración: «Semanal» pasa una sola vez a «Al cambiar»
setf("MT", mtb_freq="weekly")
setf("WG", fxi_freq="weekly")
dbm.set_setting("fullcopy_freq_migrated", "0")
dbm._migrate_fullcopy_freq()
check("migración: Semanal → Al cambiar", dev("MT").mtb_freq == "change" and dev("WG").fxi_freq == "change")
setf("MT", mtb_freq="weekly")
dbm._migrate_fullcopy_freq()
check("migración: una sola vez (después se respeta Semanal)", dev("MT").mtb_freq == "weekly")

print("\nTODO OK" if not fails else f"\n{len(fails)} FALLOS: {fails}")
sys.exit(1 if fails else 0)
