"""MikroTik RouterOS: archivos de respaldo del propio equipo (.backup y .rsc), guardados en OpenNCCM.

El respaldo de siempre («/export terse», texto) se compara versión a versión. Además, RouterOS hace dos archivos que
sirven para restaurar tal cual:

  * .backup: binario y cifrado con una clave, con TODO el equipo (usuarios, contraseñas, certificados). Solo se restaura
    en el mismo equipo o en uno del mismo modelo.
  * .rsc: el script de «/export file=», que también sirve para otro equipo.

Cómo se hacen, sin abrir ningún puerto en OpenNCCM (a diferencia del .fxi de WatchGuard, aquí basta el SSH del equipo):

  1. SSH al equipo (la credencial de siempre u otra elegida para esto);
  2. ANTES de crear nada, se leen los permisos (policy) del grupo de esa cuenta: el .backup necesita read, write,
     policy, test, sensitive y ftp; el .rsc, read, write y ftp. Una cuenta de solo lectura no crea ni deja nada;
  3. espacio libre (free-hdd-space) y restos de un intento anterior de OpenNCCM;
  4. «/system backup save name=… password=…» y «/export file=…» (show-sensitive en RouterOS 7, si la cuenta puede);
  5. se espera a que terminen de escribirse (RouterOS 6 contesta «Configuration backup saved» antes de terminar: la
     cabecera del .backup dice cuánto mide y se comprueba), se traen por SFTP con su SHA-256;
  6. se borran del equipo (se comprueba que no quedó nada) y se guardan aquí, el .backup junto a su clave.

La clave nunca aparece en los registros, la Auditoría, los nombres de archivo ni las URL."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from .config import DATA_DIR
from .db import Credential, Device, MtBackup, SessionLocal, now, select
from .i18n import N_

log = logging.getLogger("opennccm.mtfiles")

DIR = DATA_DIR / "mtfiles"
DTYPE = "mikrotik_routeros"
KEEP = 8
FREQS = {"change": N_("Al cambiar"), "weekly": N_("Semanal"), "daily": N_("Diario"), "manual": N_("Solo manual")}
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{8,32}$")  # va como argumento de la CLI de RouterOS: sin espacios ni comillas
HIDDEN = "********"
FAILS_ALARM = 2
PREFIX = "opennccm-bk-"  # solo se borran del equipo archivos con este nombre (los que crea OpenNCCM)
NEED_BACKUP = ("read", "write", "policy", "test", "sensitive", "ftp")
NEED_RSC = ("read", "write", "ftp")
# cabecera del .backup: 4 bytes de tipo y 4 (little endian) con el largo total del archivo
MAGIC = {b"\x88\xac\xa1\xb1": "", b"\xef\xa8\x91\x72": "RC4", b"\xef\xa8\x91\x73": "AES"}
ERR_RE = re.compile(r"(?i)failure|failed|not enough permissions|expected end of command|bad command|syntax error|"
                    r"no such item|invalid value|input does not match")
WAIT = 180  # segundos que se espera a que el equipo termine de escribir un archivo
LEVELS = {"full": N_("Completa"), "rsc": N_("Solo .rsc"), "none": N_("Solo lectura"), "unknown": N_("Sin comprobar")}

# hilos propios: las corridas de la noche (todos los MikroTik) no ocupan los de las demás tareas
pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ncm-mtfiles")


def new_key() -> str:
    abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(abc) for _ in range(20))


def valid_key(k: str) -> bool:
    return bool(KEY_RE.match(k or ""))


def mask(text: str, *secrets_: str) -> str:
    for x in secrets_:
        if x:
            text = (text or "").replace(x, HIDDEN)
    return text


def _q(s: str) -> str:
    """Texto dentro de comillas en la CLI de RouterOS."""
    return re.sub(r'(["\\$])', r"\\\1", s or "")


class DeviceError(RuntimeError):
    """RouterOS respondió con un error: se muestra su mensaje (sin secretos ni el prompt)."""

    def __init__(self, msg: str, output: str = ""):
        self.output = "\n".join(ln for ln in (output or "").splitlines()
                                if ln.strip() and not re.match(r"^\s*\[[^\]]+\]\s*[/\w]*>\s*$", ln)).strip()
        super().__init__(msg + (": " + self.output.splitlines()[0][:200] if self.output else ""))


# --------------------------------------------------------------------------- permisos y lectura del equipo
def _cmd(conn, cmd: str, timeout: int = 60) -> str:
    return (conn.send_command(cmd, read_timeout=timeout) or "").strip()


def _put(conn, expr: str) -> str:
    """Valor de «:put [...]» o «» si RouterOS dio un error."""
    out = _cmd(conn, f":put {expr}", 30)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    val = lines[-1] if lines else ""
    return "" if ERR_RE.search(out) else val


def read_perms(conn, username: str) -> dict:
    """Permisos del grupo de la cuenta con la que se entró: {"group", "policy", "level", "missing_backup",
    "missing_rsc"}. level: full (.backup y .rsc), rsc (solo .rsc), none (solo lectura) o unknown (no se pudo leer)."""
    u = _q(username)
    group = _put(conn, f'[/user get [find name="{u}"] group]')
    if not group:  # usuario de RADIUS o similar: no está en /user, pero sí en las sesiones activas con su grupo
        group = _put(conn, f'[/user active get [:pick [/user active find name="{u}"] 0] group]')
    if not group:
        return {"group": "", "policy": [], "level": "unknown", "missing_backup": list(NEED_BACKUP),
                "missing_rsc": list(NEED_RSC)}
    pol = _put(conn, f'[/user group get [find name="{_q(group)}"] policy]')
    have = sorted({p.strip() for p in re.split(r"[;,]", pol) if p.strip() and not p.strip().startswith("!")})
    if not have:
        return {"group": group, "policy": [], "level": "unknown", "missing_backup": list(NEED_BACKUP),
                "missing_rsc": list(NEED_RSC)}
    mb = [p for p in NEED_BACKUP if p not in have]
    mr = [p for p in NEED_RSC if p not in have]
    return {"group": group, "policy": have, "level": "full" if not mb else "rsc" if not mr else "none",
            "missing_backup": mb, "missing_rsc": mr}


def free_space(conn) -> int | None:
    v = _put(conn, "[/system resource get free-hdd-space]")
    return int(v) if v.isdigit() else None


def ros_version(conn) -> str:
    return _put(conn, "[/system resource get version]")


def check_backup(data: bytes) -> str:
    """Comprueba la cabecera del .backup (tipo y largo). Devuelve el cifrado («AES», «RC4» o «» sin cifrar)."""
    if len(data) < 8 or data[:4] not in MAGIC:
        raise RuntimeError("El archivo traído no es un .backup de RouterOS (cabecera desconocida)")
    size = struct.unpack("<I", data[4:8])[0]
    if size != len(data):
        raise RuntimeError(f"El .backup llegó incompleto ({len(data)} de {size} bytes)")
    return MAGIC[data[:4]]


def _stat(sftp, name: str):
    for p in (name, f"flash/{name}"):  # en los equipos con «flash/», RouterOS los deja en esa carpeta
        try:
            return p, sftp.stat(p).st_size or 0
        except OSError:
            continue
    return None, 0


STABLE = 3  # lecturas seguidas (1 por segundo) con el mismo tamaño para dar un archivo por terminado


def _wait_file(sftp, name: str, backup: bool, timeout: int = WAIT) -> str:
    """Espera a que el equipo termine de escribir el archivo: el tamaño no cambia en STABLE lecturas seguidas y, en el
    .backup, además coincide con el de su cabecera. La cabecera sola no basta: RouterOS 6 la va actualizando a medida
    que escribe (en el laboratorio, un .backup de 9 KB de los 16 KB reales, con la cabecera «correcta», restauraba
    solo media configuración). Devuelve su ruta."""
    end, prev, same = time.monotonic() + timeout, -1, 0
    while time.monotonic() < end:
        path, size = _stat(sftp, name)
        if path and size:
            ok = True
            if backup:
                ok = False
                try:
                    with sftp.open(path, "rb") as f:
                        head = f.read(8)
                    ok = len(head) == 8 and head[:4] in MAGIC and struct.unpack("<I", head[4:8])[0] == size
                except OSError:
                    pass
            same = same + 1 if (ok and size == prev) else 0
            if same >= STABLE:
                return path
            prev = size
        time.sleep(1)
    raise RuntimeError(f"El equipo no terminó de escribir {name} en {timeout} s")


def _download(sftp, path: str) -> bytes:
    with sftp.open(path, "rb") as f:
        f.prefetch()
        data = f.read()
    size = sftp.stat(path).st_size
    if len(data) != size:
        raise RuntimeError(f"{path} llegó incompleto ({len(data)} de {size} bytes)")
    return data


def leftovers(conn) -> int:
    v = _put(conn, f'[:len [/file find where name~"{PREFIX}"]]')
    return int(v) if v.isdigit() else 0


def remove_ours(conn, sftp, names: list[str], stamp: str) -> int:
    """Borra del equipo los archivos que creó OpenNCCM en esta corrida y devuelve cuántos quedaron (0 = bien)."""
    for n in names:
        path, _size = _stat(sftp, n) if sftp is not None else (None, 0)
        if path:
            try:
                sftp.remove(path)
            except OSError:
                pass
    pat = _q(PREFIX + stamp)
    left = _put(conn, f'[:len [/file find where name~"{pat}"]]')
    if left not in ("", "0"):
        _cmd(conn, f'/file remove [find where name~"{pat}"]', 60)  # en RouterOS 6 el SFTP a veces no lo ve aún
        time.sleep(1)
        left = _put(conn, f'[:len [/file find where name~"{pat}"]]')
    return int(left) if left.isdigit() else 0


def make_files(conn, sftp, *, backup: bool, rsc: bool, key: str, sensitive: bool, v7: bool, stamp: str,
               say=lambda m: None) -> dict:
    """Crea en el equipo el .backup (cifrado con key, o sin cifrar si key == "") y/o el .rsc, espera a que terminen de
    escribirse y los trae. De a uno: en RouterOS 6, un «/export file=» lanzado mientras el .backup se escribe lo deja
    cortado (visto en el laboratorio: 76 bytes, o la mitad). Siempre intenta borrarlos del equipo al terminar, también
    si algo falló."""
    name = PREFIX + stamp
    made, got = [], {}
    try:
        if backup:
            cmd = f'/system backup save name={name} ' + (f'password="{key}"' if key else "dont-encrypt=yes")
            say(mask(f"> {cmd}", key))
            made.append(name + ".backup")
            out = _cmd(conn, cmd, 300)
            if not key and "expected end of command" in out:  # RouterOS anterior a 6.43: sin «dont-encrypt»
                out = _cmd(conn, f"/system backup save name={name}", 300)
            if ERR_RE.search(out) or "saved" not in out.lower():
                raise DeviceError("RouterOS no pudo crear el .backup", mask(out, key))
            path = _wait_file(sftp, name + ".backup", True)
            data = _download(sftp, path)
            enc = check_backup(data)
            if key and not enc:
                raise RuntimeError("El .backup no quedó cifrado aunque se pidió con clave")
            got["backup"] = {"data": data, "enc": enc, "path": path}
            say(f"sftp get {path} · {len(data)} bytes")
        if rsc:
            cmd = f"/export file={name}" + (" show-sensitive" if v7 and sensitive else "")
            say(f"> {cmd}")
            made.append(name + ".rsc")
            out = _cmd(conn, cmd, 300)
            if ERR_RE.search(out):
                raise DeviceError("RouterOS no pudo crear el .rsc", out)
            path = _wait_file(sftp, name + ".rsc", False)
            data = _download(sftp, path)
            if not data.lstrip().startswith(b"#") or b"RouterOS" not in data[:300]:
                raise RuntimeError("El .rsc traído no parece un export de RouterOS")
            got["rsc"] = {"data": data, "path": path}
            say(f"sftp get {path} · {len(data)} bytes")
    finally:
        if made:
            left = remove_ours(conn, sftp, made, stamp)
            say(f"> /file remove {', '.join(made)}" + (f" · ¡quedaron {left}!" if left else " · el equipo quedó limpio"))
            got["left"] = left
    return got


# --------------------------------------------------------------------------- estado de cada corrida
_state: dict[int, dict] = {}
_lock = threading.Lock()
STEPS = [N_("Entrar al equipo por SSH"), N_("Comprobar los permisos de la credencial"), N_("Comprobar el espacio libre"),
         N_("Crear el .backup y el .rsc en el equipo"), N_("Traer los archivos por SFTP y comprobarlos"),
         N_("Borrar los archivos del equipo y guardar")]


def state(device_id: int) -> dict | None:
    with _lock:
        st = _state.get(device_id)
        return None if st is None else {**st, "steps": [dict(s) for s in st["steps"]], "log": list(st["log"])}


def busy(device_id: int) -> bool:
    st = _state.get(device_id)
    return bool(st and st["status"] == "running")


def cfg(dev: Device) -> dict:
    perm = {}
    try:
        perm = json.loads(dev.mtb_perm) if dev.mtb_perm else {}
    except ValueError:
        pass
    return {"cred_id": dev.mtb_cred_id, "freq": dev.mtb_freq or "change", "keep": dev.mtb_keep or KEEP,
            "perm": perm, "has_key": bool(dev.mtb_key_enc), "fails": dev.mtb_fails or 0, "last_at": dev.mtb_last_at}


def start(device_id: int, trigger: str = "manual", username: str | None = None, test: bool = False,
          over: dict | None = None) -> bool:
    """test: solo entra y comprueba permisos y espacio (no crea nada). over (solo la prueba): credencial elegida en el
    formulario, todavía sin guardar."""
    with _lock:
        st = _state.get(device_id)
        if st and st["status"] == "running":
            return False
        _state[device_id] = {"status": "running", "test": test, "trigger": trigger, "started": time.time(), "ended": None,
                             "steps": [{"t": t, "d": "", "k": "wait"} for t in STEPS[:3 if test else 6]],
                             "log": [], "error": "", "device_msg": "", "backup_id": None, "perm": None, "warn": ""}
    pool.submit(_run_safe, device_id, trigger, username, test, over or {})
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


def _log(device_id: int, msg: str):
    with _lock:
        _state[device_id]["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")


def _kb(n: int) -> str:
    return (f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.1f} KB").replace(".", ",")


def perm_text(p: dict) -> str:
    if not p:
        return ""
    if p.get("level") == "full":
        return f"grupo «{p.get('group')}»: puede crear el .backup y el .rsc"
    if p.get("level") == "rsc":
        return f"grupo «{p.get('group')}»: solo el .rsc (al .backup le falta {', '.join(p.get('missing_backup') or [])})"
    if p.get("level") == "none":
        return f"grupo «{p.get('group')}»: solo lectura para esto (falta {', '.join(p.get('missing_rsc') or p.get('missing_backup') or [])})"
    return "no se pudo leer el grupo ni sus permisos"


def _run_safe(*a):
    try:
        _run(*a)
    except Exception:  # noqa: BLE001 - nunca dejar la corrida «en curso» para siempre
        log.exception("Archivos del MikroTik")
        with _lock:
            st = _state.get(a[0])
            if st and st["status"] == "running":
                st.update(status="error", ended=time.time(), error=st["error"] or "Error interno")


def _run(device_id: int, trigger: str, username: str | None, test: bool, over: dict | None = None):
    from . import prohooks
    from .crypto import decrypt, encrypt
    from .services import add_event, connect, device_lock
    conn = sftp = None
    key = ""
    if not test:
        with SessionLocal() as db:
            dev = db.get(Device, device_id)
            if not dev.mtb_key_enc:  # la primera vez se genera la clave del .backup de este equipo
                dev.mtb_key_enc = encrypt(new_key())
                db.commit()
            key = decrypt(dev.mtb_key_enc)
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        c = cfg(dev)
        cred_id = (over or {}).get("cred_id", c["cred_id"]) if test else c["cred_id"]
        cred = db.get(Credential, cred_id) if cred_id else dev.credential
        name, host, protocol = dev.name, dev.host, dev.protocol
        target = SimpleNamespace(name=dev.name, device_type=dev.device_type, protocol=dev.protocol, host=dev.host,
                                 port=dev.port, credential=cred, enabled=dev.enabled)  # desactivado: connect() no entra
        last = db.scalars(select(MtBackup).where(MtBackup.device_id == device_id).order_by(MtBackup.id.desc()).limit(1)).first()
        last_size = (last.backup_size + last.rsc_size) if last else 0
    lock = device_lock(device_id)
    locked = False
    perm = None
    try:
        if cred is None:
            raise RuntimeError("El equipo no tiene credencial: asigne una o elija otra para los archivos .backup/.rsc.")
        if protocol == "telnet":
            raise RuntimeError("Los archivos .backup/.rsc se traen por SFTP: el equipo se gestiona por Telnet y necesita SSH.")
        site = prohooks.site_of(device_id)
        if site:
            raise RuntimeError(f"Se respalda desde el recolector de {site}: los archivos .backup/.rsc necesitan llegar al "
                               "equipo por SSH desde este servidor.")
        # 1) SSH
        _step(device_id, 0, "run")
        if not lock.acquire(timeout=600):
            raise RuntimeError("El equipo está ocupado con otro respaldo.")
        locked = True
        conn = connect(target)
        ver = ros_version(conn)
        v7 = ver[:1].isdigit() and int(ver.split(".")[0]) >= 7
        _log(device_id, f"ssh {cred.username}@{host} -p {target.port} · RouterOS {ver or '?'}")
        _step(device_id, 0, "ok", f"como {cred.username} · RouterOS {ver or '?'}")
        # 2) permisos, antes de crear nada
        _step(device_id, 1, "run")
        perm = read_perms(conn, cred.username)
        perm["at"] = now().isoformat(timespec="seconds")
        perm["cred_id"] = cred.id
        _set(device_id, perm=perm)
        _log(device_id, f"> :put [/user group get … policy] · {perm['group'] or '?'}: {';'.join(perm['policy']) or '—'}")
        if perm["level"] in ("none", "unknown"):
            _step(device_id, 1, "err", perm_text(perm))
            raise PermError(perm)
        _step(device_id, 1, "ok", perm_text(perm))
        if perm["level"] == "rsc":
            _set(device_id, warn="La credencial solo puede crear el .rsc: el .backup necesita además "
                                 + ", ".join(perm["missing_backup"]) + ".")
        # 3) espacio y restos de un intento anterior
        _step(device_id, 2, "run")
        free = free_space(conn)
        need = max(2 * last_size, 256 * 1024) + 512 * 1024
        left = leftovers(conn)
        if left and not test:  # de una corrida anterior que se cortó (solo archivos «opennccm-bk-…»)
            _cmd(conn, f'/file remove [find where name~"{PREFIX}"]', 60)
            _log(device_id, f"> /file remove (quedaban {left} archivos {PREFIX}… de un intento anterior)")
        if free is None:
            _log(device_id, "el equipo no informó free-hdd-space")
        elif free < need:
            _step(device_id, 2, "err", f"libre {_kb(free)}, hacen falta unos {_kb(need)}")
            raise RuntimeError(f"No hay espacio en el equipo: libre {_kb(free)}, hacen falta unos {_kb(need)}. "
                               "No se creó nada.")
        _step(device_id, 2, "ok", (f"libre {_kb(free)}" if free is not None else "sin dato") + f" · hacen falta ~{_kb(need)}")
        if test:
            _set(device_id, status="ok", ended=time.time())
            _log(device_id, "prueba bien: no se creó ningún archivo")
            _save_perm(device_id, perm)
            return
        # 4) y 5) crear, esperar, traer y comprobar
        import paramiko
        live = live_serial(conn)
        _step(device_id, 3, "run")
        sftp = paramiko.SFTPClient.from_transport(conn.remote_conn.get_transport())
        stamp = now().strftime("%Y%m%d-%H%M%S")
        want_b = perm["level"] == "full"

        def say(m):
            _log(device_id, m)
            if m.startswith("sftp get"):
                _step(device_id, 4, "run", m.split("/")[-1] if "/" in m else m[9:])
        got = make_files(conn, sftp, backup=want_b, rsc=True, key=key, sensitive="sensitive" in perm["policy"],
                         v7=v7, stamp=stamp, say=say)
        _step(device_id, 3, "ok", (f".backup (cifrado {got['backup']['enc']}) y .rsc" if "backup" in got else "solo .rsc")
              + (" con claves (show-sensitive)" if v7 and "sensitive" in perm["policy"] else ""))
        parts = [f".backup {_kb(len(got['backup']['data']))}"] if "backup" in got else []
        parts.append(f".rsc {_kb(len(got['rsc']['data']))}")
        _step(device_id, 4, "ok", " · ".join(parts) + " · SHA-256 y tamaño comprobados")
        # 6) borrar (make_files ya lo hizo) y guardar
        _step(device_id, 5, "run")
        bid = _store(device_id, got, trigger, username, key if "backup" in got else "", ver, live)
        if got.get("left"):
            _set(device_id, warn=f"Quedaron {got['left']} archivos {PREFIX}{stamp} en el equipo: bórrelos a mano.")
        _step(device_id, 5, "ok", ("el equipo quedó limpio" if not got.get("left") else f"¡quedaron {got['left']} en el equipo!")
              + f" · guardado bk-{bid}" + (" con su clave" if "backup" in got else ""))
        with SessionLocal() as db:
            d = db.get(Device, device_id)
            d.mtb_fails, d.mtb_last_at = 0, now()
            d.mtb_perm = json.dumps(perm)
            db.commit()
        _set(device_id, status="ok", ended=time.time(), backup_id=bid)
        from .db import audit
        audit(username or "sistema", "mt_files", f"{name}: bk-{bid} ({' · '.join(parts)}) [{trigger}]")
    except Exception as e:  # noqa: BLE001
        msg = mask(str(e) or repr(e), key)
        dev_msg = mask(e.output if isinstance(e, DeviceError) else "", key)
        with _lock:
            for s in _state[device_id]["steps"]:
                if s["k"] == "run":
                    s["k"] = "err"
        _log(device_id, f"error: {msg}")
        if isinstance(e, PermError):
            _save_perm(device_id, perm, notify=not test, name=name)
        elif not test:
            with SessionLocal() as db:
                d = db.get(Device, device_id)
                d.mtb_fails = (d.mtb_fails or 0) + 1
                fails = d.mtb_fails
                db.commit()
            if fails == FAILS_ALARM:
                add_event("warning", "Archivos .backup/.rsc sin crear",
                          f"{name}: los archivos .backup/.rsc fallaron {fails} veces seguidas. El respaldo de "
                          f"configuración sigue funcionando. Último error: {msg[:300]}", device_id)
        _set(device_id, status="error", ended=time.time(), error=msg, device_msg=dev_msg)
    finally:
        for x in (sftp, conn):
            if x is not None:
                try:
                    x.close() if x is sftp else x.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        if locked:
            lock.release()


class PermError(RuntimeError):
    def __init__(self, perm: dict):
        self.perm = perm
        if perm["level"] == "unknown":
            super().__init__("No se pudieron leer los permisos de la cuenta en el equipo; por seguridad no se creó nada.")
        else:
            super().__init__(f"La credencial es de solo lectura para esto (grupo «{perm['group']}», le falta "
                             f"{', '.join(perm['missing_rsc'] or perm['missing_backup'])}): no se creó nada en el equipo.")


def _save_perm(device_id: int, perm: dict | None, notify: bool = False, name: str = ""):
    """Guarda el último resultado de permisos; si una corrida programada o manual se topa con una cuenta sin permiso,
    se avisa una sola vez (no en cada intento)."""
    if not perm:
        return
    from .services import add_event
    with SessionLocal() as db:
        d = db.get(Device, device_id)
        try:
            old = json.loads(d.mtb_perm or "{}")
        except ValueError:
            old = {}
        d.mtb_perm = json.dumps(perm)
        db.commit()
    if notify and perm["level"] in ("none", "unknown") and (old.get("level") != perm["level"] or old.get("cred_id") != perm.get("cred_id")):
        add_event("warning", "Archivos .backup/.rsc sin crear",
                  f"{name}: la credencial no tiene permiso para crear los archivos .backup/.rsc ({perm_text(perm)}). "
                  "No se creó nada en el equipo. Elija una credencial con permisos en la pestaña Respaldos.", device_id)


def live_serial(conn) -> str | None:
    """La serie del MikroTik que está creando los archivos (la de la ficha puede ser la del aparato anterior). Un CHR
    no tiene."""
    from . import units
    from .drivers import get_profile, parse_inventory
    try:
        text = _cmd(conn, "/system routerboard print", 60)
        return units.clean(parse_inventory(text, get_profile(DTYPE))["serial"])
    except Exception as e:  # noqa: BLE001 - los archivos no dependen de esto
        log.info("serie del MikroTik: %s", e)
        return None


def _store(device_id: int, got: dict, trigger: str, username: str | None, key: str, ver: str,
           serial: str | None = None) -> int:
    from . import units
    from .crypto import encrypt
    folder = DIR / str(device_id)
    folder.mkdir(parents=True, exist_ok=True)
    b, r = got.get("backup"), got["rsc"]
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        row = MtBackup(device_id=device_id, backup_size=len(b["data"]) if b else 0,
                       backup_sha256=hashlib.sha256(b["data"]).hexdigest() if b else None,
                       encryption=b["enc"] if b else None, rsc_size=len(r["data"]),
                       rsc_sha256=hashlib.sha256(r["data"]).hexdigest(), ros_version=(ver or "")[:32], serial=serial,
                       trigger=trigger, username=username, key_enc=encrypt(key) if key else None, created_at=now())
        db.add(row)
        db.flush()
        for kind, item in (("backup", b), ("rsc", r)):
            if item:
                tmp = folder / f".bk-{row.id}.{kind}.tmp"
                tmp.write_bytes(item["data"])
                shutil.move(str(tmp), folder / f"bk-{row.id}.{kind}")
        keep = dev.mtb_keep or KEEP
        old = db.scalars(select(MtBackup).where(MtBackup.device_id == device_id, MtBackup.protected.is_not(True))
                         .order_by(MtBackup.id.desc()).offset(keep)).all()
        if units.differ(serial, dev.serial):  # otro aparato aún sin registrar: no se borra nada del anterior
            old = []
        for o in old:  # solo se guardan los N últimos (los protegidos no cuentan ni se borran: app/fullcopy.py)
            for kind in ("backup", "rsc"):
                (folder / f"bk-{o.id}.{kind}").unlink(missing_ok=True)
            db.delete(o)
        db.commit()
        return row.id


def files(device_id: int) -> list[MtBackup]:
    with SessionLocal() as db:
        return list(db.scalars(select(MtBackup).where(MtBackup.device_id == device_id).order_by(MtBackup.id.desc())))


def path(row: MtBackup, kind: str) -> Path:
    return DIR / str(row.device_id) / f"bk-{row.id}.{kind}"


def due() -> list[int]:
    """Los MikroTik que tocan hoy: semanal (si el último tiene 7 días) o diario; con «Al cambiar» solo si todavía no
    tienen ninguno o el último tiene 30 días (los demás se hacen después de un respaldo con cambios, app/fullcopy.py);
    nunca los «Solo manual», los desactivados, los retirados ni los de Telnet."""
    from .fullcopy import SAFETY_DAYS
    with SessionLocal() as db:
        devs = db.scalars(select(Device).where(Device.device_type == DTYPE, Device.enabled.is_(True),
                                               Device.retired_at.is_(None))).all()
        out = []
        for d in devs:
            freq = d.mtb_freq or "change"
            if d.protocol == "telnet" or freq == "manual" or not (d.mtb_cred_id or d.credential_id):
                continue
            last = d.mtb_last_at
            if freq == "change":
                if not last or now() - last >= timedelta(days=SAFETY_DAYS):
                    out.append(d.id)
            elif freq == "daily" or not last or now() - last >= timedelta(days=6, hours=20):
                out.append(d.id)
        return out


def scheduled():
    """Cada día a las 03:30 (después de las imágenes .fxi): los MikroTik que tocan, de a tres a la vez."""
    from . import prohooks
    for i in due():
        if not prohooks.site_of(i):
            start(i, "scheduled")


# --------------------------------------------------------------------------- web
from fastapi import APIRouter, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

router = APIRouter()
DOC_URL = "https://help.mikrotik.com/docs/spaces/ROS/pages/40992852/Backup"


def context(dev: Device, tl: list[dict]) -> dict:
    """Datos de la pestaña Respaldos de un MikroTik: las dos tarjetas, la tabla (texto y archivos) y los diálogos."""
    with SessionLocal() as db:
        creds = [(c.id, c.name, c.username) for c in db.scalars(select(Credential).order_by(Credential.name))]
        d = db.get(Device, dev.id)
        cred = db.get(Credential, d.mtb_cred_id) if d.mtb_cred_id else d.credential
        c = cfg(d)
    rows = files(dev.id)
    from . import units
    others = {r.id for r in rows if units.other_unit(dev, r)}
    st = state(dev.id)
    return {**c, "others": others, "cred": cred.name if cred else "", "cred_user": cred.username if cred else "", "creds": creds,
            "own_cred": dev.credential.name if dev.credential else "", "rows": rows, "last": rows[0] if rows else None,
            "state": st, "freqs": FREQS, "levels": LEVELS, "doc": DOC_URL, "telnet": dev.protocol == "telnet",
            "perm_text": perm_text(c["perm"]), "on": (c["freq"] != "manual"), "triggers": _triggers(),
            "pending": _pending(dev.id),
            "last_text": next((t for t in tl if t["kind"] == "running"), None)}


def _triggers() -> dict:
    from .fullcopy import TRIGGERS
    return TRIGGERS


def _pending(device_id: int) -> bool:
    from .fullcopy import pending
    return pending(device_id)


def _dev(request: Request, dev_id: int, role: str):
    from .auth import check_device, require
    user = require(request, role)
    check_device(user, dev_id)
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev is not None:
            _ = dev.credential
    if dev is None or dev.device_type != DTYPE:
        return user, None
    return user, dev


@router.post("/devices/{dev_id}/mtfiles/config")
async def save_config(request: Request, dev_id: int):
    from .crypto import encrypt
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "admin")
    if dev is None:
        return go("/devices")
    f = await request.form()
    key = (f.get("key") or "").strip()
    if key and not valid_key(key):
        flash(request, "La clave del .backup debe tener de 8 a 32 letras, números, punto, guion o guion bajo.", "error")
        return go(f"/devices/{dev_id}?mtf=cfg#backups")
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        cred_id = int(f.get("cred_id")) if (f.get("cred_id") or "").isdigit() else None
        if cred_id and db.get(Credential, cred_id) is None:
            cred_id = None
        freq = f.get("freq") if f.get("freq") in FREQS else "change"
        keep = max(1, min(int(f.get("keep")) if (f.get("keep") or "").isdigit() else KEEP, 30))
        if cred_id != d.mtb_cred_id:
            d.mtb_perm = None  # otra credencial: los permisos se vuelven a comprobar
        d.mtb_cred_id, d.mtb_freq, d.mtb_keep = cred_id, freq, keep
        if key:
            d.mtb_key_enc = encrypt(key)
        name = d.name
        db.commit()
    log_action(request, user, "mt_files_config", f"{name}: credencial {cred_id or 'la del equipo'}, {freq}, guardar {keep}"
               + (", clave nueva" if key else ""))  # nunca la clave
    if f.get("go") == "run":
        start(dev_id, "manual", user.username)
        return go(f"/devices/{dev_id}/mtfiles/run")
    flash(request, "Configuración de los archivos .backup/.rsc guardada.")
    return go(f"/devices/{dev_id}#backups")


@router.post("/devices/{dev_id}/mtfiles/test")
async def test_perms(request: Request, dev_id: int):
    from .web import log_action
    user, dev = _dev(request, dev_id, "admin")
    if dev is None:
        return JSONResponse({"error": "no"}, 404)
    f = await request.form()
    over = {"cred_id": int(f.get("cred_id")) if (f.get("cred_id") or "").isdigit() else None}
    ok = start(dev_id, "test", user.username, test=True, over=over)
    log_action(request, user, "mt_files_test", dev.name)
    return JSONResponse({"started": ok})


@router.post("/devices/{dev_id}/mtfiles/run")
def run_now(request: Request, dev_id: int):
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "operator")
    if dev is None:
        return go("/devices")
    if not start(dev_id, "manual", user.username):
        flash(request, "Ya se están creando los archivos de este equipo.", "error")
    log_action(request, user, "mt_files_run", dev.name)
    return go(f"/devices/{dev_id}/mtfiles/run")


@router.get("/devices/{dev_id}/mtfiles/run")
def run_page(request: Request, dev_id: int):
    from .web import go, render
    user, dev = _dev(request, dev_id, "operator")
    if dev is None:
        return go("/devices")
    return render(request, "mtfiles_run.html", user, device=dev, st=state(dev_id), doc=DOC_URL)


@router.get("/devices/{dev_id}/mtfiles/status")
def run_status(request: Request, dev_id: int):
    user, dev = _dev(request, dev_id, "operator")
    if dev is None:
        return JSONResponse({}, 404)
    st = state(dev_id) or {}
    return JSONResponse({k: st.get(k) for k in ("status", "steps", "log", "error", "device_msg", "backup_id", "test",
                                                  "perm", "warn")})


@router.get("/devices/{dev_id}/mtfiles/{row_id}/{kind}")
def download(request: Request, dev_id: int, row_id: int, kind: str):
    from .web import go, log_action
    user, dev = _dev(request, dev_id, "admin")
    with SessionLocal() as db:
        row = db.get(MtBackup, row_id)
    if dev is None or row is None or row.device_id != dev_id or kind not in ("backup", "rsc") or not path(row, kind).exists():
        return go(f"/devices/{dev_id}#backups")
    log_action(request, user, "mt_files_download", f"{dev.name}: bk-{row.id}.{kind}")
    safe = re.sub(r"[^\w.-]+", "_", dev.name)
    # tal cual lo entregó el equipo, con su extensión (nunca en .zip); la clave no va dentro
    return Response(path(row, kind).read_bytes(), media_type="application/octet-stream" if kind == "backup" else "text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{safe}_{row.created_at:%Y%m%d-%H%M}.{kind}"'})


@router.post("/devices/{dev_id}/mtfiles/{row_id}/protect")
async def protect_row(request: Request, dev_id: int, row_id: int):
    """Proteger (no se borra sola) o quitar la protección. Solo administradores; queda en Auditoría."""
    from . import fullcopy
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "admin")
    f = await request.form()
    on = f.get("on") == "1"
    with SessionLocal() as db:
        row = db.get(MtBackup, row_id)
        if dev is None or row is None or row.device_id != dev_id:
            return go(f"/devices/{dev_id}#backups")
        fullcopy.protect(row, on, user.username)
        db.commit()
    log_action(request, user, "mt_files_protect" if on else "mt_files_unprotect", f"{dev.name}: bk-{row_id}")
    flash(request, "Copia protegida: no se borrará sola." if on else "Se quitó la protección de la copia.")
    return go(f"/devices/{dev_id}#backups")


@router.post("/devices/{dev_id}/mtfiles/{row_id}/delete")
def delete_row(request: Request, dev_id: int, row_id: int):
    """Borrar una copia (también una protegida). Solo administradores; queda en Auditoría."""
    from .web import flash, go, log_action
    user, dev = _dev(request, dev_id, "admin")
    with SessionLocal() as db:
        row = db.get(MtBackup, row_id)
        if dev is None or row is None or row.device_id != dev_id:
            return go(f"/devices/{dev_id}#backups")
        was = bool(row.protected)
        for kind in ("backup", "rsc"):
            path(row, kind).unlink(missing_ok=True)
        db.delete(row)
        db.commit()
    log_action(request, user, "mt_files_delete", f"{dev.name}: bk-{row_id}" + (" (protegida)" if was else ""))
    flash(request, f"Copia bk-{row_id} borrada.")
    return go(f"/devices/{dev_id}#backups")


@router.post("/devices/{dev_id}/mtfiles/{row_id}/key")
def reveal_key(request: Request, dev_id: int, row_id: int):
    from .crypto import decrypt
    from .web import log_action
    user, dev = _dev(request, dev_id, "admin")
    with SessionLocal() as db:
        row = db.get(MtBackup, row_id)
    if dev is None or row is None or row.device_id != dev_id or not row.key_enc:
        return JSONResponse({"error": "no"}, 404)
    log_action(request, user, "mt_files_key_view", f"{dev.name}: bk-{row.id}")  # quién vio la clave (no la clave)
    return JSONResponse({"key": decrypt(row.key_enc)})
