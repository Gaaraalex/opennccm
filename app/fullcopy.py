"""Copias completas de los equipos: la imagen .fxi de WatchGuard (app/fxi.py) y el .backup/.rsc de MikroTik
(app/mtfiles.py). Aquí se decide CUÁNDO se hacen y qué copias quedan protegidas.

«Al cambiar» (la frecuencia por omisión desde la 2.45.5): después de cada respaldo de configuración,
  * si el respaldo encontró cambios (o es el primero, o cambió el firmware) → se hacen las copias completas;
  * si no hubo cambios, no se hacen, salvo que el equipo todavía no tenga ninguna (la primera) o la última tenga
    30 días (la de seguridad: el .backup y el .fxi guardan contraseñas, certificados y la feature key, que el respaldo
    de texto no ve cambiar);
  * nunca dos en menos de 10 minutos: varios cambios seguidos dan una sola copia, 10 minutos después de la anterior.
«Semanal», «Diario» y «Solo manual» siguen como antes (la corrida de la noche).

Copias protegidas: cuando un respaldo trae otra versión de firmware (RouterOS 7.20 → 7.25, Fireware 12.10 → 12.11),
la última copia de texto de la versión anterior queda etiquetada («Antes de firmware …») y la última copia completa
de esa versión queda protegida. Ninguna se borra sola (ni por «Guardar los N últimos» ni por la retención de
versiones): un equipo de reemplazo puede venir con otra versión y conviene restaurar la copia de la misma. Solo un
administrador las quita."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import timedelta

from .db import ConfigVersion, Device, FxiImage, MtBackup, SessionLocal, now, select
from .i18n import N_

log = logging.getLogger("opennccm.fullcopy")

GAP = 600            # segundos mínimos entre dos copias completas del mismo equipo
SAFETY_DAYS = 30     # con «Al cambiar», una de seguridad si la última tiene estos días
PERM_RETRY = 86400   # con una cuenta sin permiso, se vuelve a probar como mucho una vez al día (salvo cambios)
_last: dict[int, float] = {}      # cuándo se pidió la última copia completa de cada equipo (en este proceso)
_pending: dict[int, threading.Timer] = {}
_lock = threading.Lock()


def module_for(device_type: str):
    if device_type == "watchguard_fireware":
        from . import fxi
        return fxi
    if device_type == "mikrotik_routeros":
        from . import mtfiles
        return mtfiles
    return None


def fw_key(v: str | None) -> str:
    """Versión de firmware comparable: «7.25.1 (stable)» y «7.25.1» son la misma."""
    v = (v or "").strip()
    m = re.search(r"\d+(?:\.\d+)+(?:[-_.]?[A-Za-z]+\d*)?", v)
    return (m.group(0) if m else v).lower()


# --------------------------------------------------------------------------- copias protegidas (firmware)
def on_firmware_change(db, dev: Device, old: str, new: str) -> dict:
    """El respaldo de este equipo trae otra versión de firmware: se etiqueta la última copia de texto de la versión
    anterior y se protege su última copia completa. Se llama dentro de la transacción del respaldo."""
    note = f"Antes de firmware {new} (era {old})"[:160]
    out = {"label": None, "files": None}
    db.flush()  # la versión recién agregada en este respaldo (si cambió algo) ya es de la versión nueva
    prev = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev.id, ConfigVersion.kind == "running")
                      .order_by(ConfigVersion.id.desc()).limit(5)).all()
    v = next((x for x in prev if fw_key(x.fw_version) != fw_key(new)), None)
    if v is not None:
        if not v.label:
            v.label = note[:128]
        out["label"] = v.id
    if dev.device_type == "mikrotik_routeros":
        rows = db.scalars(select(MtBackup).where(MtBackup.device_id == dev.id).order_by(MtBackup.id.desc())).all()
        target = next((r for r in rows if fw_key(r.ros_version) != fw_key(new)), None)
    elif dev.device_type == "watchguard_fireware":
        rows = db.scalars(select(FxiImage).where(FxiImage.device_id == dev.id).order_by(FxiImage.id.desc())).all()
        target = next((r for r in rows if fw_key(r.fw_version or old) != fw_key(new)), None)
    else:
        target = None
    if target is not None and not target.protected:
        target.protected, target.protect_note = True, note
        out["files"] = target.id
    return out


def protect(row, on: bool, who: str, note: str | None = None):
    row.protected = on
    row.protect_note = (note or f"Protegida a mano por {who}")[:160] if on else None


# --------------------------------------------------------------------------- «Al cambiar»
def _info(dev: Device, mod) -> dict:
    with SessionLocal() as db:
        if mod.__name__.endswith("fxi"):
            last = db.scalars(select(FxiImage).where(FxiImage.device_id == dev.id).order_by(FxiImage.id.desc()).limit(1)).first()
            return {"freq": dev.fxi_freq or "change", "ready": bool(dev.fxi_cred_id and dev.fxi_key_enc),
                    "last": last.created_at if last else None, "attempt": dev.fxi_last_at, "perm": None}
        last = db.scalars(select(MtBackup).where(MtBackup.device_id == dev.id).order_by(MtBackup.id.desc()).limit(1)).first()
        import json
        try:
            perm = json.loads(dev.mtb_perm) if dev.mtb_perm else None
        except ValueError:
            perm = None
        ready = bool((dev.mtb_cred_id or dev.credential_id) and dev.protocol != "telnet")
        return {"freq": dev.mtb_freq or "change", "ready": ready, "last": last.created_at if last else None,
                "attempt": dev.mtb_last_at, "perm": perm, "cred": dev.mtb_cred_id or dev.credential_id}


def reason(dev: Device, result: str, fw_changed: bool) -> str | None:
    """Por qué toca una copia completa después de este respaldo («cambio», «primera», «seguridad», «firmware») o None."""
    mod = module_for(dev.device_type)
    if mod is None or not dev.enabled or dev.retired_at is not None:
        return None
    i = _info(dev, mod)
    if i["freq"] != "change" or not i["ready"]:
        return None
    if fw_changed:
        why = "firmware"
    elif result in ("changed", "first"):
        why = "cambio"
    elif i["last"] is None:
        why = "primera"
    elif now() - i["last"] >= timedelta(days=SAFETY_DAYS):
        why = "seguridad"
    else:
        return None
    p = i["perm"]
    if why in ("primera", "seguridad") and p and p.get("level") in ("none", "unknown") and p.get("cred_id") == i.get("cred"):
        try:  # cuenta sin permiso: no se insiste en cada respaldo, como mucho una vez al día
            from datetime import datetime
            if (now() - datetime.fromisoformat(p["at"])).total_seconds() < PERM_RETRY:
                return None
        except (KeyError, ValueError):
            pass
    return why


def after_backup(device_id: int, result: str, fw_changed: bool = False):
    """Lo llama services.backup_device después de cada respaldo hecho desde aquí. Nunca hace fallar el respaldo."""
    try:
        with SessionLocal() as db:
            dev = db.get(Device, device_id)
            if dev is None:
                return
            _ = dev.credential
            db.expunge(dev)
        if result not in ("changed", "first", "unchanged"):
            return
        why = reason(dev, result, fw_changed)
        if why:
            request(device_id, why)
    except Exception:  # noqa: BLE001
        log.exception("Copia completa después del respaldo de %s", device_id)


def request(device_id: int, why: str):
    """Pide la copia completa ahora o, si la anterior fue hace menos de 10 minutos, cuando se cumplan (una sola vez
    aunque lleguen varios cambios)."""
    with _lock:
        wait = GAP - (time.monotonic() - _last.get(device_id, -1e9))
        if wait > 0:
            if device_id not in _pending:
                t = threading.Timer(wait, _fire, args=(device_id, why))
                t.daemon = True
                _pending[device_id] = t
                t.start()
                log.info("Copia completa de %s (%s) en %.0f s", device_id, why, wait)
            return
        _last[device_id] = time.monotonic()
    _start(device_id, why)


def _fire(device_id: int, why: str):
    with _lock:
        _pending.pop(device_id, None)
        _last[device_id] = time.monotonic()
    _start(device_id, why)


def _start(device_id: int, why: str):
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        mod = module_for(dev.device_type) if dev else None
    if mod is None:
        return
    if not mod.start(device_id, f"change:{why}"):  # ya hay una en curso: se intenta otra vez dentro de 10 minutos
        with _lock:
            _last[device_id] = time.monotonic()
        request(device_id, why)


def pending(device_id: int) -> bool:
    return device_id in _pending


TRIGGERS = {"manual": N_("Manual"), "scheduled": N_("Programado"), "change:cambio": N_("Al cambiar"),
            "change:primera": N_("La primera"), "change:seguridad": N_("De seguridad (30 días)"),
            "change:firmware": N_("Cambio de firmware")}
