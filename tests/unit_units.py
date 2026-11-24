"""Intercambio o reemplazo de equipos (app/units.py), con respaldos completos contra equipos simulados. Sin red:

* dos OLT ZTE intercambiadas: serie en cada copia, de dónde viene cada una, aviso en la ficha, vuelta atrás;
* tarjeta de control cambiada: la misma OLT; tarjeta que venía de otra ficha: otro aparato;
* no se revierte a otro aparato: al pedirlo, al aprobarlo, al ejecutarlo ni solo, ni con la serie sin confirmar;
* no se hereda la línea base, los puertos vistos, la licencia ni los permisos; con startup también;
* firmware distinto entre dos aparatos no es una actualización; una actualización hecha desde OpenNCCM sí protege;
* la retención y «Guardar N» no se llevan la última copia del aparato anterior;
* reemplazos detectados antes de guardar la serie: se reparten al actualizar."""
import json
import os
import sys
import tempfile
from datetime import timedelta

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"), NCM_FW_CHECK_NEW="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import fullcopy, fwverify, mtfiles, scheduler, services, units  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import (ChangeRequest, ConfigVersion, Credential, Device, Event, MtBackup, SessionLocal, init_db,  # noqa: E402
                    now, select, set_setting)

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Olt:
    def __init__(self, serial, cards, config, version="V2.1.0"):
        self.serial, self.cards, self.version = serial, dict(cards), version
        self.config = "!\nhostname ZXAN\n" + config + "!\nend\n"

    def answer(self, cmd):
        if cmd == "show running-config":
            return self.config
        if cmd == "show system-group":
            return f"System Description: C320 Version {self.version} Software, Copyright (c) by ZTE Corporation Compiled\n"
        if cmd == "show card":
            return ("Rack Shelf Slot CfgType RealType Port  HardVer SoftVer         Status\n" + "-" * 60 + "\n"
                    + "".join(f"1    1     {n}    GTGH    GTGHG    16    V1.0.0  V2.1.0          INSERVICE\n" for n in self.cards)
                    + "1    1     4    SMXA    SMXA     3     V1.0.0  V2.1.0          INSERVICE\n")
        if cmd.startswith("show card slotno "):
            n = int(cmd.split()[-1])
            s = self.serial if n == 4 else self.cards.get(n)
            return f"Real-Type     : X      Serial-Number : {s}\n" if s else "Real-Type     : X\n"
        return ""


class Ios:
    def __init__(self, serial, host, version="15.2(4)M1"):
        self.serial, self.host, self.version = serial, host, version
        self.extra = ""

    def answer(self, cmd):
        if cmd in ("show running-config", "show startup-config"):
            return f"!\nversion 15.2\nhostname {self.host}\n{self.extra}!\nend\n"
        if cmd.startswith("show version"):
            return (f"Cisco IOS Software, Version {self.version}, RELEASE SOFTWARE\n"
                    + (f"Processor board ID {self.serial}\n" if self.serial else ""))
        return ""


AT = {}      # dirección → el aparato que está ahí
SENT, AUTO = [], []


class Conn:
    def __init__(self, host):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, cmd, read_timeout=0, **kw):
        SENT.append((self.host, cmd))
        return AT[self.host].answer(cmd)

    def clear_buffer(self, backoff=False):
        pass


class Now:
    """Cola de trabajos: lo que se lanza en segundo plano corre aquí mismo; la reversión automática solo se anota."""
    def submit(self, fn, *a, **kw):
        if getattr(fn, "__name__", "") == "_auto_rollback":
            AUTO.append(a[0])
        return None


services.connect = lambda dev, *a, **k: Conn(dev.host)
services.jobs = Now()
services.send_alert = lambda *a, **k: None
STARTED = []
mtfiles.start = lambda i, trigger="manual", *a, **k: STARTED.append((i, trigger)) or True

init_db()
with SessionLocal() as db:
    cr = Credential(name="lab", username="adm", password_enc=encrypt("x"), secret_enc=encrypt(""))
    db.add(cr)
    db.flush()
    for name, host, dtype in (("OLT NORTE", "192.0.2.11", "zte_olt"), ("OLT SUR", "192.0.2.12", "zte_olt"),
                              ("OLT ESTE", "192.0.2.13", "zte_olt"), ("R1", "192.0.2.21", "cisco_ios"),
                              ("R2", "192.0.2.22", "cisco_ios"), ("MT", "192.0.2.31", "mikrotik_routeros")):
        db.add(Device(name=name, host=host, device_type=dtype, credential_id=cr.id, protocol="ssh"))
    db.commit()
    ID = {d.name: d.id for d in db.scalars(select(Device))}


def dev(name):
    with SessionLocal() as db:
        return db.get(Device, ID[name])


def setf(name, **kw):
    with SessionLocal() as db:
        d = db.get(Device, ID[name])
        for k, v in kw.items():
            setattr(d, k, v)
        db.commit()


def vers(name, kind="running"):
    with SessionLocal() as db:
        return list(db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == ID[name], ConfigVersion.kind == kind)
                               .order_by(ConfigVersion.id)))


def events(name, category):
    with SessionLocal() as db:
        return [e.message for e in db.scalars(select(Event).where(Event.device_id == ID[name], Event.category == category)
                                              .order_by(Event.id))]


def backup(name, trigger="manual"):
    return services.backup_device(ID[name], trigger, "admin")


def notice(name, scope=None):
    with SessionLocal() as db:
        return units.notice(db, db.get(Device, ID[name]), scope)


# --- 1) dos OLT intercambiadas
A = Olt("700000000001", {2: "710000000001", 3: "720000000001"}, "interface vlan 10\n  description norte\n!\n")
B = Olt("700000000002", {2: "710000000002", 3: "720000000002"},
        "".join(f"interface gpon-onu_1/2/1:{i}\n  name cliente{i}\n!\n" for i in range(1, 60)))
AT["192.0.2.11"], AT["192.0.2.12"] = A, B
check("primeros respaldos", backup("OLT NORTE") == "first" and backup("OLT SUR") == "first")
check("cada copia guarda su serie", vers("OLT NORTE")[0].serial == A.serial and vers("OLT SUR")[0].serial == B.serial)
check("se guardan las series de las tarjetas", json.loads(dev("OLT NORTE").unit_parts) == {"2": "710000000001", "3": "720000000001", "4": A.serial},
      dev("OLT NORTE").unit_parts)
check("el inventario sigue leyendo la serie de la de control", dev("OLT NORTE").serial == A.serial)
SENT.clear()
backup("OLT NORTE")
check("sin cambios no se releen todas las tarjetas (solo la de control)",
      [c for _h, c in SENT if "slotno" in c] == ["show card slotno 4"], [c for _h, c in SENT if "slotno" in c])

base_a = vers("OLT NORTE")[0].id
old_since = now() - timedelta(days=60)
setf("OLT NORTE", baseline_version_id=base_a, port_seen='{"viejo": null}', port_seen_since=old_since, auto_rollback=True)
with SessionLocal() as db:
    pend = ChangeRequest(title=f"Reversión de OLT NORTE a la versión #{base_a}", body="no interface vlan 99", variables="{}",
                         device_ids=json.dumps([ID["OLT NORTE"]]), requested_by="op", is_rollback=True,
                         rollback_version_id=base_a, status="approved", created_at=now() - timedelta(minutes=5))
    db.add(pend)
    db.commit()
    PEND = pend.id

AT["192.0.2.11"], AT["192.0.2.12"] = B, A  # se intercambian
try:
    units.verify_live(ID["OLT NORTE"])
    live_err = ""
except RuntimeError as e:
    live_err = str(e)
check("sin respaldo todavía: al enviar una reversión se relee la serie y no se envía", B.serial in live_err, live_err)

check("respaldo tras el intercambio", backup("OLT NORTE", "scheduled") == "changed")
d, v = dev("OLT NORTE"), vers("OLT NORTE")
check("la ficha toma la serie nueva", d.serial == B.serial and d.replaced_serial == A.serial and d.moved_from_id == ID["OLT SUR"])
check("la versión nueva es del aparato nuevo", v[-1].serial == B.serial and v[-1].label == units.NEW_LABEL + B.serial)
check("la última del anterior queda protegida y etiquetada", v[0].protected and v[0].label and v[0].serial == A.serial, v[0].label)
m = events("OLT NORTE", "Equipo reemplazado")
check("el aviso dice de dónde viene y que respalde la otra", len(m) == 1 and "viene de «OLT SUR»" in m[0] and "respalde" in m[0], m)
check("sin reversión automática al cambiar de aparato", not AUTO)
check("la línea base no se hereda", d.baseline_version_id is None and not d.baseline_drift and not events("OLT NORTE", "Desviación de línea base"))
check("los puertos vistos no se heredan", "viejo" not in (d.port_seen or "") and (not d.port_seen_since or d.port_seen_since > old_since))
check("no es un cambio de tarjeta", not events("OLT NORTE", "Tarjeta de control cambiada"))
check("ficha: viene de la otra, que falta respaldar", notice("OLT NORTE")["kind"] == "stale", notice("OLT NORTE"))
check("la otra ficha avisa que su aparato ya está en otro lugar", (notice("OLT SUR") or {}).get("kind") == "gone", notice("OLT SUR"))
check("sin acceso a la otra ficha no se muestra su nombre", notice("OLT NORTE", {ID["OLT NORTE"]})["src"] is None)

backup("OLT SUR")
m = events("OLT SUR", "Equipo reemplazado")
check("el segundo respaldo confirma el intercambio", len(m) == 1 and "intercambio con «OLT NORTE»" in m[0], m)
check("las dos fichas dicen «intercambiado»", notice("OLT NORTE")["kind"] == "swap" and notice("OLT SUR")["kind"] == "swap")
check("cada ficha conserva su historial", len(vers("OLT NORTE")) == 2 and len(vers("OLT SUR")) == 2)
check("la OLT SUR también protege la del anterior", vers("OLT SUR")[0].protected and vers("OLT SUR")[0].serial == B.serial)

cmds, notes = services.rollback_commands(ID["OLT NORTE"], base_a)
check("no se calcula una reversión a otro aparato", cmds is None and A.serial in notes[0], notes)
with SessionLocal() as db:
    check("la solicitud de antes ya no se puede aprobar",
          units.change_block(db, db.get(ChangeRequest, PEND), db.get(Device, ID["OLT NORTE"])))
PUSHED = []
real_push = services.push_commands
services.push_commands = lambda i, c, *a, **k: PUSHED.append(i) or "ok"
services.execute_change_request(PEND)
with SessionLocal() as db:
    c = db.get(ChangeRequest, PEND)
    check("ni ejecutar, aunque ya estuviera aprobada", c.status == "failed" and not PUSHED and "cambió de aparato" in c.result, c.result)

v2 = vers("OLT NORTE")[-1].id
B.config = B.config.replace("cliente7\n", "cliente7b\n")
backup("OLT NORTE", "scheduled")
check("con el mismo aparato la reversión automática sigue funcionando", AUTO == [ID["OLT NORTE"]], AUTO)
cmds, notes = services.rollback_commands(ID["OLT NORTE"], v2)
check("con el mismo aparato se puede revertir", bool(cmds) and not notes, notes)
try:
    services._push_rollback(ID["OLT NORTE"], cmds)
except RuntimeError as e:
    check("envío al mismo aparato", False, str(e))
check("la reversión del mismo aparato se envía", PUSHED == [ID["OLT NORTE"]], PUSHED)
PUSHED.clear()
AT["192.0.2.11"] = A
try:
    services._push_rollback(ID["OLT NORTE"], cmds)
except RuntimeError:
    pass
check("si contesta otro aparato no se envía nada", not PUSHED)
services.push_commands = real_push

# vuelven a su lugar
AT["192.0.2.12"] = B
backup("OLT NORTE")
backup("OLT SUR")
check("si vuelve el aparato, vuelve su línea base", dev("OLT NORTE").baseline_version_id == base_a, dev("OLT NORTE").baseline_version_id)
check("y se puede revertir a sus copias", services.rollback_commands(ID["OLT NORTE"], base_a)[0] is not None)
check("pero no a las del que estuvo en medio", services.rollback_commands(ID["OLT NORTE"], v2)[0] is None)

# --- 2) solo la tarjeta de control
C = Olt("700000000003", {2: "710000000003", 3: "720000000003"}, "interface vlan 30\n!\n")
AT["192.0.2.13"] = C
backup("OLT ESTE")
base_c = vers("OLT ESTE")[0].id
setf("OLT ESTE", baseline_version_id=base_c, auto_rollback=True)
AUTO.clear()
C.serial = "700000000033"
C.config = C.config.replace("vlan 30", "vlan 31")
backup("OLT ESTE", "scheduled")
d = dev("OLT ESTE")
check("solo la tarjeta de control: no es otro aparato", not events("OLT ESTE", "Equipo reemplazado") and not d.replaced_at)
check("avisa de la tarjeta", len(events("OLT ESTE", "Tarjeta de control cambiada")) == 1)
check("su historial sigue siendo suyo", d.serial == C.serial and {x.serial for x in vers("OLT ESTE")} == {C.serial} and d.baseline_version_id == base_c)
check("se puede revertir", services.rollback_commands(ID["OLT ESTE"], base_c)[0] is not None)
check("pero no sola en ese respaldo", not AUTO and events("OLT ESTE", "Reversión automática en pausa"))
C.serial = A.serial  # una serie que está en otra ficha
backup("OLT ESTE")
check("una serie que está en otra ficha se trata como otro aparato", len(events("OLT ESTE", "Equipo reemplazado")) == 1)

D4 = Olt("700000000044", {2: "710000000044"}, "interface vlan 44\n!\n", "V2.1.2")
AT["192.0.2.13"] = D4
n_fw = len(events("OLT ESTE", "Copias protegidas (firmware)"))
r = fwverify.read_inventory(ID["OLT ESTE"])
check("OLT: al comprobar la versión también se lee la serie, otro aparato no es una actualización",
      r["ok"] and dev("OLT ESTE").serial == C.serial and len(events("OLT ESTE", "Copias protegidas (firmware)")) == n_fw, r)
last_id = vers("OLT ESTE")[-1].id
setf("OLT ESTE", serial=" " + C.serial + " ")
backup("OLT ESTE")
check("serie guardada con espacios: igual protege la última del anterior",
      next(x for x in vers("OLT ESTE") if x.id == last_id).protected)
N0 = dev("OLT ESTE").serial
setf("OLT ESTE", inventory_raw_enc=encrypt("System Description: C320 Version V2.1.2 Software\nSerial-Number : 00000000\n"))
services.refresh_inventory_from_raw()
check("una serie de relleno no borra la buena al reiniciar", dev("OLT ESTE").serial == N0, dev("OLT ESTE").serial)

# --- 3) serie sin leer
R = Ios("FTX0000AAA1", "R1")
AT["192.0.2.21"] = R
backup("R1")
setf("R1", auto_rollback=True)
AUTO.clear()
R.serial, R.extra = None, "ip domain name otro\n"
backup("R1", "scheduled")
check("serie sin leer: la copia queda sin confirmar", vers("R1")[-1].serial == "", vers("R1")[-1].serial)
check("serie sin leer: no se revierte sola", not AUTO and events("R1", "Reversión automática en pausa"))
check("serie sin leer: la ficha conserva la que tenía", dev("R1").serial == "FTX0000AAA1")
cmds, notes = services.rollback_commands(ID["R1"], vers("R1")[0].id)
check("serie sin leer: la reversión manual pide revisarla", cmds and any("número de serie" in n for n in notes), notes)
try:
    units.verify_live(ID["R1"])
    check("la serie no estaba en el último inventario: no se bloquea el envío", True)
except RuntimeError as e:
    check("la serie no estaba en el último inventario: no se bloquea el envío", False, str(e))
R.serial, R.extra = "FTX0000AAA1", "ip domain name tercero\n"
backup("R1", "scheduled")
check("tras una copia sin confirmar tampoco se revierte sola a ella", not AUTO)
R.extra = "ip domain name cuarto\n"
backup("R1", "scheduled")
check("confirmada otra vez, la reversión automática vuelve", AUTO == [ID["R1"]])

P = Ios(None, "R2")  # una plataforma que no informa su serie
AT["192.0.2.22"] = P
backup("R2")
setf("R2", auto_rollback=True)
AUTO.clear()
P.extra = "ip domain name otro\n"
backup("R2", "scheduled")
check("sin serie nunca: la reversión automática funciona como antes", AUTO == [ID["R2"]] and vers("R2")[-1].serial is None)

# --- 4) otro aparato con otro firmware y la misma configuración
n_run, n_start = len(vers("R1")), len(vers("R1", "startup"))
N = Ios("FTX0000BBB2", "R1", "15.7(3)M8")
N.extra = R.extra
AT["192.0.2.21"] = N
services.FW_CHANGED.clear()
res = backup("R1")
check("misma configuración en otro aparato: igual se guarda su primera copia",
      res == "unchanged" and len(vers("R1")) == n_run + 1 and vers("R1")[-1].serial == N.serial, (res, len(vers("R1"))))
check("también su startup", len(vers("R1", "startup")) == n_start + 1 and vers("R1", "startup")[-2].protected)
check("no es una actualización de firmware", not events("R1", "Copias protegidas (firmware)") and ID["R1"] not in services.FW_CHANGED)
check("la ficha queda con el firmware del nuevo", "15.7" in (dev("R1").os_version or ""), dev("R1").os_version)
check("«equipo nuevo» sin cambio de configuración: sin evento de cambio de más",
      "marcada «equipo nuevo»." in events("R1", "Equipo reemplazado")[0], events("R1", "Equipo reemplazado"))

N.version = "15.9(3)M2"
r = fwverify.read_inventory(ID["R1"])
v = vers("R1")
check("actualización leída al comprobar: protege la copia de la versión anterior",
      r["ok"] and v[-1].protected and v[-1].serial == N.serial and len(events("R1", "Copias protegidas (firmware)")) == 1, r)
check("y el próximo respaldo lo sabe", ID["R1"] in services.FW_CHANGED)
backup("R1")
check("sin avisar dos veces", len(events("R1", "Copias protegidas (firmware)")) == 1)
AT["192.0.2.21"] = R
fwverify.read_inventory(ID["R1"])
check("al comprobar, otro aparato no se pisa: lo registra el respaldo",
      dev("R1").serial == N.serial and len(events("R1", "Copias protegidas (firmware)")) == 1)
backup("R1")
check("y el respaldo lo registra", dev("R1").serial == R.serial and len(events("R1", "Equipo reemplazado")) == 2)
check("categorías de los avisos dentro de 32 letras (PostgreSQL)",
      all(len(c) <= 32 for c in ("Copias protegidas (firmware)", "Tarjeta de control cambiada", "Reversión automática en pausa")))

# --- 5) MikroTik: archivos, licencia y permisos
INV = "                  version: {0} (stable)\n               board-name: CCR2004\n\n       serial-number: {1}\n"
RUN = "# by RouterOS {0}\n# serial number = {1}\n/system identity\nset name={2}\n"
BLOB = {"backup": {"data": b"\x88\xac\xa1\xb1" + b"\0" * 20, "enc": ""}, "rsc": {"data": b"# x by RouterOS"}}


def store(version, serial, name):
    with SessionLocal() as db:
        d = db.get(Device, ID["MT"])
        return services._store_raw(db, d, services.get_profile(d.device_type),
                                   {"running": RUN.format(version, serial, name), "inventory": INV.format(version, serial)},
                                   "manual", "admin", True)


def mt_rows():
    with SessionLocal() as db:
        return list(db.scalars(select(MtBackup).where(MtBackup.device_id == ID["MT"]).order_by(MtBackup.id)))


store("7.20.4", "HX000AAA111", "uno")
setf("MT", mtb_keep=2, license_enc=encrypt("licencia del anterior"), license_at=now(), mtb_perm='{"level":"none"}')
a1 = mtfiles._store(ID["MT"], BLOB, "manual", "admin", "", "7.20.4 (stable)", "HX000AAA111")
a2 = mtfiles._store(ID["MT"], BLOB, "manual", "admin", "", "7.20.4 (stable)", "HX000AAA111")
early = mtfiles._store(ID["MT"], BLOB, "scheduled", None, "", "7.25.1 (stable)", "HX000BBB222")
check("copia completa hecha antes de detectar el cambio: no borra las del anterior", [r.id for r in mt_rows()] == [a1, a2, early])
services.FW_CHANGED.clear()
store("7.25.1", "HX000BBB222", "dos")
rows, d = {r.id: r for r in mt_rows()}, dev("MT")
check("se protege la última completa del anterior", rows[a2].protected and "HX000AAA111" in rows[a2].protect_note and not rows[a1].protected)
check("no la que ya era del nuevo", not rows[early].protected)
check("no dice que se actualizó el firmware", not events("MT", "Copias protegidas (firmware)") and ID["MT"] not in services.FW_CHANGED)
check("no hereda licencia ni permisos", d.license_enc is None and d.mtb_perm is None and d.mtb_last_at is None)
setf("MT", mtb_cred_id=d.credential_id)
check("otro aparato: toca copia completa aunque no haya cambios", fullcopy.reason(dev("MT"), "unchanged", False, True) == "equipo")
check("el origen de esa copia cabe en la base", len("change:equipo") <= 16 and "change:equipo" in fullcopy.TRIGGERS)
for _ in range(3):
    mtfiles._store(ID["MT"], BLOB, "manual", "admin", "", "7.25.1 (stable)", "HX000BBB222")
check("«Guardar 2» no se lleva la del anterior", a2 in [r.id for r in mt_rows()] and len(mt_rows()) == 3, [r.id for r in mt_rows()])
store("7.26", "HX000BBB222", "tres")
rows = mt_rows()
check("una actualización del nuevo protege su copia, no otra vez la del anterior",
      rows[-1].protected and rows[-1].serial == "HX000BBB222" and "7.26" in rows[-1].protect_note)
first = vers("MT")[0].id
for i in range(4):
    store("7.26", "HX000BBB222", f"n{i}")
set_setting("retention_versions", "1")
scheduler.purge_old_versions()
set_setting("retention_versions", "0")
left = vers("MT")
check("la retención no se lleva la última del anterior", left[0].id == first and left[0].protected)
with SessionLocal() as db:
    db.get(ConfigVersion, first).label = None
    db.commit()
set_setting("retention_versions", "1")
scheduler.purge_old_versions()
set_setting("retention_versions", "0")
check("protegida sin etiqueta: tampoco", vers("MT")[0].id == first)
check("series de relleno no son series", not units.key("00000000") and not units.key("N/A") and not units.key("To be filled by O.E.M."))

# --- 6) al actualizar: reemplazos detectados antes de guardar la serie
with SessionLocal() as db:
    cred = db.scalars(select(Credential)).first().id
    p = Device(name="VIEJA 1", host="192.0.2.41", device_type="zte_olt", credential_id=cred, serial="800000000002")
    q = Device(name="VIEJA 2", host="192.0.2.42", device_type="zte_olt", credential_id=cred, serial="800000000001")
    r = Device(name="VIEJA 3", host="192.0.2.43", device_type="zte_olt", credential_id=cred, serial="800000000009")
    s = Device(name="VIEJA 4", host="192.0.2.44", device_type="zte_olt", credential_id=cred, serial="800000000004")
    db.add_all([p, q, r, s])
    db.flush()
    t0 = now() - timedelta(days=3)
    for d_, old, new in ((p, "800000000001", "800000000002"), (q, "800000000002", "800000000001"), (r, None, "800000000009")):
        for i, label in enumerate((None, None, units.NEW_LABEL + new)):
            db.add(ConfigVersion(device_id=d_.id, kind="running", content_enc=encrypt(f"cfg {i}"), sha256=str(i), size=9,
                                 trigger="manual", label=label, created_at=t0 + timedelta(hours=i)))
        if old:
            db.add(Event(severity="warning", category="Equipo reemplazado", device_id=d_.id,
                         created_at=t0 + timedelta(hours=2, seconds=8),
                         message=f"{d_.name} ({d_.host}): cambió el número de serie {old} → {new}. Es otro aparato con la "
                                 "misma dirección (RMA o cambio de equipo). Revise."))
    db.add(ConfigVersion(device_id=s.id, kind="running", content_enc=encrypt("cfg"), sha256="s", size=3, trigger="manual"))
    db.flush()
    p.baseline_version_id = db.scalars(select(ConfigVersion.id).where(ConfigVersion.device_id == p.id).order_by(ConfigVersion.id)).first()
    db.commit()
    ID.update({d_.name: d_.id for d_ in (p, q, r, s)})
with SessionLocal() as db:  # las fechas se guardan al segundo: todo en el mismo, manda el orden de las versiones
    w = Device(name="VIEJA 5", host="192.0.2.45", device_type="mikrotik_routeros", credential_id=cred, serial="800000000052")
    db.add(w)
    db.flush()
    same = now() - timedelta(days=1)
    for kind, label in (("running", None), ("running", None), ("startup", None),
                        ("running", units.NEW_LABEL + "800000000052"), ("startup", None), ("running", None)):
        db.add(ConfigVersion(device_id=w.id, kind=kind, content_enc=encrypt("cfg"), sha256="w", size=3, trigger="manual",
                             label=label, created_at=same))
        db.flush()
    db.add(MtBackup(device_id=w.id, trigger="manual", created_at=same))
    db.add(Event(severity="warning", category="Equipo reemplazado", device_id=w.id, created_at=same,
                 message="VIEJA 5 (192.0.2.45): cambió el número de serie 800000000051 → 800000000052. Es otro aparato"))
    db.commit()
    ID["VIEJA 5"] = w.id
set_setting("units_migrated", "0")
init_db()
v = vers("VIEJA 5")
check("mismo segundo: cada versión con su aparato",
      [x.serial for x in v] == ["800000000051"] * 2 + ["800000000052"] * 2 and v[1].protected, [x.serial for x in v])
check("mismo segundo: también el startup", [x.serial for x in vers("VIEJA 5", "startup")] == ["800000000051", "800000000052"])
check("mismo segundo: la reversión al anterior queda bloqueada",
      services.rollback_commands(ID["VIEJA 5"], v[1].id)[0] is None)
with SessionLocal() as db:
    f = db.scalars(select(MtBackup).where(MtBackup.device_id == ID["VIEJA 5"])).first()
    check("mismo segundo: la copia completa queda protegida como del anterior", f.serial == "800000000051" and f.protected)
v = vers("VIEJA 1")
check("al actualizar: cada copia con su serie", [x.serial for x in v] == ["800000000001", "800000000001", "800000000002"], [x.serial for x in v])
check("al actualizar: protege la última del anterior, sin tocar las demás", v[1].protected and not v[0].protected and v[1].label)
check("al actualizar: la reversión al anterior queda bloqueada", services.rollback_commands(ID["VIEJA 1"], v[1].id)[0] is None)
check("al actualizar: quita la línea base del anterior", dev("VIEJA 1").baseline_version_id is None)
check("al actualizar: reconoce el intercambio", notice("VIEJA 1")["kind"] == "swap" and notice("VIEJA 2")["kind"] == "swap")
v = vers("VIEJA 3")
check("sin el aviso, por la etiqueta: serie anterior desconocida y bloqueada",
      v[1].serial == units.UNKNOWN and v[1].protected and services.rollback_commands(ID["VIEJA 3"], v[1].id)[0] is None, v[1].serial)
check("sin indicios no se inventa una serie", vers("VIEJA 4")[0].serial is None and not vers("VIEJA 4")[0].protected)
check("la configuración guardada no se toca", decrypt(vers("VIEJA 1")[1].content_enc) == "cfg 1")
setf("VIEJA 4", replaced_serial="marca")
init_db()
check("la migración corre una sola vez", dev("VIEJA 4").replaced_serial == "marca")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
