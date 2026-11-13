"""Firmware en muchos equipos (app/firmware.py), sin red: espacio libre antes de copiar, suma calculada en el equipo,
suma del fabricante al cargar, tandas (y detenerse si una falla), avance por equipo y la comprobación previa."""
import hashlib
import io
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import firmware, services  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ChangeRequest, Credential, Device, FirmwareJob, SessionLocal, init_db, select  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- espacio libre
DF = """Filesystem           1K-blocks      Used Available Use% Mounted on
tmpfs                    30000      9520     20480  32% /tmp
/dev/mtdblock7
                        262144    100000    162144  38% /root.dev"""
check("df -k: lo disponible, el menor de las filas (aunque la línea venga partida)", firmware._df_free(DF) == 20480 * 1024)
check("df -k sin filas: no se sabe", firmware._df_free("sh: df: not found") is None)
check("margen: la imagen y 1 MB (o el 2 %)", firmware.need_bytes(1000) == 1000 + (1 << 20)
      and firmware.need_bytes(500 << 20) == (500 << 20) + (10 << 20))


class Conn:
    """Equipo simulado: responde por comando y anota lo que se le pidió."""

    def __init__(self, answers):
        self.answers, self.sent = answers, []

    def send_command(self, cmd, read_timeout=0):
        self.sent.append(cmd)
        for k, v in self.answers.items():
            if cmd.startswith(k):
                return v(cmd) if callable(v) else v
        return "sh: not found"

    send_command_timing = send_command


ros = Conn({"/system resource print": "   uptime: 1d\n   free-hdd-space: 12.5MiB\n   total-hdd-space: 128.0MiB\n"})
free, src = firmware.free_space(ros, "routeros", {}, None)
check("RouterOS: free-hdd-space", free == int(12.5 * (1 << 20)) and src == "free-hdd-space", (free, src))


class Img:
    def __init__(self, data, version="6.3.16"):
        self.filename, self.size, self.version = "XW.bin", len(data), version
        self.md5, self.sha256 = hashlib.md5(data).hexdigest(), hashlib.sha256(data).hexdigest()


data = os.urandom(300_000)
img = Img(data)
copied = []
firmware.scp_put = lambda conn, local, remote, progress=None: copied.append(remote)  # la copia no hace falta aquí
ls = f"-rw-r--r--    1 ubnt     admin    {img.size} Oct 27 12:00 /tmp/fwupdate.bin"

poor = Conn({"df -k /tmp": "Filesystem 1K-blocks Used Available Use% Mounted on\ntmpfs 3000 2900 100 97% /tmp",
             "ls -l": ls})
log = []
try:
    firmware._airos(poor, img, True, log)
    check("sin espacio: no copia", False)
except RuntimeError as e:
    check("sin espacio: se para antes de copiar y dice cuánto falta", not copied and "No hay espacio" in str(e)
          and "libre 100.0 KB" in str(e), str(e))

bad = Conn({"df -k /tmp": DF.split("\n/dev")[0], "ls -l": ls, "sha256sum": "0" * 64 + "  /tmp/fwupdate.bin"})
log = []
try:
    firmware._airos(bad, img, True, log)
    check("suma distinta: no graba", False)
except RuntimeError as e:
    check("suma SHA-256 distinta en el equipo: no graba la imagen", "no coincide" in str(e) and "fwupdate -m" not in bad.sent, str(e))

good = Conn({"df -k /tmp": DF.split("\n/dev")[0], "ls -l": ls, "sha256sum": "sh: sha256sum: not found",
             "md5sum": img.md5 + "  /tmp/fwupdate.bin", "fwupdate -m": "Done, rebooting..."})
stages = []
log = []
firmware._airos(good, img, True, log, lambda st=None, pct=None, msg=None: stages.append(st) if st else None)
check("sin sha256sum usa md5sum; coincide y graba", "Suma MD5 calculada en el equipo: coincide con la imagen." in log
      and "fwupdate -m" in good.sent, log)
check("pasos en orden: espacio, copia, suma, instalar", stages == ["space", "copy", "verify", "install"], stages)
none = Conn({"df -k /tmp": DF.split("\n/dev")[0], "ls -l": ls})
log = []
firmware._airos(none, img, False, log)
check("sin sumas en el equipo: lo dice y sigue con el tamaño comprobado", any("no calcula sumas" in x for x in log), log)

# ---------------------------------------------------------------- suma del fabricante al cargar
blob = os.urandom(50_000)
im = firmware.save_image("img-a.bin", io.BytesIO(blob), "admin", "1.0", "ubiquiti_airos", hashlib.sha256(blob).hexdigest().upper())
check("con el SHA-256 del fabricante (en mayúsculas) se guarda y se anota", im.vendor_sum == hashlib.sha256(blob).hexdigest()
      and im.sha256 == hashlib.sha256(blob).hexdigest())
try:
    firmware.save_image("img-a.bin", io.BytesIO(b"otra cosa"), "admin", "1.1", "cisco_ios", hashlib.md5(blob).hexdigest())
    check("suma que no coincide: error", False)
except ValueError as e:
    on_disk = (firmware.FIRMWARE_DIR / "img-a.bin").read_bytes()
    check("suma que no coincide: no se guarda ni reemplaza la imagen que había", "no coincide" in str(e) and on_disk == blob
          and not list(firmware.FIRMWARE_DIR.glob(".*subiendo")))
try:
    firmware.save_image("img-b.bin", io.BytesIO(blob), "admin", "1.0", "", "no-es-una-suma")
    check("formato de suma: error", False)
except ValueError as e:
    check("una suma que no es MD5 ni SHA-256 se rechaza", "MD5" in str(e))

# ---------------------------------------------------------------- tandas
with SessionLocal() as db:
    cred = Credential(name="c", username="u", password_enc=encrypt("p"))
    db.add(cred)
    db.flush()
    devs = [Device(name=f"AP-{i}", host=f"192.0.2.{i}", device_type="ubiquiti_airos", credential_id=cred.id, os_version="6.3.6")
            for i in range(1, 8)]
    db.add_all(devs)
    db.commit()
    ids = [d.id for d in devs]
    cr = ChangeRequest(title="fw", body="", variables=json.dumps({"batch": 3, "stop": True}), device_ids=json.dumps(ids),
                       requested_by="admin", kind="firmware", firmware_id=im.id, reload=False, status="running")
    db.add(cr)
    db.commit()
    cr_id = cr.id
    check("7 equipos de a 3: tres tandas", firmware.plan_batches(cr, ids) == [ids[:3], ids[3:6], ids[6:]],
          firmware.plan_batches(cr, ids))
    cr.variables = json.dumps({"batch": 0})
    check("«todos a la vez»: una tanda", firmware.plan_batches(cr, ids) == [ids])
    cr.variables = json.dumps({"batch": 3, "stop": True})
    db.commit()

calls = []


def fake_upgrade(i, image_id, reload, channel, cr_id, progress, wait):
    calls.append(i)
    progress("copy", 50)
    if i == ids[1]:
        raise RuntimeError("No hay espacio para la imagen")
    if i == ids[2]:
        progress("skipped", None, "Ya tiene la versión 6.3.16: no se toca.")
        raise firmware.Skipped("Ya tiene la versión 6.3.16: no se toca.")
    progress("done", None, "ok")
    return "log de " + str(i)


real_upgrade = firmware.upgrade_device
firmware.upgrade_device = fake_upgrade
events = []
real_event = services.add_event
services.add_event = lambda *a, **k: events.append(a)
with SessionLocal() as db:
    firmware.execute_firmware_request(db, db.get(ChangeRequest, cr_id))
with SessionLocal() as db:
    c = db.get(ChangeRequest, cr_id)
    jobs = {j.device_id: j for j in db.scalars(select(FirmwareJob).where(FirmwareJob.cr_id == cr_id))}
check("si uno falla, las tandas siguientes no empiezan", sorted(calls) == sorted(ids[:3]), calls)
check("el que falló, el que ya la tenía y los que no se hicieron quedan en el avance",
      jobs[ids[1]].stage == "failed" and "espacio" in jobs[ids[1]].message and jobs[ids[2]].stage == "skipped"
      and all(jobs[i].stage == "stopped" for i in ids[3:]) and jobs[ids[0]].stage == "done",
      {k: v.stage for k, v in jobs.items()})
check("cada equipo en su tanda", [jobs[i].batch for i in ids] == [1, 1, 1, 2, 2, 2, 3])
check("solicitud ejecutada en parte, y por qué", c.status == "partial" and "Se detuvo después de la tanda 1" in c.result
      and "SIN CAMBIOS" in c.result and "NO SE HIZO" in c.result, c.result)
check("el evento cuenta los que ya la tenían y que se detuvo", events and "1 ya tenían la versión" in events[-1][2]
      and "detenida" in events[-1][2], events[-1:])

with SessionLocal() as db:
    c = db.get(ChangeRequest, cr_id)
    c.variables, c.status = json.dumps({"batch": 3, "stop": False}), "running"
    db.commit()
calls.clear()
with SessionLocal() as db:
    firmware.execute_firmware_request(db, db.get(ChangeRequest, cr_id))
with SessionLocal() as db:
    c = db.get(ChangeRequest, cr_id)
    n = db.query(FirmwareJob).filter(FirmwareJob.cr_id == cr_id, FirmwareJob.kind == "run").count()
check("con «seguir aunque falle» hace todas las tandas", sorted(calls) == sorted(ids) and c.status == "partial", calls)
check("al volver a ejecutarla, el avance empieza de cero (no se duplica)", n == len(ids), n)
firmware.upgrade_device = real_upgrade

# ---------------------------------------------------------------- avance
p = firmware.Progress(None)
p("copy", 10)  # sin fila: no hace nada
with SessionLocal() as db:
    j = db.scalars(select(FirmwareJob).where(FirmwareJob.cr_id == cr_id)).first()
    jid = j.id
pr = firmware.Progress(jid)
pr("copy", 0)
pr(None, 40)  # menos de 1,5 s después: no se escribe
with SessionLocal() as db:
    check("el porcentaje de la copia no escribe a cada paquete", db.get(FirmwareJob, jid).pct == 0)
pr(None, 100)
with SessionLocal() as db:
    check("…pero el 100 % siempre", db.get(FirmwareJob, jid).pct == 100)
with SessionLocal() as db:
    info = firmware.page_info(db.get(ChangeRequest, cr_id))
check("la solicitud dice cómo van las tandas; con un equipo copiando sigue «en curso»",
      "De a 3 equipos" in info["plan"] and "si uno falla" not in info["plan"] and info["run"] and info["busy"] is True, info["plan"])
pr("done", None, "ok")
with SessionLocal() as db:
    check("terminado todo, ya no está en curso", firmware.page_info(db.get(ChangeRequest, cr_id))["busy"] is False)

# ---------------------------------------------------------------- comprobación previa
real_connect, real_free = services.connect, firmware.free_space


class Ctx:
    def __enter__(self):
        return Conn({})

    def __exit__(self, *a):
        return False


def connect(dev):
    if dev.name == "AP-1":
        raise OSError("timed out")
    return Ctx()


services.connect = connect
firmware.free_space = lambda conn, method, fw, img: ((100 << 10), "df -k")
with SessionLocal() as db:
    db.get(Device, ids[2]).os_version = "1.0"
    db.commit()
firmware.check_request(cr_id)
with SessionLocal() as db:
    chk = {j.device_id: j for j in db.scalars(select(FirmwareJob).where(FirmwareJob.cr_id == cr_id, FirmwareJob.kind == "check"))}
check("comprobación: el que no responde", chk[ids[0]].stage == "failed" and "No se pudo conectar" in chk[ids[0]].message)
check("comprobación: el que ya tiene la versión no se toca", chk[ids[2]].stage == "done" and "Ya tiene la versión" in chk[ids[2]].message)
check("comprobación: sin espacio", chk[ids[3]].stage == "failed" and "Sin espacio" in chk[ids[3]].message, chk[ids[3]].message)
firmware.free_space = lambda conn, method, fw, img: ((50 << 20), "df -k")
firmware.check_request(cr_id)
with SessionLocal() as db:
    chk = {j.device_id: j for j in db.scalars(select(FirmwareJob).where(FirmwareJob.cr_id == cr_id, FirmwareJob.kind == "check"))}
    check("comprobación: con espacio, dice versión y espacio; y no se duplican las filas",
          chk[ids[3]].stage == "done" and "6.3.6 → 1.0" in chk[ids[3]].message and "50.0 MB" in chk[ids[3]].message
          and len(chk) == len(ids), chk[ids[3]].message)
services.connect, firmware.free_space, services.add_event = real_connect, real_free, real_event

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
