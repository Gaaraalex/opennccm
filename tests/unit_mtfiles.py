"""Archivos .backup y .rsc de MikroTik (app/mtfiles.py) con un RouterOS simulado (consola y SFTP):

* antes de crear nada se leen los permisos del grupo: una cuenta de solo lectura no envía ninguna orden que cree
  archivos, y se avisa una sola vez; una cuenta sin «policy» trae solo el .rsc;
* RouterOS 6 contesta «Configuration backup saved» antes de terminar de escribir: se espera a que el .backup mida lo que
  dice su cabecera (antes se traía cortado, 76 bytes en el laboratorio);
* se traen los dos con su SHA-256, el .backup cifrado (AES), el .rsc con show-sensitive en RouterOS 7;
* se borran del equipo, también si algo falla, y los restos de un corte se borran en el intento siguiente (solo los
  «opennccm-bk-…»); sin espacio no se crea nada;
* la clave nunca queda en el registro; solo se guardan los N últimos; dos fallos seguidos abren la alarma;
* qué equipos tocan en la corrida de la noche. Sin red."""
import io
import os
import re
import struct
import sys
import tempfile
import time

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"), NCM_FW_CHECK_NEW="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import paramiko  # noqa: E402

from app import mtfiles, services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import Credential, Device, Event, SessionLocal, init_db, now, select  # noqa: E402

fails = []
mtfiles.WAIT = 20


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


FULL = "local;telnet;ssh;ftp;reboot;read;write;policy;test;winbox;password;web;sniff;sensitive;api;romon;rest-api"
READ = "ssh;ftp;read;test;winbox;api;!local;!telnet;!reboot;!write;!policy;!password;!web;!sniff;!sensitive"
NOPOL = "ssh;ftp;read;write;test;sensitive;!local;!policy"


class FakeFile:
    def __init__(self, data: bytes, slow: int = 0, live_header: bool = False):
        self.data, self.visible = data, (len(data) if not slow else 76)
        self.slow, self.live_header = slow, live_header

    def grow(self):
        if self.slow and self.visible < len(self.data):
            self.visible = min(len(self.data), self.visible + max(1, len(self.data) // self.slow))

    def view(self) -> bytes:
        b = self.data[:self.visible]
        if self.live_header and len(b) >= 8:  # RouterOS 6: la cabecera dice lo escrito hasta ahora
            b = b[:4] + struct.pack("<I", len(b)) + b[8:]
        return b

    def cut(self):  # un «/export file=» mientras se escribía: queda cortado ahí, con su cabecera «correcta»
        self.data = self.view()
        self.live_header = False


class Router:
    """Lo mínimo de RouterOS que usa app/mtfiles.py."""

    def __init__(self, policy=FULL, ver="7.24.5 (stable)", free=70_000_000, slow=0, user_in_db=True):
        self.policy, self.ver, self.free, self.slow, self.user_in_db = policy, ver, free, slow, user_in_db
        self.files: dict[str, FakeFile] = {}
        self.cmds: list[str] = []
        self.fail_download = False

    def has(self, p):
        return p in [x for x in self.policy.split(";") if not x.startswith("!")]

    def send_command(self, cmd, read_timeout=None, **kw):
        self.cmds.append(cmd)
        m = re.match(r':put \[/user get \[find name="([^"]+)"\] group\]', cmd)
        if m:
            return "grp" if self.user_in_db else "no such item"
        if cmd.startswith(":put [/user active get"):
            return "grp"
        if cmd.startswith(':put [/user group get'):
            return self.policy
        if cmd == ":put [/system resource get version]":
            return self.ver
        if cmd == ":put [/system resource get free-hdd-space]":
            return str(self.free)
        m = re.match(r':put \[:len \[/file find where name~"([^"]+)"\]\]', cmd)
        if m:
            return str(sum(1 for n in self.files if re.search(m.group(1), n)))
        m = re.match(r'/file remove \[find where name~"([^"]+)"\]', cmd)
        if m:
            for n in [n for n in self.files if re.search(m.group(1), n)]:
                del self.files[n]
            return ""
        m = re.match(r'/system backup save name=(\S+) (?:password="([^"]+)"|dont-encrypt=yes)', cmd)
        if m:
            if not all(self.has(p) for p in ("write", "policy", "test")):
                return "Failed to save system configuration backup\nnot enough permissions (9)"
            body = os.urandom(20000)
            magic = b"\xef\xa8\x91\x73" if m.group(2) else b"\x88\xac\xa1\xb1"
            data = magic + struct.pack("<I", 8 + len(body)) + body
            self.files[m.group(1) + ".backup"] = FakeFile(data, self.slow, live_header=self.slow > 0)
            self.full = len(data)
            return "Configuration backup saved"
        m = re.match(r"/export file=(\S+)( show-sensitive)?$", cmd)
        if m:
            if not (self.has("write") and self.has("ftp")):
                return "not enough permissions (9) (:export; line 1)"
            for f in self.files.values():
                if f.visible < len(f.data):
                    f.cut()
            text = f"# 2026-11-21 16:53:49 by RouterOS {self.ver}\n/system identity\nset name=R\n"
            if m.group(2):
                text += "/ppp secret\nadd name=a password=secreto\n"
            self.files[m.group(1) + ".rsc"] = FakeFile(text.encode(), self.slow)
            return ""
        return "bad command name"

    def disconnect(self):
        pass


class FakeSFTP:
    def __init__(self, r: Router):
        self.r = r

    def stat(self, p):
        f = self.r.files.get(p)
        if f is None:
            raise FileNotFoundError(p)
        f.grow()
        return type("S", (), {"st_size": f.visible})()

    def open(self, p, mode="rb"):
        f = self.r.files.get(p)
        if f is None:
            raise FileNotFoundError(p)
        if p.endswith(".backup") and not self.r.has("sensitive"):
            raise PermissionError(13, "Invalid argument")
        if self.r.fail_download and p.endswith(".rsc"):
            raise OSError("Socket is closed")
        b = io.BytesIO(f.view())
        b.prefetch = lambda *a: None
        return b

    def remove(self, p):
        self.r.files.pop(p, None)

    def close(self):
        pass


R = {"r": Router()}
services.connect = lambda target: R["r"]  # noqa: E731
paramiko.SFTPClient.from_transport = staticmethod(lambda t: FakeSFTP(R["r"]))
Router.remote_conn = type("RC", (), {"get_transport": lambda self: None})()

init_db()
with SessionLocal() as db:
    cr = Credential(name="lab", username="adm", password_enc=encrypt("x"), secret_enc=encrypt(""))
    db.add(cr)
    db.flush()
    for n in ("R1", "R2", "R3", "R4"):
        db.add(Device(name=n, host="10.0.0.9", port=22, device_type="mikrotik_routeros", credential_id=cr.id))
    db.add(Device(name="TEL", host="10.0.0.8", port=23, protocol="telnet", device_type="mikrotik_routeros", credential_id=cr.id))
    db.add(Device(name="OFF", host="10.0.0.7", device_type="mikrotik_routeros", credential_id=cr.id, enabled=False))
    db.add(Device(name="CISCO", host="10.0.0.6", device_type="cisco_ios", credential_id=cr.id))
    db.commit()
    ids = {d.name: d.id for d in db.scalars(select(Device))}


def run(dev, test=False, over=None):
    assert mtfiles.start(dev, "manual", "admin", test=test, over=over)
    for _ in range(400):
        st = mtfiles.state(dev)
        if st["status"] != "running":
            return st
        time.sleep(0.05)
    return mtfiles.state(dev)


def creates(r):
    return [c for c in r.cmds if c.startswith(("/system backup save", "/export"))]


# --- 1) cuenta completa, RouterOS 7
r = R["r"] = Router()
st = run(ids["R1"])
check("completa: termina bien", st["status"] == "ok", st.get("error"))
rows = mtfiles.files(ids["R1"])
check("completa: guarda .backup y .rsc", len(rows) == 1 and rows[0].backup_size > 0 and rows[0].rsc_size > 0)
row = rows[0]
check("completa: .backup cifrado AES", row.encryption == "AES")
check("completa: los archivos están en disco", mtfiles.path(row, "backup").exists() and mtfiles.path(row, "rsc").exists())
check("completa: RouterOS 7 exporta con show-sensitive", any(c.endswith("show-sensitive") for c in r.cmds))
check("completa: el .rsc trae las claves", b"secreto" in mtfiles.path(row, "rsc").read_bytes())
check("completa: el equipo quedó limpio", not r.files, list(r.files))
with SessionLocal() as db:
    key = decrypt(db.get(Device, ids["R1"]).mtb_key_enc)
check("completa: se generó la clave del equipo", mtfiles.valid_key(key))
check("completa: la clave va en la orden", any(f'password="{key}"' in c for c in r.cmds))
check("completa: la clave no queda en el registro", key not in "\n".join(st["log"]) and mtfiles.HIDDEN in "\n".join(st["log"]))
check("completa: la clave guardada con el .backup", decrypt(row.key_enc) == key)
check("completa: los permisos se leen antes de crear nada",
      min(i for i, c in enumerate(r.cmds) if c.startswith("/system backup")) >
      max(i for i, c in enumerate(r.cmds) if "/user group get" in c))
check("completa: se guarda el resultado de permisos", mtfiles.cfg(services.SessionLocal().get(Device, ids["R1"]))["perm"]["level"] == "full")

# --- 2) RouterOS 6: el .backup se termina de escribir después de «saved»
r = R["r"] = Router(ver="6.49.23 (stable)", slow=6)
st = run(ids["R1"])
row = mtfiles.files(ids["R1"])[0]
data = mtfiles.path(row, "backup").read_bytes()
check("v6 lento: termina bien", st["status"] == "ok", st.get("error"))
check("v6 lento: el .backup llega entero (su cabecera)", len(data) == struct.unpack("<I", data[4:8])[0] == row.backup_size > 76,
      row.backup_size)
check("v6 lento: no se exporta hasta terminar el .backup (si no, lo corta)", row.backup_size == r.full, (row.backup_size, r.full))
check("v6: sin show-sensitive (RouterOS 6 lo incluye solo)", not any("show-sensitive" in c for c in r.cmds))

# --- 3) solo lectura: no se crea nada y se avisa una vez
r = R["r"] = Router(policy=READ)
st = run(ids["R2"])
check("solo lectura: falla", st["status"] == "error")
check("solo lectura: no envía ninguna orden que cree archivos", not creates(r), creates(r))
check("solo lectura: el mensaje lo dice", "solo lectura" in st["error"] and "write" in st["error"], st["error"])
check("solo lectura: no cuenta como fallo", (services.SessionLocal().get(Device, ids["R2"]).mtb_fails or 0) == 0)
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.device_id == ids["R2"])).all()
check("solo lectura: un aviso", len(ev) == 1 and ev[0].category == "Archivos .backup/.rsc sin crear", [e.message for e in ev])
run(ids["R2"])
with SessionLocal() as db:
    check("solo lectura: no repite el aviso", len(db.scalars(select(Event).where(Event.device_id == ids["R2"])).all()) == 1)

# --- 4) la comprobación de permisos (botón «Comprobar permisos») no crea nada, tampoco con cuenta completa
r = R["r"] = Router()
st = run(ids["R2"], test=True)
check("comprobar: bien con cuenta completa", st["status"] == "ok" and st["perm"]["level"] == "full", st.get("error"))
check("comprobar: no crea nada", not creates(r) and not r.files)
check("comprobar: 3 pasos", len(st["steps"]) == 3)

# --- 5) sin «policy»: solo el .rsc
r = R["r"] = Router(policy=NOPOL)
st = run(ids["R3"])
rows = mtfiles.files(ids["R3"])
check("sin policy: termina bien", st["status"] == "ok", st.get("error"))
check("sin policy: solo el .rsc", rows and rows[0].backup_size == 0 and rows[0].rsc_size > 0 and not rows[0].key_enc)
check("sin policy: no intenta el .backup", not any(c.startswith("/system backup") for c in r.cmds))
check("sin policy: avisa qué falta", "policy" in (st.get("warn") or ""), st.get("warn"))

# --- 6) usuario de RADIUS: el grupo se lee de las sesiones activas
r = R["r"] = Router(user_in_db=False)
st = run(ids["R3"], test=True)
check("RADIUS: el grupo sale de /user active", st["status"] == "ok" and st["perm"]["group"] == "grp", st.get("error"))

# --- 7) sin espacio: no se crea nada
r = R["r"] = Router(free=100_000)
st = run(ids["R4"])
check("sin espacio: falla sin crear nada", st["status"] == "error" and not creates(r) and "espacio" in st["error"], st["error"])

# --- 8) restos de un corte anterior: se borran (solo los de OpenNCCM)
r = R["r"] = Router()
r.files["opennccm-bk-20260101-000000.backup"] = FakeFile(b"x")
r.files["mio.backup"] = FakeFile(b"y")
st = run(ids["R4"])
check("restos: termina bien", st["status"] == "ok", st.get("error"))
check("restos: borra los opennccm-bk viejos y deja los del usuario", list(r.files) == ["mio.backup"], list(r.files))

# --- 9) falla a mitad: se borra lo creado; dos fallos seguidos abren la alarma
r = R["r"] = Router()
r.fail_download = True
st = run(ids["R4"])
check("falla al traer: error", st["status"] == "error")
check("falla al traer: el equipo quedó limpio", not r.files, list(r.files))
with SessionLocal() as db:
    n0 = len(db.scalars(select(Event).where(Event.device_id == ids["R4"])).all())
run(ids["R4"])
with SessionLocal() as db:
    evs = db.scalars(select(Event).where(Event.device_id == ids["R4"])).all()
    check("dos fallos: alarma", len(evs) == n0 + 1 and "2 veces" in evs[-1].message, [e.message for e in evs])
    check("dos fallos: cuenta", db.get(Device, ids["R4"]).mtb_fails == 2)
r.fail_download = False
run(ids["R4"])
check("vuelve a andar: cuenta a cero", services.SessionLocal().get(Device, ids["R4"]).mtb_fails == 0)

# --- 10) solo los N últimos
with SessionLocal() as db:
    db.get(Device, ids["R1"]).mtb_keep = 3
    db.commit()
for _ in range(4):
    R["r"] = Router()
    run(ids["R1"])
rows = mtfiles.files(ids["R1"])
left = sorted(os.listdir(mtfiles.DIR / str(ids["R1"])))
check("guarda solo los 3 últimos", len(rows) == 3 and len(left) == 6, left)

# --- 11) la cabecera del .backup
try:
    mtfiles.check_backup(b"\xef\xa8\x91\x73" + struct.pack("<I", 500) + b"x" * 68)
    check("cabecera: detecta cortado", False)
except RuntimeError as e:
    check("cabecera: detecta cortado", "incompleto" in str(e))
try:
    mtfiles.check_backup(b"<html>no es un backup</html>")
    check("cabecera: detecta otro archivo", False)
except RuntimeError as e:
    check("cabecera: detecta otro archivo", "cabecera" in str(e))

# --- 12) qué equipos tocan a la noche
with SessionLocal() as db:
    db.get(Device, ids["R1"]).mtb_last_at = now()
    db.get(Device, ids["R2"]).mtb_freq = "manual"
    db.get(Device, ids["R3"]).mtb_freq, db.get(Device, ids["R3"]).mtb_last_at = "daily", now()
    db.commit()
due = mtfiles.due()
check("noche: semanal reciente no", ids["R1"] not in due)
check("noche: solo manual no", ids["R2"] not in due)
check("noche: diario sí", ids["R3"] in due)
check("noche: telnet, desactivado y otras marcas no", not {ids["TEL"], ids["OFF"], ids["CISCO"]} & set(due))

# --- 13) Telnet: no se conecta
r = R["r"] = Router()
st = run(ids["TEL"])
check("telnet: explica que necesita SSH", st["status"] == "error" and "SSH" in st["error"] and not r.cmds)

# --- 14) la descarga manual del .backup (menú) también comprueba permisos y espera a que termine
r = R["r"] = Router(policy=READ)
try:
    services.mikrotik_backup_file(ids["R4"])
    check("descarga manual: solo lectura no crea nada", False)
except Exception as e:  # noqa: BLE001
    check("descarga manual: solo lectura no crea nada", not creates(r) and "solo lectura" in str(e), str(e))
r = R["r"] = Router(ver="6.49.23 (stable)", slow=5)
name, data = services.mikrotik_backup_file(ids["R4"])
check("descarga manual: entera y sin cifrar", len(data) == struct.unpack("<I", data[4:8])[0] and data[:4] == b"\x88\xac\xa1\xb1")
check("descarga manual: el equipo quedó limpio", not r.files)
name, data = services.mikrotik_backup_file(ids["R4"], "Clave123")
check("descarga manual con clave: cifrada", data[:4] == b"\xef\xa8\x91\x73")

print("\nTODO OK" if not fails else f"\n{len(fails)} FALLOS: {fails}")
sys.exit(1 if fails else 0)
