"""WatchGuard Fireware: imagen de respaldo completa (.fxi).

El respaldo de siempre (XML, «export config to console») trae la configuración. La imagen .fxi es la copia completa y
cifrada de ESE Firebox (configuración, certificados con sus claves, feature key y contraseñas): sirve para recuperar el
mismo equipo, no para migrar a otro. Fireware solo la entrega subiéndola a un servidor FTP o TFTP, así que OpenNCCM abre
un receptor FTP propio, solo mientras dura el respaldo:

  1. receptor FTP en un puerto alto, que solo acepta la IP del Firebox, con usuario y contraseña de un solo uso y que
     se cierra al recibir el archivo o a los 5 minutos;
  2. SSH al Firebox con una credencial de ADMINISTRADOR (Device Monitor no puede crear imágenes);
  3. «backup image NOMBRE» (la guarda en el Firebox) y «export image NOMBRE CLAVE to ftp://…» (la sube cifrada);
  4. se recibe el archivo;
  5. «delete backup NOMBRE» (no se deja la copia en el Firebox), se cierra el receptor y se guarda con su clave.

Si hay ufw, la aplicación (sin privilegios) pide abrir y cerrar esos puertos con fxi-firewall.request; systemd lo
ejecuta como root (opennccm fxi-firewall), y una regla abierta se quita sola a los 6 minutos aunque el proceso muera.
Ninguna clave ni contraseña aparece en los registros, la Auditoría, los nombres de archivo ni las URL."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shutil
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .config import DATA_DIR
from .db import Credential, Device, FxiImage, SessionLocal, now, select
from .i18n import N_

log = logging.getLogger("opennccm.fxi")

FXI_DIR = DATA_DIR / "fxi"
FIREWALL_REQUEST = DATA_DIR / "fxi-firewall.request"
FIREWALL_STATE = DATA_DIR / "fxi-firewall.state"
PORT = 2121
PASV = (50000, 50010)
TIMEOUT = 300            # el receptor se cierra a los 5 minutos aunque no llegue nada
MAX_BYTES = 2 * 1024 ** 3  # una imagen con el sistema operativo pasa de los 100 MB; 2 GB es el tope
KEEP = 8
FREQS = {"weekly": N_("Semanal"), "daily": N_("Diario"), "manual": N_("Solo manual")}
KEY_RE = re.compile(r"^[A-Za-z0-9._+=,#%-]{8,32}$")  # sin espacios ni comillas: va como argumento de la CLI
HIDDEN = "********"
FAILS_ALARM = 2


def new_key() -> str:
    return secrets.token_urlsafe(18)[:24].replace("-", "x").replace("_", "y")


def valid_key(k: str) -> bool:
    return bool(KEY_RE.match(k or ""))


def local_addr(host: str, port: int = 4118) -> str:
    """Dirección de este servidor que usaría para llegar al equipo (la que el Firebox ve). No envía nada."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return ""


def parse_pasv(v: str) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d{4,5})\s*[-–:]\s*(\d{4,5})\s*$", v or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if 1024 < a <= b <= 65535 and b - a <= 100 else None


# --------------------------------------------------------------------------- receptor FTP de un solo uso
class Receiver:
    """Servidor FTP mínimo que recibe UN archivo de UNA dirección. Modo pasivo (PASV/EPSV) y activo (PORT/EPRT)."""

    def __init__(self, allowed_ip: str, dest: Path, port: int = PORT, pasv: tuple[int, int] = PASV,
                 timeout: int = TIMEOUT, max_bytes: int = MAX_BYTES):
        self.allowed, self.dest, self.port, self.pasv = allowed_ip, dest, port, pasv
        self.timeout, self.max_bytes = timeout, max_bytes
        self.user, self.password = "onccm-" + secrets.token_hex(3), secrets.token_urlsafe(16)
        self.done = threading.Event()
        self.result: dict | None = None
        self.events: list[str] = []
        self._srv: socket.socket | None = None
        self._closed = False

    # -- ciclo de vida
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self.port))
        s.listen(4)
        s.settimeout(1)
        self._srv = s
        self._deadline = time.monotonic() + self.timeout
        threading.Thread(target=self._accept, daemon=True, name="fxi-ftp").start()

    def close(self):
        self._closed = True
        self.done.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> dict | None:
        self.done.wait(timeout)
        return self.result

    def _accept(self):
        while not self._closed and time.monotonic() < self._deadline:
            try:
                c, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if addr[0] != self.allowed:  # solo el Firebox
                self.events.append(f"rechazada {addr[0]}")
                c.close()
                continue
            threading.Thread(target=self._session, args=(c,), daemon=True).start()
        self.close()

    # -- sesión de control
    def _session(self, c: socket.socket):
        c.settimeout(60)
        f = c.makefile("rb")
        send = lambda m: c.sendall((m + "\r\n").encode())  # noqa: E731
        logged, user, active, pasv_sock = False, None, None, None
        try:
            send("220 OpenNCCM receptor de imagen .fxi")
            while not self._closed:
                raw = f.readline(2048)
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper()
                if cmd == "USER":
                    user = arg
                    send("331 Contraseña")
                elif cmd == "PASS":
                    if user == self.user and secrets.compare_digest(arg, self.password):
                        logged = True
                        send("230 OK")
                    else:
                        send("530 Usuario o contraseña incorrectos")
                        break
                elif cmd == "QUIT":
                    send("221 Adiós")
                    break
                elif not logged:
                    send("530 Inicie sesión")
                elif cmd == "SYST":
                    send("215 UNIX Type: L8")
                elif cmd == "FEAT":
                    c.sendall(b"211-Funciones\r\n EPSV\r\n PASV\r\n UTF8\r\n211 Fin\r\n")
                elif cmd in ("OPTS", "TYPE", "MODE", "STRU", "NOOP", "ALLO"):
                    send("200 OK")
                elif cmd in ("PWD", "XPWD"):
                    send('257 "/"')
                elif cmd in ("CWD", "XCWD", "CDUP"):
                    send("250 OK")
                elif cmd in ("PASV", "EPSV"):
                    pasv_sock = self._pasv_listen()
                    if pasv_sock is None:
                        send("425 Sin puertos pasivos libres")
                        continue
                    p = pasv_sock.getsockname()[1]
                    if cmd == "EPSV":
                        send(f"229 Modo pasivo (|||{p}|)")
                    else:
                        ip = c.getsockname()[0].split(".")
                        send(f"227 Modo pasivo ({','.join(ip)},{p >> 8},{p & 255})")
                    active = None
                elif cmd in ("PORT", "EPRT"):
                    addr = self._parse_active(cmd, arg)
                    if not addr or addr[0] != self.allowed:
                        send("501 Dirección no permitida")
                        continue
                    active, pasv_sock = addr, None
                    send("200 OK")
                elif cmd == "STOR":
                    if self.result is not None:
                        send("550 Ya se recibió el archivo")
                        continue
                    data = self._data_conn(pasv_sock, active)
                    if data is None:
                        send("425 No se pudo abrir la conexión de datos")
                        continue
                    send("150 Recibiendo")
                    ok, size = self._receive(data, arg)
                    send("226 Recibido" if ok else "552 Demasiado grande o incompleto")
                    pasv_sock = active = None
                else:  # sin listados, lecturas ni borrados: solo se recibe
                    send("502 No disponible")
        except (OSError, ValueError):
            pass
        finally:
            for s in (pasv_sock, c):
                try:
                    s and s.close()
                except OSError:
                    pass

    def _pasv_listen(self) -> socket.socket | None:
        for p in range(self.pasv[0], self.pasv[1] + 1):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("0.0.0.0", p))
                s.listen(1)
                s.settimeout(30)
                return s
            except OSError:
                s.close()
        return None

    @staticmethod
    def _parse_active(cmd: str, arg: str) -> tuple[str, int] | None:
        try:
            if cmd == "PORT":
                n = [int(x) for x in arg.split(",")]
                return ".".join(map(str, n[:4])), n[4] * 256 + n[5]
            parts = arg.split(arg[0])  # |1|ip|puerto|
            return parts[2], int(parts[3])
        except (ValueError, IndexError):
            return None

    def _data_conn(self, pasv_sock, active) -> socket.socket | None:
        try:
            if pasv_sock is not None:
                while True:
                    d, addr = pasv_sock.accept()
                    if addr[0] == self.allowed:
                        pasv_sock.close()
                        return d
                    d.close()
            if active:
                return socket.create_connection(active, timeout=30)
        except OSError:
            return None
        return None

    def _receive(self, d: socket.socket, name: str) -> tuple[bool, int]:
        self.dest.mkdir(parents=True, exist_ok=True)
        tmp = self.dest / f".recibiendo-{secrets.token_hex(4)}"
        h, size, ok = hashlib.sha256(), 0, True
        d.settimeout(120)
        try:
            with open(tmp, "wb") as out:
                while True:
                    b = d.recv(65536)
                    if not b:
                        break
                    size += len(b)
                    if size > self.max_bytes:
                        ok = False
                        break
                    h.update(b)
                    out.write(b)
                    self.progress = size
        except OSError:
            ok = False
        finally:
            d.close()
        if not ok or size == 0:
            tmp.unlink(missing_ok=True)
            return False, size
        self.result = {"path": tmp, "size": size, "sha256": h.hexdigest(), "name": Path(name).name}
        self.done.set()
        return True, size


# --------------------------------------------------------------------------- cortafuegos (ufw) por medio de root
def firewall(action: str, ip: str = "", port: int = PORT, pasv: tuple[int, int] = PASV, wait: float = 4) -> str:
    """Pide a root (unidad opennccm-fxi-firewall.path) abrir o cerrar los puertos del receptor en ufw. Devuelve el
    estado que informa root («open», «closed», «no-ufw») o «» si no hay quien lo atienda (sin systemd, pruebas)."""
    try:
        FIREWALL_STATE.unlink(missing_ok=True)
        tmp = FIREWALL_REQUEST.with_suffix(".tmp")
        tmp.write_text(f"action={action}\nip={ip}\nport={port}\npasv={pasv[0]}:{pasv[1]}\n", encoding="utf-8")
        os.replace(tmp, FIREWALL_REQUEST)
    except OSError as e:
        log.warning("No se pudo pedir %s del cortafuegos: %s", action, e)
        return ""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if FIREWALL_STATE.exists():
            return FIREWALL_STATE.read_text(encoding="utf-8").strip()
        time.sleep(0.2)
    if FIREWALL_REQUEST.exists() and action == "close":
        FIREWALL_REQUEST.unlink(missing_ok=True)  # nadie lo atiende: no se deja el pedido
    return ""


# --------------------------------------------------------------------------- el trabajo
_state: dict[int, dict] = {}
_lock = threading.Lock()
STEPS = [N_("Abrir el receptor FTP temporal"), N_("Entrar al Firebox por SSH"), N_("Pedir la imagen"),
         N_("Recibir el archivo"), N_("Cerrar el receptor y comprobar")]


def state(device_id: int) -> dict | None:
    with _lock:
        st = _state.get(device_id)
        return None if st is None else {**st, "steps": [dict(s) for s in st["steps"]], "log": list(st["log"])}


def busy(device_id: int) -> bool:
    st = _state.get(device_id)
    return bool(st and st["status"] == "running")


def cfg(dev: Device) -> dict:
    return {"cred_id": dev.fxi_cred_id, "freq": dev.fxi_freq or "weekly", "keep": dev.fxi_keep or KEEP,
            "addr": dev.fxi_addr or "", "port": dev.fxi_port or PORT, "pasv": dev.fxi_pasv or f"{PASV[0]}-{PASV[1]}",
            "on": bool(dev.fxi_cred_id and dev.fxi_key_enc)}


def start(device_id: int, trigger: str = "manual", username: str | None = None, test: bool = False,
          over: dict | None = None) -> bool:
    """over (solo la prueba): credencial, dirección y puertos del formulario, todavía sin guardar."""
    from .services import jobs
    with _lock:
        st = _state.get(device_id)
        if st and st["status"] == "running":
            return False
        _state[device_id] = {"status": "running", "test": test, "trigger": trigger, "started": time.time(), "ended": None,
                             "steps": [{"t": t, "d": "", "k": "wait"} for t in STEPS[:3 if test else 5]],
                             "log": [], "error": "", "device_msg": "", "image_id": None, "recv": 0, "file": ""}
    jobs.submit(_run, device_id, trigger, username, test, over or {})
    return True


def _set(device_id: int, **kw):
    with _lock:
        _state[device_id].update(kw)


def _step(device_id: int, i: int, k: str, d: str | None = None):
    with _lock:
        s = _state[device_id]["steps"][i]
        s["k"] = k
        if d is not None:
            s["d"] = d


def _log(device_id: int, msg: str, secrets_: tuple = ()):
    for x in secrets_:
        if x:
            msg = msg.replace(x, HIDDEN)
    with _lock:
        _state[device_id]["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")


def mask(text: str, *secrets_: str) -> str:
    for x in secrets_:
        if x:
            text = (text or "").replace(x, HIDDEN)
    return text


def _send(conn, cmd: str, timeout: int = 600) -> str:
    """Una orden de la CLI de Fireware hasta que vuelve el prompt; si pide confirmación, responde «yes»."""
    out = conn.send_command(cmd, expect_string=r"(?i)(\(y/n\)|\[y/n\]|yes/no|[#>]\s*$)", read_timeout=timeout,
                            strip_prompt=False, strip_command=True)
    if re.search(r"(?i)\(y/n\)|\[y/n\]|yes/no", out):
        out += conn.send_command("yes", expect_string=r"[#>]\s*$", read_timeout=timeout)
    return out


FAIL_RE = re.compile(r"(?i)(% ?Invalid|% ?Error|\berror\b|fail(ed|ure)?|denied|not permitted|no permission|"
                     r"insufficient|cannot|unable|timed? ?out)")


def _run(device_id: int, trigger: str, username: str | None, test: bool, over: dict | None = None):
    from .crypto import decrypt
    from .services import add_event, connect, device_lock
    rx, conn, fname, key = None, None, "", ""
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        c = {**cfg(dev), **{k: v for k, v in (over or {}).items() if v}}
        cred = db.get(Credential, c["cred_id"]) if c["cred_id"] else None
        key = decrypt(dev.fxi_key_enc) if dev.fxi_key_enc else ""
        name, host = dev.name, dev.host
        target = SimpleNamespace(name=dev.name, device_type=dev.device_type, protocol=dev.protocol, host=dev.host,
                                 port=dev.port, credential=cred)
        addr = c["addr"] or local_addr(dev.host, dev.port)
    pasv = parse_pasv(c["pasv"]) or PASV
    lock = device_lock(device_id)
    fw = ""
    try:
        if cred is None or (not key and not test):  # la prueba no crea imagen: no necesita la clave
            raise RuntimeError("Falta configurar la imagen .fxi (credencial de administrador y clave de cifrado).")
        if not addr:
            raise RuntimeError("No se pudo saber por qué dirección llega el Firebox a OpenNCCM: escríbala en la configuración.")
        # 1) receptor
        _step(device_id, 0, "run")
        rx = Receiver(host, FXI_DIR / str(device_id), port=c["port"], pasv=pasv)
        rx.start()
        fw = firewall("open", host, c["port"], pasv)
        _log(device_id, f"receptor FTP abierto en {addr}:{c['port']} (solo para {host}"
                        + {"open": "; ufw: permitido", "no-ufw": "; sin ufw activo"}.get(fw, "") + ")")
        _step(device_id, 0, "ok", f"{addr}:{c['port']} · solo para {host} · usuario de un solo uso")
        # 2) SSH con la credencial de administrador
        _step(device_id, 1, "run")
        if not lock.acquire(timeout=600):
            raise RuntimeError("El equipo está ocupado con otro respaldo.")
        try:
            conn = connect(target)
            _log(device_id, f"ssh {cred.username}@{host} -p {target.port} · OK")
            _step(device_id, 1, "ok", f"como {cred.username}")
            url = f"ftp://{rx.user}:{rx.password}@{addr}:{c['port']}/"
            secrets_ = (key, rx.password)
            # 3) la imagen
            _step(device_id, 2, "run")
            stamp = now().strftime("%Y%m%d-%H%M%S")
            fname = f"opennccm-{'prueba' if test else 'img'}-{stamp}.fxi"
            if test:  # la prueba sube la configuración (no crea ninguna imagen en el Firebox)
                cmd = f"export config to {url}opennccm-prueba-{stamp}.xml"
                _log(device_id, f"WG#> {cmd}", secrets_)
                out = _send(conn, cmd, 120)
                fname = ""
            else:
                cmd = f"backup image {fname}"
                _log(device_id, f"WG#> {cmd}")
                out = _send(conn, cmd)
                if FAIL_RE.search(out or ""):
                    raise DeviceError(out)
                cmd = f"export image {fname} {key} to {url}{fname}"
                _log(device_id, f"WG#> {cmd}", secrets_)
                watch = threading.Event()
                threading.Thread(target=_progress, args=(device_id, rx, watch), daemon=True).start()
                try:
                    out = _send(conn, cmd)
                finally:
                    watch.set()
            _step(device_id, 2, "ok", mask(f"export {'config' if test else 'image'} … to ftp://{rx.user}:{rx.password}@{addr}:{c['port']}/",
                                           *secrets_))
            if FAIL_RE.search(out or "") and rx.result is None:
                raise DeviceError(mask(out, *secrets_))
        finally:
            if conn is not None and fname:
                try:  # no se deja la imagen en el Firebox
                    _send(conn, f"delete backup {fname}", 120)
                    _log(device_id, f"WG#> delete backup {fname}")
                except Exception as e:  # noqa: BLE001
                    _log(device_id, f"no se pudo borrar {fname} del Firebox: {mask(str(e), key)}")
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            lock.release()
        if test:
            got = rx.wait(15)
            if not got:
                raise RuntimeError("El Firebox no llegó al receptor FTP.")
            Path(got["path"]).unlink(missing_ok=True)
            _set(device_id, status="ok", ended=time.time())
            _log(device_id, f"prueba bien: el Firebox llegó al receptor ({got['size']} bytes)")
            return
        # 4) el archivo
        _step(device_id, 3, "run")
        got = rx.wait(120)
        if not got:
            raise RuntimeError("El Firebox no envió la imagen al receptor.")
        mb = got["size"] / 1048576
        _step(device_id, 3, "ok", f"{fname} · {mb:.1f} MB".replace(".", ","))
        _log(device_id, f"recibido {mb:.1f} MB · sha256 {got['sha256'][:4]}…{got['sha256'][-4:]}")
        # 5) cerrar y guardar
        _step(device_id, 4, "run")
        rx.close()
        firewall("close")
        img_id = _store(device_id, got, trigger, username, key)
        _step(device_id, 4, "ok", "SHA-256 comprobado · guardada junto a su clave")
        _log(device_id, f"receptor cerrado · imagen img-{img_id} guardada")
        with SessionLocal() as db:
            d = db.get(Device, device_id)
            d.fxi_fails, d.fxi_last_at = 0, now()
            db.commit()
        _set(device_id, status="ok", ended=time.time(), image_id=img_id, file=got["name"])
        from .db import audit
        audit(username or "sistema", "fxi_backup", f"{name}: imagen img-{img_id} ({mb:.1f} MB) [{trigger}]")
    except Exception as e:  # noqa: BLE001
        msg = mask(str(e) or repr(e), key, rx.password if rx else "")
        dev_msg = mask(e.output if isinstance(e, DeviceError) else "", key, rx.password if rx else "")
        with _lock:
            for s in _state[device_id]["steps"]:
                if s["k"] == "run":
                    s["k"] = "err"
        _log(device_id, f"error: {msg}")
        if not test:  # antes de dar el estado por terminado: quien lo lea ya ve la cuenta y la alarma
            with SessionLocal() as db:
                d = db.get(Device, device_id)
                d.fxi_fails = (d.fxi_fails or 0) + 1
                fails = d.fxi_fails
                db.commit()
            if fails == FAILS_ALARM:
                add_event("warning", "Imagen .fxi sin crear",
                          f"{name}: la imagen .fxi falló {fails} veces seguidas. El respaldo XML sigue funcionando. "
                          f"Último error: {msg[:300]}", device_id)
        _set(device_id, status="error", ended=time.time(), error=msg, device_msg=dev_msg)
    finally:
        if rx is not None:
            rx.close()
            if rx.result and Path(rx.result["path"]).exists() and Path(rx.result["path"]).name.startswith(".recibiendo"):
                Path(rx.result["path"]).unlink(missing_ok=True)
        if fw:
            firewall("close")
        _log(device_id, "receptor cerrado")


class DeviceError(RuntimeError):
    """El Firebox respondió con un error: se muestra su mensaje tal cual (sin secretos ni el prompt)."""

    def __init__(self, output: str):
        self.output = "\n".join(ln for ln in (output or "").splitlines()
                                if ln.strip() and not re.match(r"^\s*\S*[#>]\s*$", ln)).strip()
        first = next((ln.strip() for ln in self.output.splitlines() if FAIL_RE.search(ln)), self.output[:200])
        super().__init__(f"El Firebox respondió: {first}")


def _progress(device_id: int, rx: Receiver, stop: threading.Event):
    """Mientras el Firebox sube la imagen: los MB recibidos en el paso 4."""
    while not stop.wait(0.5):
        got = getattr(rx, "progress", 0)
        if got:
            _step(device_id, 3, "run", f"{got / 1048576:.1f} MB recibidos".replace(".", ","))


def _store(device_id: int, got: dict, trigger: str, username: str | None, key: str) -> int:
    from .crypto import encrypt
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        img = FxiImage(device_id=device_id, filename="", size=got["size"], sha256=got["sha256"], trigger=trigger,
                       username=username, key_enc=encrypt(key), created_at=now())
        db.add(img)
        db.flush()
        dest = FXI_DIR / str(device_id) / f"img-{img.id}.fxi"
        shutil.move(str(got["path"]), dest)
        img.filename = dest.name
        keep = dev.fxi_keep or KEEP
        old = db.scalars(select(FxiImage).where(FxiImage.device_id == device_id)
                         .order_by(FxiImage.id.desc()).offset(keep)).all()
        for o in old:  # solo se guardan las N últimas
            (FXI_DIR / str(device_id) / o.filename).unlink(missing_ok=True)
            db.delete(o)
        db.commit()
        return img.id


def images(device_id: int) -> list[FxiImage]:
    with SessionLocal() as db:
        return list(db.scalars(select(FxiImage).where(FxiImage.device_id == device_id).order_by(FxiImage.id.desc())))


def path(img: FxiImage) -> Path:
    return FXI_DIR / str(img.device_id) / img.filename


def scheduled():
    """Cada día a las 03:00: los Firebox con imagen semanal (si la última tiene 7 días) o diaria."""
    with SessionLocal() as db:
        devs = db.scalars(select(Device).where(Device.fxi_cred_id.is_not(None), Device.fxi_key_enc.is_not(None),
                                               Device.enabled.is_(True))).all()
        due = []
        for d in devs:
            last = d.fxi_last_at
            if d.fxi_freq == "daily" or (d.fxi_freq in (None, "weekly") and (not last or now() - last >= timedelta(days=6, hours=20))):
                due.append(d.id)
    for i in due:
        start(i, "scheduled")


# --------------------------------------------------------------------------- web
from fastapi import APIRouter, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402

router = APIRouter()
DOC_URL = ("https://www.watchguard.com/help/docs/help-center/en-US/content/en-us/Fireware/backup_upgrade_recovery/"
           "firebox_backup_image_export_c.html")


def context(dev: Device, tl: list[dict]) -> dict:
    """Datos de la pestaña Respaldos de un Firebox: las dos tarjetas, la tabla (XML y .fxi juntas) y el diálogo."""
    with SessionLocal() as db:
        creds = [(c.id, c.name, c.username) for c in db.scalars(select(Credential).order_by(Credential.name))]
        cred = db.get(Credential, dev.fxi_cred_id) if dev.fxi_cred_id else None
    imgs = images(dev.id)
    rows = [{"k": "xml", "id": t["id"], "n": t["n"], "when": t["when"], "src": t["src"],
             "who": t["who"] if t["who"].lower() not in (t["src"].lower(), "scheduled", "syslog", "push", "api", "console") else "",
             "size": t.get("size") or 0} for t in tl if t["kind"] == "running"]
    rows += [{"k": "fxi", "id": i.id, "n": i.id, "when": i.created_at, "src": i.trigger, "who": i.username or "",
              "size": i.size} for i in imgs]
    rows.sort(key=lambda r: r["when"], reverse=True)
    c = cfg(dev)
    return {**c, "cred": cred.name if cred else "", "cred_user": cred.username if cred else "", "creds": creds,
            "images": imgs, "rows": rows[:60], "last_xml": next((r for r in rows if r["k"] == "xml"), None),
            "last_fxi": imgs[0] if imgs else None, "detected": local_addr(dev.host, dev.port), "state": state(dev.id),
            "freqs": FREQS, "doc": DOC_URL, "avg_mb": (sum(i.size for i in imgs) / len(imgs) / 1048576) if imgs else 0}


def _dev(request: Request, dev_id: int, role: str):
    from .auth import check_device, require
    user = require(request, role)
    check_device(user, dev_id)
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
    if dev is None or dev.device_type != "watchguard_fireware":
        return user, None
    return user, dev


@router.post("/devices/{dev_id}/fxi/config")
async def save_config(request: Request, dev_id: int):
    from .crypto import encrypt
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "admin")
    if dev is None:
        return go("/devices")
    f = await request.form()
    key = (f.get("key") or "").strip()
    errors = []
    if key and not valid_key(key):
        errors.append("La clave de cifrado debe tener de 8 a 32 caracteres, sin espacios.")
    pasv = parse_pasv(f.get("pasv") or "")
    port = int(f.get("port")) if (f.get("port") or "").isdigit() else 0
    if not pasv:
        errors.append("Los puertos pasivos deben ser un rango como 50000-50010 (hasta 100 puertos, por encima de 1024).")
    if not 1024 < port <= 65535 or (pasv and pasv[0] <= port <= pasv[1]):
        errors.append("El puerto de control debe estar por encima de 1024 y fuera del rango pasivo.")
    addr = (f.get("addr") or "").strip()
    if addr and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", addr):
        errors.append("La dirección debe ser una IPv4, como 10.20.0.15.")
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        cred_id = int(f.get("cred_id")) if (f.get("cred_id") or "").isdigit() else None
        if not cred_id or db.get(Credential, cred_id) is None:
            errors.append("Elija la credencial de administrador del Firebox.")
        if not key and not d.fxi_key_enc:
            errors.append("Escriba o genere la clave de cifrado de la imagen.")
        if errors:
            flash(request, " ".join(errors), "error")
            return go(f"/devices/{dev_id}?fxi=cfg#backups")
        freq = f.get("freq") if f.get("freq") in FREQS else "weekly"
        keep = max(1, min(int(f.get("keep")) if (f.get("keep") or "").isdigit() else KEEP, 30))
        d.fxi_cred_id, d.fxi_freq, d.fxi_keep = cred_id, freq, keep
        d.fxi_addr, d.fxi_port, d.fxi_pasv = addr or None, port, f"{pasv[0]}-{pasv[1]}"
        if key:
            d.fxi_key_enc = encrypt(key)
        name = d.name
        db.commit()
    log_action(request, user, "fxi_config", f"{name}: credencial {cred_id}, {freq}, guardar {keep}"
               + (", clave nueva" if key else ""))  # nunca la clave
    if f.get("go") == "run":
        start(dev_id, "manual", user.username)
        return go(f"/devices/{dev_id}/fxi/run")
    flash(request, "Configuración de la imagen .fxi guardada.")
    return go(f"/devices/{dev_id}#backups")


@router.post("/devices/{dev_id}/fxi/test")
async def test_conn(request: Request, dev_id: int):
    from .web import log_action
    user, dev = _dev(request, dev_id, "admin")
    if dev is None:
        return JSONResponse({"error": "no"}, 404)
    f = await request.form()
    over = {"cred_id": int(f.get("cred_id")) if (f.get("cred_id") or "").isdigit() else None,
            "addr": (f.get("addr") or "").strip() if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", (f.get("addr") or "").strip()) else "",
            "port": int(f.get("port")) if (f.get("port") or "").isdigit() and 1024 < int(f.get("port")) <= 65535 else None,
            "pasv": (f.get("pasv") or "") if parse_pasv(f.get("pasv") or "") else ""}
    ok = start(dev_id, "test", user.username, test=True, over=over)
    log_action(request, user, "fxi_test", dev.name)
    return JSONResponse({"started": ok})


@router.post("/devices/{dev_id}/fxi/run")
def run_now(request: Request, dev_id: int):
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "admin")
    if dev is None:
        return go("/devices")
    if not cfg(dev)["on"]:
        flash(request, "Primero configure la imagen .fxi.", "error")
        return go(f"/devices/{dev_id}?fxi=cfg#backups")
    if not start(dev_id, "manual", user.username):
        flash(request, "Ya se está creando una imagen de este equipo.", "error")
    log_action(request, user, "fxi_run", dev.name)
    return go(f"/devices/{dev_id}/fxi/run")


@router.get("/devices/{dev_id}/fxi/run")
def run_page(request: Request, dev_id: int):
    from .web import go, render
    user, dev = _dev(request, dev_id, "operator")
    if dev is None:
        return go("/devices")
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        cred = db.get(Credential, d.fxi_cred_id) if d.fxi_cred_id else None
        c = cfg(d)
        addr = c["addr"] or local_addr(d.host, d.port)
        from .db import ConfigVersion
        last_xml = db.scalar(select(ConfigVersion.id).where(ConfigVersion.device_id == dev_id, ConfigVersion.kind == "running")
                             .order_by(ConfigVersion.id.desc()).limit(1))
    return render(request, "fxi_run.html", user, device=dev, st=state(dev_id), c=c, addr=addr,
                  cred=cred.username if cred else "", doc=DOC_URL, last_xml=last_xml)


@router.get("/devices/{dev_id}/fxi/status")
def run_status(request: Request, dev_id: int):
    user, dev = _dev(request, dev_id, "operator")
    if dev is None:
        return JSONResponse({}, 404)
    st = state(dev_id) or {}
    return JSONResponse({k: st.get(k) for k in ("status", "steps", "log", "error", "device_msg", "image_id", "test")})


@router.get("/devices/{dev_id}/fxi/{img_id}/download")
def download(request: Request, dev_id: int, img_id: int):
    from .web import go, log_action
    user, dev = _dev(request, dev_id, "admin")
    with SessionLocal() as db:
        img = db.get(FxiImage, img_id)
    if dev is None or img is None or img.device_id != dev_id or not path(img).exists():
        return go(f"/devices/{dev_id}#backups")
    log_action(request, user, "fxi_download", f"{dev.name}: img-{img.id}")
    # tal cual la entregó el Firebox, con su extensión (nunca en .zip); la clave no va dentro
    return FileResponse(path(img), media_type="application/octet-stream", filename=f"{dev.name}_img-{img.id}.fxi")


@router.post("/devices/{dev_id}/fxi/{img_id}/key")
def reveal_key(request: Request, dev_id: int, img_id: int):
    from .crypto import decrypt
    from .web import log_action
    user, dev = _dev(request, dev_id, "admin")
    with SessionLocal() as db:
        img = db.get(FxiImage, img_id)
    if dev is None or img is None or img.device_id != dev_id:
        return JSONResponse({"error": "no"}, 404)
    log_action(request, user, "fxi_key_view", f"{dev.name}: img-{img.id}")  # quién vio la clave (no la clave)
    return JSONResponse({"key": decrypt(img.key_enc)})
