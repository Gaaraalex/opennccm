"""Qué aparato físico hay detrás de cada ficha: intercambios y reemplazos.

La ficha es la del lugar (nombre, IP, ubicación) y conserva todo su historial. El aparato se reconoce por su número de
serie, que cada copia guarda (versiones, .fxi, .backup/.rsc):

* ``None``: copia de antes de guardarlo, o de una plataforma que no informa su serie;
* ``""``: la plataforma la informa, pero en ese respaldo no se pudo leer (aparato sin confirmar);
* otro valor: la serie que se leyó.

Cuando la serie cambia (services._store_raw):

* las copias sin serie pasan a ser del aparato anterior y sus últimas copias quedan protegidas;
* se busca la serie nueva en las demás fichas del mismo fabricante para decir de dónde viene;
* lo que era del aparato físico no se hereda: línea base (vuelve si vuelve ese aparato), puertos vistos, licencia,
  permisos comprobados;
* no se revierte a una copia de otra serie, ni al pedirlo, ni al aprobarlo, ni al ejecutarlo (se relee la serie).

En una OLT ZTE C300/C320 la serie es la de la tarjeta de control. Para no confundir esa tarjeta con la OLT entera se
guardan también las series de sus demás tarjetas: si la mayoría sigue igual, solo cambió la de control.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import func, or_, update

from .db import ConfigVersion, Device, Event, FxiImage, MtBackup, get_setting, now, select, set_setting
from .drivers import get_profile, parse_inventory
from .i18n import _, _f

log = logging.getLogger("opennccm.units")

COPIES = (ConfigVersion, MtBackup, FxiImage)
NEW_LABEL = "equipo nuevo · serie "
UNKNOWN = "desconocida"          # la del aparato anterior, en reemplazos viejos de los que no quedó el aviso
RECENT_DAYS = 30                 # días que la ficha muestra el aviso del cambio de aparato
PLACEHOLDERS = {"NONE", "NULL", "UNKNOWN", "NOTSPECIFIED", "NOTAVAILABLE", "DEFAULTSTRING", "SYSTEMSERIALNUMBER",
                "TOBEFILLEDBYO.E.M.", "TOBEFILLEDBYOEM"}


def key(serial: str | None) -> str:
    """Serie comparable (sin mayúsculas ni espacios) o "" si no identifica a ningún aparato."""
    s = re.sub(r"\s+", "", serial or "").upper()
    if len(s) < 4 or not re.search(r"[A-Z0-9]", s) or s in PLACEHOLDERS or set(s) == {"0"}:
        return ""
    return s


def clean(serial: str | None) -> str | None:
    return serial.strip()[:128] if key(serial) else None


def differ(a: str | None, b: str | None) -> bool:
    """Las dos series se conocen y son de aparatos distintos."""
    x, y = key(a), key(b)
    return bool(x and y and x != y)


def stored_serial(dev: Device) -> str | None:
    """La serie que se leyó en el último inventario guardado."""
    from .crypto import try_decrypt
    if not dev.inventory_raw_enc:
        return None
    text = try_decrypt(dev.inventory_raw_enc)
    return clean(parse_inventory(text, get_profile(dev.device_type))["serial"]) if text else None


def _same(column, serial: str):
    return func.upper(column) == serial.strip().upper()


# --------------------------------------------------------------------------- tarjetas de una OLT ZTE
_CARD = re.compile(r"(?m)^\s*\d+\s+\d+\s+(\d+)\s+[A-Z][A-Z0-9]{2,}\b")
MAX_CARDS = 24


def saved_parts(dev: Device) -> dict:
    try:
        return json.loads(dev.unit_parts) if dev.unit_parts else {}
    except ValueError:
        return {}


def read_parts(conn, dev: Device, inventory: str | None, serial: str | None) -> str | None:
    """Series de todas las tarjetas (JSON {ranura: serie}). Solo se leen la primera vez, si cambió la serie o si cambió
    la cantidad de tarjetas; solo comandos «show». Nunca hace fallar el respaldo."""
    if dev.device_type != "zte_olt" or not inventory or not key(serial):
        return None
    slots = list(dict.fromkeys(_CARD.findall(inventory)))[:MAX_CARDS]
    old = saved_parts(dev)
    if not slots or (old and len(old) == len(slots) and not differ(dev.serial, serial)):
        return None
    prof, out = get_profile(dev.device_type), {}
    for slot in slots:
        try:
            text = conn.send_command(f"show card slotno {slot}", read_timeout=30)
        except Exception as e:  # noqa: BLE001
            log.info("tarjeta %s de %s: %s", slot, dev.name, e)
            return None
        out[slot] = clean(parse_inventory(text, prof)["serial"]) or ""
    return json.dumps(out)


def only_control_card(dev: Device, old: str | None, new: str | None, parts: str | None) -> bool:
    """Cambió la serie, pero más de la mitad de las otras tarjetas son las mismas: es la misma OLT."""
    try:
        now_parts = json.loads(parts) if parts else {}
    except ValueError:
        return False
    if not isinstance(now_parts, dict):
        return False
    control = {key(old), key(new)}
    before = {key(s) for s in saved_parts(dev).values()} - control - {""}
    after = {key(str(s)) for s in now_parts.values()} - control - {""}
    return bool(before) and len(before & after) * 2 > len(before)


# --------------------------------------------------------------------------- de dónde viene
def find_source(db, dev: Device, serial: str | None) -> Device | None:
    """Otra ficha del mismo fabricante donde estuvo (o todavía figura) esa serie."""
    if not key(serial):
        return None
    vendor = get_profile(dev.device_type).vendor

    def fits(other):
        return other is not None and other.id != dev.id and get_profile(other.device_type).vendor == vendor
    holders = [d for d in db.scalars(select(Device).where(Device.id != dev.id, _same(Device.serial, serial))) if fits(d)]
    if holders:
        return holders[0] if len(holders) == 1 else None  # repetida en varias fichas: no se adivina
    for dev_id in db.scalars(select(ConfigVersion.device_id)
                             .where(ConfigVersion.device_id != dev.id, _same(ConfigVersion.serial, serial))
                             .order_by(ConfigVersion.id.desc()).limit(20)):
        other = db.get(Device, dev_id)
        if fits(other):
            return other
    return None


def origin_text(dev: Device, source: Device | None, old: str, new: str) -> str:
    if source is None:
        return ""
    if not differ(source.serial, new):
        return (f" Este aparato viene de «{source.name}», que todavía figura con esa serie: respalde «{source.name}» "
                "para saber qué aparato quedó allá.")
    if not differ(source.serial, old):
        return f" Es un intercambio con «{source.name}»: el aparato que estaba aquí ahora está allá."
    return f" Este aparato estuvo antes en «{source.name}»."


# --------------------------------------------------------------------------- al cambiar de aparato
def protect_last(db, dev_id: int, old: str, new: str | None, only: list[int] | None = None) -> bool:
    """Protege la última copia de cada tipo del aparato «old». Devuelve si protegió también una copia completa."""
    note = f"Último del aparato serie {old}"[:120]
    for kind in ("running", "startup"):
        ids = db.scalars(select(ConfigVersion.id).where(ConfigVersion.device_id == dev_id, ConfigVersion.kind == kind,
                                                        _same(ConfigVersion.serial, old))
                         .order_by(ConfigVersion.id.desc()).limit(1)).all()
        for vid in ids + (only or [] if kind == "running" else []):
            db.execute(update(ConfigVersion).where(ConfigVersion.id == vid).values(
                protected=True, label=func.coalesce(func.nullif(ConfigVersion.label, ""), note)))
    files = False
    for model in (MtBackup, FxiImage):
        row = db.scalars(select(model).where(model.device_id == dev_id, _same(model.serial, old))
                         .order_by(model.id.desc()).limit(1)).first()
        if row is not None:
            files = True
            if not row.protected:
                row.protected = True
                row.protect_note = (note + (f" (reemplazado por {new})" if new else ""))[:160]
    return files


def replace(db, dev: Device, old: str, new: str, source: Device | None, prev_id: int | None) -> dict:
    """La ficha pasa a otro aparato. Se llama dentro de la transacción del respaldo, ya con el turno de escritura y
    antes de guardar el inventario, los puertos y la licencia nuevos."""
    db.flush()  # las versiones de este respaldo ya llevan la serie nueva
    old = clean(old) or old
    # la última copia con la serie anterior leída de verdad (las «sin confirmar» de después podrían ser del nuevo)
    sure = db.scalars(select(ConfigVersion.id).where(ConfigVersion.device_id == dev.id, ConfigVersion.kind == "running",
                                                     _same(ConfigVersion.serial, old))
                      .order_by(ConfigVersion.id.desc()).limit(1)).all()
    for model in COPIES:
        db.execute(update(model).where(model.device_id == dev.id, or_(model.serial.is_(None), model.serial == ""))
                   .values(serial=old))
    prev = db.get(ConfigVersion, prev_id) if prev_id else None
    if prev is not None and differ(prev.serial, old):
        prev = None
    files = protect_last(db, dev.id, old, new, sure + ([prev.id] if prev is not None else []))
    try:
        bases = json.loads(dev.unit_baselines) if dev.unit_baselines else {}
    except ValueError:
        bases = {}
    had_base = bool(dev.baseline_version_id)
    if had_base:
        bases[key(old)] = dev.baseline_version_id
    back = bases.pop(key(new), None)
    if back and db.get(ConfigVersion, back) is None:
        back = None
    dev.unit_baselines = json.dumps(bases) if bases else None
    dev.baseline_version_id, dev.baseline_drift = back, False
    dev.port_seen = dev.port_seen_since = None
    dev.license_enc = dev.license_at = None
    dev.os_version = None  # la del anterior; la de este la pone el inventario de este respaldo
    dev.unit_parts = None
    dev.mtb_perm, dev.mtb_fails, dev.fxi_fails = None, 0, 0
    dev.mtb_last_at = dev.fxi_last_at = None
    dev.replaced_at, dev.replaced_serial = now(), old
    dev.moved_from_id = source.id if source is not None else None
    return {"files": files, "baseline": "restored" if back else "cleared" if had_base else ""}


def rename(db, dev_id: int, old: str | None, new: str | None):
    """La misma máquina con otra serie (tarjeta de control, o un lector de inventario corregido): sus copias la siguen."""
    if not differ(old, new):
        return
    for model in COPIES:
        db.execute(update(model).where(model.device_id == dev_id, _same(model.serial, old)).values(serial=clean(new)))


# --------------------------------------------------------------------------- reversión
def other_unit(dev: Device, copy) -> bool:
    return copy is not None and differ(copy.serial, dev.serial)


def rollback_block(dev: Device, version) -> str | None:
    if not other_unit(dev, version):
        return None
    return _f("Esa copia es del aparato con serie {0} y hoy este equipo es el de serie {1}: no se puede revertir a la configuración de otro aparato.",
              version.serial, dev.serial)


def rollback_notes(current, target) -> list[str]:
    if any(v is not None and v.serial == "" for v in (current, target)):
        return [_("No se pudo confirmar el número de serie en una de las dos copias: compruebe que es el mismo aparato antes de aprobar.")]
    return []


def change_block(db, cr, dev: Device) -> str | None:
    """Una solicitud de reversión que ya no vale: el aparato cambió después de pedirla."""
    if not cr.is_rollback:
        return None
    if dev.replaced_at and cr.created_at and dev.replaced_at > cr.created_at:
        return _f("{0} cambió de aparato después de pedirse esta reversión: ya no se puede ejecutar. Recházala y pide otra si hace falta.", dev.name)
    vid = cr.rollback_version_id
    if not vid:  # solicitudes de antes de guardar la versión: está al final del título
        m = re.search(r"#(\d+)$", cr.title or "")
        vid = int(m.group(1)) if m else None
    target = db.get(ConfigVersion, vid) if vid else None
    return rollback_block(dev, target) if target is not None and target.device_id == dev.id else None


def verify_live(device_id: int):
    """Antes de enviar una reversión: el aparato que contesta es el de la ficha. Lanza RuntimeError si no lo es o si no
    se puede confirmar en una plataforma que sí informa su serie."""
    from . import services
    from .db import SessionLocal
    from .drivers import inventory_followup
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        prof = get_profile(dev.device_type)
        if not key(dev.serial) or not prof.inventory:
            return
        expected, must_read = dev.serial, bool(stored_serial(dev))
        with services.connect(dev) as conn:
            text = "\n\n".join(conn.send_command(c, read_timeout=60) for c in prof.inventory.split("\n") if c.strip())
            more = inventory_followup(prof, text)
            if more:
                text += "\n\n" + conn.send_command(more, read_timeout=60)
    live = clean(parse_inventory(text, prof)["serial"])
    if differ(expected, live):
        raise RuntimeError(f"No se envió la reversión: el equipo que contesta tiene la serie {live} y la ficha la "
                           f"{expected} (¿se cambió de lugar?). Respáldelo para registrar el cambio.")
    if must_read and not live:
        raise RuntimeError(f"No se envió la reversión: no se pudo leer la serie del equipo para confirmar que es el "
                           f"mismo aparato ({expected}). Respáldelo e inténtelo de nuevo.")


# --------------------------------------------------------------------------- la ficha
def notice(db, dev: Device, scope: set[int] | None = None) -> dict | None:
    """El aviso de la ficha. Los nombres de otras fichas, solo si el usuario puede verlas."""
    def visible(d):
        return d if d is not None and (scope is None or d.id in scope) else None
    out = None
    if dev.replaced_at and now() - dev.replaced_at < timedelta(days=RECENT_DAYS):
        src = visible(db.get(Device, dev.moved_from_id) if dev.moved_from_id else None)
        kind = ""
        if src is not None:
            kind = ("stale" if not differ(src.serial, dev.serial)
                    else "swap" if not differ(src.serial, dev.replaced_serial) else "from")
        out = {"old": dev.replaced_serial, "new": dev.serial, "at": dev.replaced_at, "src": src, "kind": kind}
    if key(dev.serial):  # su aparato ya apareció en otra ficha y esta todavía no se respaldó
        gone = db.scalars(select(Device).where(Device.moved_from_id == dev.id, _same(Device.serial, dev.serial))
                          .limit(1)).first()
        if gone is not None and (not dev.replaced_at or (gone.replaced_at and gone.replaced_at > dev.replaced_at)):
            out = {"old": None, "new": dev.serial, "at": gone.replaced_at, "src": visible(gone), "kind": "gone"}
    return out


# --------------------------------------------------------------------------- reemplazos de antes de guardar la serie
_EVENT = re.compile(r"cambió el número de serie (.+?) → (.+?)\. Es otro")


def migrate():
    """Una sola vez al actualizar: los reemplazos que ya se habían detectado (aviso «Equipo reemplazado» o la etiqueta
    «equipo nuevo · serie …») reparten las series entre las copias y protegen las últimas de cada aparato."""
    from .db import SessionLocal
    if get_setting("units_migrated") == "1":
        return
    with SessionLocal() as db:
        marks: dict[int, list[dict]] = {}
        for vid, dev_id, at, label in db.execute(
                select(ConfigVersion.id, ConfigVersion.device_id, ConfigVersion.created_at, ConfigVersion.label)
                .where(ConfigVersion.label.like(NEW_LABEL + "%")).order_by(ConfigVersion.id)):
            marks.setdefault(dev_id, []).append({"at": at, "vid": vid, "old": None, "new": label[len(NEW_LABEL):].strip()})
        for ev in db.scalars(select(Event).where(Event.category == "Equipo reemplazado", Event.device_id.is_not(None))
                             .order_by(Event.id)):
            m = _EVENT.search(ev.message or "")
            if not m:
                continue
            rows = marks.setdefault(ev.device_id, [])
            old, new = m.group(1), m.group(2)
            # el aviso sale al terminar el respaldo que guardó la versión etiquetada
            hit = next((r for r in rows if r["old"] is None and key(r["new"]) == key(new)
                        and timedelta(0) <= ev.created_at - r["at"] < timedelta(hours=1)), None)
            if hit:
                hit["old"] = old
            else:
                rows.append({"at": ev.created_at, "vid": None, "old": old, "new": new})
        done = []
        for dev_id, rows in marks.items():
            dev = db.get(Device, dev_id)
            if dev is None:
                continue
            rows.sort(key=lambda r: r["at"])
            for i, r in enumerate(rows):
                r["old"] = r["old"] or (rows[i - 1]["new"] if i else UNKNOWN)
                for model in COPIES:
                    # las fechas se guardan al segundo: entre versiones manda el orden en que se guardaron (la etiquetada
                    # es la primera del aparato nuevo); las demás copias de ese mismo segundo son de antes del cambio
                    before = (model.id < r["vid"] if model is ConfigVersion and r["vid"] else model.created_at <= r["at"])
                    db.execute(update(model).where(model.device_id == dev_id, model.serial.is_(None), before)
                               .values(serial=clean(r["old"]) or UNKNOWN))
            last = rows[-1]
            if key(last["new"]) and not differ(dev.serial, last["new"]):
                for model in COPIES:
                    db.execute(update(model).where(model.device_id == dev_id, model.serial.is_(None))
                               .values(serial=clean(last["new"])))
            for r in rows:
                protect_last(db, dev_id, clean(r["old"]) or UNKNOWN, r["new"])
            dev.replaced_at, dev.replaced_serial = last["at"], last["old"][:128]
            base = db.get(ConfigVersion, dev.baseline_version_id) if dev.baseline_version_id else None
            if base is not None and differ(base.serial, dev.serial):
                dev.unit_baselines = json.dumps({key(base.serial): base.id})
                dev.baseline_version_id, dev.baseline_drift = None, False
            if dev.port_seen_since and dev.port_seen_since < last["at"]:
                dev.port_seen = dev.port_seen_since = None
            if dev.license_at and dev.license_at < last["at"]:
                dev.license_enc = dev.license_at = None
            done.append(dev)
        db.flush()
        for dev in done:  # con todas las series repartidas ya se sabe de dónde vino cada uno
            src = find_source(db, dev, dev.serial)
            dev.moved_from_id = src.id if src is not None else None
        db.commit()
    set_setting("units_migrated", "1")
