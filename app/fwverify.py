"""Versión instalada leída del propio equipo, sin respaldar, y comprobación después de actualizar el firmware.

- «Comprobar ahora» le pregunta al equipo su versión (los comandos de inventario de su perfil, que solo consultan)
  antes de compararla con la del fabricante: así no hace falta «Respaldar ahora» para ver el cambio.
- Después de actualizar, OpenNCCM espera a que el equipo vuelva, lee la versión y dice si quedó en la nueva. Si no,
  guarda en Eventos y en la solicitud de cambio el final del registro del equipo, que suele decir por qué.
Sirve para todos los tipos de equipo: usa el inventario de cada perfil (app.drivers) y, para el registro, el comando
de la receta «Ver el registro del equipo» de su familia de comandos (app.recipes).
"""
from __future__ import annotations

import logging
import threading
import time

from .crypto import encrypt
from .db import ChangeRequest, Device, SessionLocal
from .drivers import get_profile, parse_inventory

log = logging.getLogger("opennccm")

# registro del equipo cuando su familia no tiene receta «Ver el registro» (o es un comando de varias líneas)
LOG_CMD = {"ros": "/log print without-paging", "airos": "tail -n 80 /var/log/messages", "linux": "journalctl -n 80 --no-pager",
           "edgeos": "show log tail 80", "ios": "show logging", "junos": "show log messages | last 80", "vrp": "display logbuffer",
           "voss": "show logging file tail", "exos": "show log", "fastpath": "show logging buffered", "fortios": "execute log display"}


def read_inventory(device_id: int) -> dict:
    """Lee del equipo su versión, modelo y serie (solo comandos de consulta) y los guarda. Devuelve
    {"ok", "before", "version", "error"}; sin comando de inventario en el perfil no se conecta."""
    from . import services
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        if dev is None:
            return {"ok": False, "error": "missing"}
        prof = get_profile(dev.device_type)
        before = dev.os_version
        try:
            if prof.key in services.HTTP_ONLY:  # SwitchOS: por HTTP
                from . import swos
                from .crypto import decrypt
                cred = (dev.credential.username, decrypt(dev.credential.password_enc) if dev.credential.password_enc else "")
                _cfg, inventory = swos.backup(dev, cred)
            elif not prof.inventory:
                return {"ok": False, "before": before, "version": before, "error": "sin comando de inventario"}
            else:
                with services.connect(dev) as conn:
                    inventory = "\n\n".join(conn.send_command(c, read_timeout=60) for c in prof.inventory.split("\n") if c.strip())
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "before": before, "version": before, "error": str(e) or type(e).__name__}
        if inventory:
            inv = parse_inventory(inventory, prof)
            dev.os_version = inv["os_version"] or dev.os_version
            dev.model = inv["model"] or dev.model
            dev.serial = inv["serial"] or dev.serial
            dev.inventory_raw_enc = encrypt(inventory)
            db.commit()
        return {"ok": True, "before": before, "version": dev.os_version, "error": None}


def log_command(device_type: str) -> str | None:
    """El comando que muestra el registro del equipo: el de la receta de su familia, si es de una línea."""
    from . import consolecore as cc
    from . import recipes
    from .consolecenter import _task_of
    fam = cc.os_of(device_type)
    for r in recipes.BUILTIN:
        if r["os"] == fam and _task_of(r) == "logs":
            c = next((s["c"] for s in r["steps"] if s["c"]), "")
            if c and "\n" not in c and "{{" not in c:
                return c
    return LOG_CMD.get(fam)


def device_log(device_id: int, lines: int = 60) -> str:
    """Las últimas líneas del registro del equipo (solo consulta). Vacío si no hay comando o no responde."""
    from . import services
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        cmd = log_command(dev.device_type) if dev else None
        if not cmd:
            return ""
        try:
            with services.connect(dev) as conn:
                out = conn.send_command(cmd, read_timeout=90)
        except Exception as e:  # noqa: BLE001
            return f"({cmd}: {e or type(e).__name__})"
    rows = [x for x in (out or "").splitlines() if x.strip()]
    return f"$ {cmd}\n" + "\n".join(rows[-lines:])


def same_version(a: str | None, b: str | None) -> bool:
    from .fwupdates import vkey
    return bool(a and b) and (a.strip().lower() == b.strip().lower() or vkey(a) == vkey(b))


def _note(cr_id: int | None, text: str):
    if not cr_id:
        return
    with SessionLocal() as db:
        cr = db.get(ChangeRequest, cr_id)
        if cr:
            cr.result = ((cr.result or "") + "\n\n" + text).strip()
            db.commit()


def verify_after_upgrade(device_id: int, before: str | None, target: str | None, reload: bool, cr_id: int | None = None,
                         wait_first: int | None = None, every: int = 60, give_up: int = 1200, progress=None):
    """Espera a que el equipo responda, lee la versión y avisa si quedó en la nueva o no (con su registro). «progress»
    (app.firmware.Progress) deja el paso en el avance de la solicitud."""
    from . import fwupdates, services
    progress = progress or (lambda *a, **k: None)
    progress("checking", None, "Esperando a que el equipo vuelva para leer su versión…" if reload else "Leyendo la versión…")
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        name = dev.name if dev else str(device_id)
    time.sleep(wait_first if wait_first is not None else (90 if reload else 5))
    start, res = time.monotonic(), None
    while True:
        res = read_inventory(device_id)
        if res.get("ok") or time.monotonic() - start >= give_up:
            break
        time.sleep(every)
    if not res.get("ok"):
        msg = (f"{name} no volvió a responder después de actualizar el firmware ({res.get('error')}). "
               "Revise el equipo; si no vuelve, aparece en Alarmas.")
        services.add_event("major", "Actualización de firmware", msg, device_id)
        _note(cr_id, "Verificación: " + msg)
        services.end_maintenance(device_id)
        progress("failed", None, msg)
        return res
    # el avance de la solicitud dice el resultado apenas se lee la versión (el respaldo y la búsqueda de versiones nuevas
    # de después pueden tardar, y la tanda siguiente no los espera)
    now_v = res.get("version")
    ok = same_version(now_v, target) if target else bool(now_v and not same_version(now_v, before))
    if ok:
        progress("done", None, f"{name} quedó en la versión {now_v}" + (f" (antes {before})." if before else "."))
    else:
        progress("failed" if reload else "done", None,
                 (f"{name} sigue en la versión {now_v or '?'}" + (f"; se esperaba {target}." if target else ".")) if reload
                 else f"Copiada y lista; sigue en {now_v or '?'} hasta que se reinicie.")
    try:  # respaldo después de actualizar (configuración e inventario al día) y la versión disponible de nuevo
        services.backup_device(device_id, "push", "firmware")
    except Exception as e:  # noqa: BLE001
        log.info("Respaldo después de actualizar %s: %s", name, e)
    try:
        fwupdates.check_all([device_id])
    except Exception as e:  # noqa: BLE001
        log.info("Firmware disponible de %s: %s", name, e)
    if ok:
        msg = f"{name} quedó en la versión {now_v}" + (f" (antes {before})." if before else ".")
        services.add_event("info", "Actualización de firmware", msg, device_id)
        _note(cr_id, "Verificación: " + msg)
    else:
        logtxt = device_log(device_id)
        msg = (f"{name} sigue en la versión {now_v or '?'}" + (f"; se esperaba {target}" if target else "") +
               ". La actualización no se aplicó. Registro del equipo:\n" + (logtxt or "(el equipo no tiene un comando de registro conocido)"))
        services.add_event("major", "Actualización de firmware sin aplicar", msg, device_id)
        _note(cr_id, "Verificación: " + msg)
    res["applied"] = ok
    return res


def start_verify(device_id: int, before: str | None, target: str | None, reload: bool, cr_id: int | None = None,
                 progress=None):
    """En segundo plano (puede esperar varios minutos a que el equipo reinicie)."""
    t = threading.Thread(target=verify_after_upgrade, args=(device_id, before, target, reload, cr_id),
                         kwargs={"progress": progress}, daemon=True, name=f"fwverify-{device_id}")
    t.start()
    return t
