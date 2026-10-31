"""Restaurar una copia de seguridad (versión de configuración) en otro equipo del mismo modelo.

El equipo de origen y sus versiones no se tocan. El destino puede ser:

* otro equipo de OpenNCCM (con su credencial),
* un equipo que no está en OpenNCCM, por SSH, con la IP, usuario y contraseña que se escriben en ese momento
  (no se guardan: viven solo en memoria hasta aplicar, como mucho TTL segundos),
* un equipo conectado por cable de consola al PC del administrador: el navegador habla con el puerto serie
  (Web Serial, Chrome/Edge) y el servidor solo valida el modelo y calcula los comandos.

Siempre se valida primero que el destino es del mismo modelo que el origen (inventario leído del destino frente al
del origen). Los comandos son la diferencia entre la configuración actual del destino y la copia (rollback_plan), así
que se borra lo que sobra y se añade lo que falta; la dirección por la que se gestiona el destino no se toca.
"""
import re
import secrets
import threading
import time

from .crypto import decrypt, encrypt
from .db import ConfigVersion, Credential, Device, SessionLocal
from .drivers import HUAWEI, IOS, SET, clean_config, get_profile, parse_inventory
from .i18n import _, _f
from .rollback import rollback_plan

TTL = 15 * 60
_pending: dict[str, dict] = {}   # token -> {"ver_id", "user", "target", "cmds", "at", "state", "out", ...}
_lock = threading.Lock()


def norm_model(m: str | None) -> str:
    """Modelo comparable: sin mayúsculas, espacios ni prefijos de marca («RouterBOARD hEX S» = «hEX S»)."""
    m = re.sub(r"(?i)^(mikrotik|routerboard|cisco|juniper)\s+", "", (m or "").strip())
    return re.sub(r"[\s_]+", "", m).lower()


def same_model(a: str | None, b: str | None) -> bool:
    return bool(norm_model(a)) and norm_model(a) == norm_model(b)


def source(ver_id: int) -> tuple[ConfigVersion, Device, str]:
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        if v is None:
            raise ValueError(_("La versión no existe"))
        dev = v.device
        (dev.name, dev.model)  # carga la relación antes de cerrar la sesión
        return v, dev, decrypt(v.content_enc)


def transient(src: Device, host: str, port: int, username: str, password: str, secret: str = "") -> Device:
    """Equipo que no está en OpenNCCM: mismo tipo que el origen y la credencial escrita (no se guarda en la base)."""
    cred = Credential(name=_("credencial temporal"), username=username, password_enc=encrypt(password),
                      secret_enc=encrypt(secret) if secret else None)
    d = Device(name=host, host=host, port=port, protocol="ssh", device_type=src.device_type)
    d.credential = cred
    return d


def _read(dev: Device) -> tuple[str, str]:
    """(inventario, configuración actual) del destino por SSH."""
    from .services import connect
    prof = get_profile(dev.device_type)
    with connect(dev) as conn:
        inv = "\n\n".join(conn.send_command(c, read_timeout=60) for c in (prof.inventory or "").split("\n") if c.strip())
        running = conn.send_command(prof.backup, read_timeout=180)
    return inv, clean_config(running, prof)


def plan(src: Device, src_text: str, inv: str, current: str, mgmt: str | None) -> dict:
    """Valida el modelo y calcula los comandos. {"ok", "error", "model", "src_model", "cmds", "notes"}."""
    prof = get_profile(src.device_type)
    model = parse_inventory(inv, prof)["model"] if inv else None
    res = {"ok": False, "error": "", "model": model or "", "src_model": src.model or "", "cmds": [], "notes": [],
           "mismatch": bool(model and src.model and not same_model(src.model, model))}
    if not src.model:
        res["error"] = _f("No se conoce el modelo de {0}: haga un respaldo de ese equipo para leer su inventario y vuelva a intentarlo.", src.name)
    elif not model:
        res["error"] = _("No se pudo leer el modelo del equipo de destino. Compruebe que es del mismo fabricante y que el usuario puede ver el inventario.")
    elif not same_model(src.model, model):
        res["error"] = _f("El equipo de destino es un {0} y la copia es de un {1}: solo se puede restaurar en un equipo del mismo modelo.", model, src.model)
    if res["error"]:
        return res
    if len(current.strip()) < 10:
        res["error"] = _("No se pudo leer la configuración actual del equipo de destino.")
        return res
    cmds, notes = rollback_plan(current, src_text, prof, mgmt)
    res["notes"] = notes
    if cmds is None:
        res["error"] = notes[0] if notes else _("Esta plataforma no se puede restaurar por comandos.")
        res["notes"] = []
        return res
    if not cmds:
        res["error"] = _("El equipo de destino ya tiene esta configuración: no hay nada que restaurar.")
        return res
    res.update(ok=True, cmds=cmds)
    return res


def check_ssh(ver_id: int, target: Device, user: str, target_id: int | None = None) -> dict:
    """Conecta al destino, valida el modelo y deja los comandos preparados (token) para aplicarlos."""
    _v, src, text = source(ver_id)
    try:
        inv, current = _read(target)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _f("No se pudo conectar por SSH a {0}: {1}", target.host, e), "model": "",
                "src_model": src.model or "", "cmds": [], "notes": []}
    res = plan(src, text, inv, current, target.host)
    if res["ok"]:
        _gc()
        tok = secrets.token_urlsafe(18)
        with _lock:
            _pending[tok] = {"ver_id": ver_id, "user": user, "target": target, "target_id": target_id,
                             "cmds": res["cmds"], "at": time.time(), "state": "ready", "out": "", "error": ""}
        res["token"] = tok
    return res


def _gc():
    with _lock:
        for k in [k for k, v in _pending.items() if time.time() - v["at"] > TTL and v["state"] != "running"]:
            _pending.pop(k, None)


def get(tok: str, user: str) -> dict | None:
    _gc()
    with _lock:
        job = _pending.get(tok)
    return job if job and job["user"] == user else None


def start(tok: str, user: str) -> bool:
    """Aplica en segundo plano los comandos validados. False si el token no existe, caducó o ya se usó."""
    with _lock:
        job = _pending.get(tok)
        if not job or job["user"] != user or job["state"] != "ready" or time.time() - job["at"] > TTL:
            return False
        job["state"] = "running"
    threading.Thread(target=_run, args=(tok,), daemon=True).start()
    return True


def _run(tok: str):
    from . import services
    job = _pending[tok]
    lock = services.device_lock(job["target_id"]) if job["target_id"] else threading.Lock()
    try:
        with lock:
            if job["target_id"]:
                with SessionLocal() as db:
                    dev = db.get(Device, job["target_id"])
                    dev.credential  # carga la credencial antes de cerrar la sesión
                    out = services.apply_config(dev, job["cmds"])
            else:
                out = services.apply_config(job["target"], job["cmds"])
        job.update(state="done", out=out[-4000:])
        # el destino ya no necesita la contraseña escrita: se olvida
        job["target"] = None
        if job["target_id"]:
            services.backup_device(job["target_id"], "push", job["user"])
    except Exception as e:  # noqa: BLE001
        job.update(state="error", error=str(e)[:4000], target=None)
    job["at"] = time.time()


# --------------------------------------------------------------------------- consola serie (desde el navegador)
SERIAL = {"mikrotik_routeros"}  # además de las familias de sintaxis Cisco, Huawei y Junos (SET)


def serial_supported(device_type: str) -> bool:
    prof = get_profile(device_type)
    return device_type in SERIAL or prof.family in (IOS, HUAWEI) or device_type in ("juniper_junos", "juniper")


def serial_pre(device_type: str) -> list[str]:
    """Órdenes para que la consola no pagine antes de leer el inventario y la configuración."""
    prof = get_profile(device_type)
    if device_type == "mikrotik_routeros":
        return []
    if prof.family == HUAWEI:
        return ["screen-length 0 temporary"]
    if prof.family == SET:
        return ["set cli screen-length 0"]
    return ["terminal length 0"]


def serial_script(device_type: str, cmds: list[str]) -> list[str]:
    """Comandos a escribir por la consola: entrar en configuración, los comandos, salir y guardar."""
    prof = get_profile(device_type)
    if device_type == "mikrotik_routeros":
        return list(cmds)
    if prof.family == HUAWEI:
        return ["system-view", *cmds, "return", "save", "y"]
    if prof.family == SET:
        return ["configure", *cmds, "commit and-quit"]
    return ["configure terminal", *cmds, "end"] + (["write memory"] if prof.save else [])


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r")
_PROMPT = re.compile(r"^\s*(\[[^\]]*\]\s*>|<[^>]*>|[\w.@-]+(\([\w-]+\))?\s*[#>$%])\s*$")


def console_text(text: str, cmd: str) -> str:
    """Salida tal como llega por la consola: sin secuencias de terminal, sin el eco de la orden ni el prompt final."""
    lines = _ANSI.sub("", text).split("\n")
    for i, l in enumerate(lines):
        if cmd and cmd in l:
            lines = lines[i + 1:]
            break
    while lines and (not lines[-1].strip() or _PROMPT.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def serial_check(ver_id: int, inventory: str, config: str) -> dict:
    """El navegador leyó el inventario y la configuración por la consola: validar y devolver el guion."""
    _v, src, text = source(ver_id)
    prof = get_profile(src.device_type)
    res = plan(src, text, inventory or "", clean_config(console_text(config or "", prof.backup), prof), None)
    if res["ok"]:
        res["script"] = serial_script(src.device_type, res["cmds"])
    return res
