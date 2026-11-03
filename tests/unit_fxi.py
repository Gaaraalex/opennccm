"""Imagen .fxi de WatchGuard (app/fxi.py): el receptor FTP de un solo uso (solo la IP del Firebox, usuario de un solo uso,
modo pasivo y activo, un archivo, se cierra solo) y el respaldo completo con un Firebox simulado que sube la imagen de
verdad por FTP; ninguna clave ni contraseña queda en el registro ni en los mensajes; solo se guardan las N últimas; dos
fallos seguidos abren la alarma. Sin red: todo en 127.0.0.1."""
import ftplib
import io
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"), NCM_FW_CHECK_NEW="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import fxi, services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import Credential, Device, Event, SessionLocal, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


PORT, PASV = 52121, (52130, 52135)
DATA = os.urandom(300_000)


def upload(rx, passive=True, user=None, pw=None, data=DATA, name="x.fxi"):
    f = ftplib.FTP()
    f.connect("127.0.0.1", rx.port, timeout=10)
    f.login(user or rx.user, pw or rx.password)
    f.set_pasv(passive)
    f.storbinary(f"STOR {name}", io.BytesIO(data))
    f.quit()


# 1) receptor: modo pasivo
rx = fxi.Receiver("127.0.0.1", Path(D) / "rx", port=PORT, pasv=PASV, timeout=20)
rx.start()
upload(rx, passive=True)
got = rx.wait(5)
check("pasivo: recibe el archivo completo", got and got["size"] == len(DATA) and Path(got["path"]).read_bytes() == DATA)
try:
    upload(rx, name="otro.fxi")
    second = False
except ftplib.error_perm:
    second = True
except OSError:
    second = True
check("un solo archivo por receptor", second)
rx.close()
time.sleep(0.3)
try:
    ftplib.FTP().connect("127.0.0.1", PORT, timeout=2)
    closed = False
except OSError:
    closed = True
check("al cerrarlo, el puerto deja de escuchar", closed)

# 2) modo activo, contraseña equivocada y otra IP
rx = fxi.Receiver("127.0.0.1", Path(D) / "rx", port=PORT, pasv=PASV, timeout=20)
rx.start()
try:
    upload(rx, pw="mala")
    bad = False
except ftplib.error_perm:
    bad = True
check("contraseña equivocada: rechazada", bad and rx.result is None)
upload(rx, passive=False)
check("activo (PORT): recibe el archivo", (rx.wait(5) or {}).get("size") == len(DATA))
rx.close()
rx = fxi.Receiver("10.9.9.9", Path(D) / "rx", port=PORT, pasv=PASV, timeout=20)
rx.start()
try:
    upload(rx)
    foreign = False
except (OSError, EOFError, ftplib.Error):
    foreign = True
check("otra IP distinta de la del Firebox: no se la atiende", foreign and rx.result is None)
rx.close()
rx = fxi.Receiver("127.0.0.1", Path(D) / "rx", port=PORT, pasv=PASV, timeout=2)
rx.start()
time.sleep(3.5)
try:
    ftplib.FTP().connect("127.0.0.1", PORT, timeout=2)
    expired = False
except OSError:
    expired = True
check("sin archivo, se cierra solo al vencer el plazo", expired)
check("contraseña y usuario de un solo uso", fxi.Receiver("1.1.1.1", Path(D)).password != rx.password)

# 3) el respaldo completo con un Firebox simulado
init_db()
KEY = "Clave-Img-2026x"  # 15: el máximo que admite Fireware
SENT = []


class FakeFirebox:
    images: dict = {}
    mode = "ok"

    def send_command(self, cmd, **kw):
        SENT.append(cmd)
        m = re.match(r"^backup image (\S+)$", cmd)
        if m:
            if self.mode == "denied":
                return "% Error: permission denied for this user\nWG#"
            self.images[m.group(1)] = DATA
            return "Backup image saved\nWG#"
        m = re.match(r"^export (image (\S+) (\S+)|config) to (ftp://\S+)$", cmd)
        if m:
            if m.group(3) and not 8 <= len(m.group(3)) <= 15:  # como Fireware 12.8 (T40)
                return "%Error: the length of password should be between 8 and 15.\nWG#"
            if self.mode == "noftp":
                return "% Error: Failed to connect to ftp server (timeout)\nWG#"
            u = urlparse(m.group(4))
            f = ftplib.FTP()
            f.connect(u.hostname, u.port, timeout=10)
            f.login(u.username, u.password)
            f.storbinary("STOR " + (u.path.lstrip("/") or "x"), io.BytesIO(self.images.get(m.group(2), b"<profile/>" * 50)))
            f.quit()
            return "Export done\nWG#"
        if cmd.startswith("delete backup "):
            self.images.pop(cmd.split()[-1], None)
            return "WG#"
        return "% Invalid input\nWG#"

    def disconnect(self):
        pass


FB = FakeFirebox()
services.connect = lambda target: FB
fxi.FIREWALL_REQUEST = Path(D) / "fxi-firewall.request"
fxi.firewall = lambda *a, **k: ""  # sin systemd en la prueba
with SessionLocal() as db:
    cr = Credential(name="status", username="status", password_enc=encrypt("x"))
    adm = Credential(name="fw-admin", username="fw-admin", password_enc=encrypt("y"))
    db.add_all([cr, adm])
    db.flush()
    dev = Device(name="fw-prueba", host="127.0.0.1", port=4118, device_type="watchguard_fireware", credential_id=cr.id,
                 fxi_cred_id=adm.id, fxi_key_enc=encrypt(KEY), fxi_addr="127.0.0.1", fxi_port=PORT,
                 fxi_pasv=f"{PASV[0]}-{PASV[1]}", fxi_keep=2)
    db.add(dev)
    db.commit()
    dev_id = dev.id


def run(trigger="manual", test=False):
    assert fxi.start(dev_id, trigger, "admin", test=test)
    for _ in range(300):
        st = fxi.state(dev_id)
        if st["status"] != "running":
            return st
        time.sleep(0.1)
    return fxi.state(dev_id)


st = run()
imgs = fxi.images(dev_id)
check("respaldo: termina bien, con los 5 pasos en verde", st["status"] == "ok" and [s["k"] for s in st["steps"]] == ["ok"] * 5,
      (st["status"], st["error"], [s["k"] for s in st["steps"]]))
check("imagen guardada con su tamaño, SHA-256 y su clave cifrada", len(imgs) == 1 and imgs[0].size == len(DATA)
      and fxi.path(imgs[0]).read_bytes() == DATA and decrypt(imgs[0].key_enc) == KEY)
check("órdenes: backup image, export image y delete backup", [c.split()[0] + " " + c.split()[1] for c in SENT] ==
      ["backup image", "export image", "delete backup"], SENT)
check("no queda la imagen en el Firebox", FB.images == {})
check("el paso 4 muestra el archivo con su punto y el tamaño con coma", re.search(r"\.fxi · \d+,\d MB$", st["steps"][3]["d"]),
      st["steps"][3]["d"])
alltext = "\n".join(st["log"] + [s["d"] for s in st["steps"]]) + st["error"]
check("ni la clave ni la contraseña del FTP en el registro", KEY not in alltext and "********" in alltext
      and not re.search(r"ftp://onccm-\w+:(?!\*)", alltext), alltext)
check("el nombre del archivo no lleva la clave", KEY not in imgs[0].filename)

# 4) solo se guardan las N últimas
run()
run()
imgs = fxi.images(dev_id)
check("se guardan solo las 2 últimas (y se borran los archivos viejos)", len(imgs) == 2
      and len(list((Path(D) / "fxi" / str(dev_id)).glob("img-*.fxi"))) == 2)

# 5) fallos: mensaje del Firebox, sin secretos, y alarma al segundo seguido
FB.mode = "noftp"
st = run()
check("si el Firebox no llega al FTP: error con su mensaje", st["status"] == "error" and "Failed to connect" in st["device_msg"],
      (st["error"], st["device_msg"]))
check("el error no lleva secretos", KEY not in st["error"] + st["device_msg"])
check("aun fallando, se borra la imagen del Firebox", FB.images == {})
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.category == "Imagen .fxi sin crear")).all()
check("un fallo todavía no abre alarma", ev == [])
FB.mode = "denied"
st = run()
with SessionLocal() as db:
    ev = db.scalars(select(Event).where(Event.category == "Imagen .fxi sin crear")).all()
check("credencial sin permiso de administrador: lo dice el Firebox", "permission denied" in st["device_msg"])
check("dos fallos seguidos: alarma «Imagen .fxi sin crear» (aviso)", len(ev) == 1 and ev[0].severity == "warning")
FB.mode = "ok"
run()
with SessionLocal() as db:
    check("al volver a funcionar, la cuenta de fallos vuelve a 0", db.get(Device, dev_id).fxi_fails == 0)

# 6) probar conexión: el Firebox sube la configuración, no crea ninguna imagen
SENT.clear()
st = run(test=True)
check("probar conexión: el Firebox llega al receptor sin crear imagen", st["status"] == "ok" and
      SENT and SENT[0].startswith("export config to ftp://") and not any(c.startswith("backup image") for c in SENT), SENT)

# 7) clave y dirección
with SessionLocal() as db:  # una clave larga guardada con la 2.20.0: falla antes de tocar el Firebox y lo explica
    db.get(Device, dev_id).fxi_key_enc = encrypt("x" * 24)
    db.commit()
SENT.clear()
st = run()
check("clave guardada de más de 15: error claro sin enviar órdenes", st["status"] == "error" and "8 a 15" in st["error"]
      and SENT == [], (st["error"], SENT))
check("clave generada válida (8 a 15, sin espacios)", all(fxi.valid_key(fxi.new_key()) for _ in range(20)))
check("claves no válidas", not fxi.valid_key("corta") and not fxi.valid_key("con espacio 123") and not fxi.valid_key("x" * 16))
check("puertos pasivos", fxi.parse_pasv("50000-50010") == (50000, 50010) and fxi.parse_pasv("50010-50000") is None
      and fxi.parse_pasv("80-90") is None)
check("dirección local hacia el equipo", fxi.local_addr("127.0.0.1") == "127.0.0.1")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.stdout.flush()
os._exit(1 if fails else 0)  # sin esperar a los hilos de los receptores ya cerrados
