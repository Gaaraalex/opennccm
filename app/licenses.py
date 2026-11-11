"""Vencimiento de los servicios de la licencia guardada de cada equipo (Profile.license_cmd: hoy, la feature key de
WatchGuard, donde cada suscripción lleva su fecha: «AV@Apr-29-2024», «LIVESECURITY@Apr-29-2024»…).

Los servicios se agrupan por fecha. Por cada fecha se avisa una sola vez al quedar 60, 30 y 7 días, y al vencer (evento
y alerta). Se revisa a diario y después de cada respaldo; lo ya avisado se guarda en Ajustes (license_warned_<equipo>)
para no repetirlo tras un reinicio. Una renovación trae otras fechas y vuelve a empezar.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from sqlalchemy import select

from .crypto import try_decrypt
from .db import Device, SessionLocal, get_setting, now, set_setting

log = logging.getLogger("opennccm")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_FEAT = re.compile(r"\b([A-Z][A-Z0-9_]+)@([A-Z][a-z]{2})-(\d{1,2})-(\d{4})\b")
STEPS = (60, 30, 7, 0)  # días que faltan cuando se avisa (0: vencido)
SOON = 60
LIST_DAYS = 90  # el informe muestra lo que vence en este plazo (y lo ya vencido)


def services(text: str) -> dict[str, date]:
    """Cada servicio con fecha y su vencimiento. Si aparece en varias claves (la vieja y la renovada), la fecha mayor."""
    out: dict[str, date] = {}
    for name, mon, day, year in _FEAT.findall(text or ""):
        try:
            d = date(int(year), MONTHS[mon], int(day))
        except (KeyError, ValueError):
            continue
        if name not in out or d > out[name]:
            out[name] = d
    return out


def groups(text: str, today: date | None = None) -> list[dict]:
    """Los servicios agrupados por fecha, de la más próxima a la más lejana: {date, days, names}."""
    today = today or now().date()
    by: dict[date, list[str]] = {}
    for name, d in services(text).items():
        by.setdefault(d, []).append(name)
    return [{"date": d, "days": (d - today).days, "names": sorted(n)} for d, n in sorted(by.items())]


def due_soon(text: str, today: date | None = None) -> bool:
    """Algo vence en 60 días o ya venció: conviene volver a leer la licencia para ver si se renovó."""
    return any(g["days"] <= SOON for g in groups(text, today))


def step(days: int) -> int | None:
    """El aviso que toca con esos días por delante (el más cercano ya alcanzado), o None si falta más de 60."""
    hit = [s for s in STEPS if days <= s]
    return min(hit) if hit else None


def message(name: str, label: str, g: dict) -> tuple[str, str, str]:
    """(severidad, categoría, texto) del aviso de una fecha."""
    when = g["date"].strftime("%d-%m-%Y")
    names = ", ".join(g["names"])
    if g["days"] <= 0:
        return ("major", f"{label}: servicios vencidos",
                f"{name}: venció el {when}: {names}. Hasta renovarlos el equipo no recibe actualizaciones de esos "
                "servicios (firmas de antivirus, IPS, filtrado web…) ni soporte. Al renovar, la feature key nueva se lee "
                "en el próximo respaldo.")
    return ("major" if g["days"] <= 7 else "warning", f"{label}: servicios por vencer",
            f"{name}: el {when} (en {g['days']} días) vence: {names}. Renuévelos antes para no perder las "
            "actualizaciones de esos servicios ni el soporte.")


def check(device_id: int, today: date | None = None) -> int:
    """Avisa lo que toca de un equipo; devuelve cuántos avisos dio."""
    from .drivers import get_profile
    from .services import add_event
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        if dev is None or not dev.license_enc:
            return 0
        text, name, label = try_decrypt(dev.license_enc) or "", dev.name, get_profile(dev.device_type).license_label
    key = f"license_warned_{device_id}"
    try:
        warned = json.loads(get_setting(key, "{}") or "{}")
    except ValueError:
        warned = {}
    current, sent = {}, 0
    for g in groups(text, today):
        iso, s = g["date"].isoformat(), step(g["days"])
        prev = warned.get(iso)
        if prev is not None:
            current[iso] = prev
        if s is None or (prev is not None and prev <= s):
            continue
        sev, cat, msg = message(name, label, g)
        add_event(sev, cat, msg, device_id)
        current[iso] = s
        sent += 1
    if current != warned:  # las fechas que ya no están (renovadas) se olvidan
        set_setting(key, json.dumps(current, sort_keys=True))
    return sent


def scheduled():
    """Una vez al día: los equipos con licencia guardada."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(Device.id).where(Device.license_enc.is_not(None))))
    for i in ids:
        try:
            check(i)
        except Exception as e:  # noqa: BLE001 - un equipo no debe frenar a los demás
            log.warning("Vencimiento de licencia (equipo %s): %s", i, e)


def report(scope: set[int] | None = None, days: int = LIST_DAYS, today: date | None = None) -> list[tuple]:
    """(equipo, modelo, servicio, vence, días) de lo que vence en ese plazo o ya venció, lo más urgente primero."""
    rows = []
    with SessionLocal() as db:
        for d in db.scalars(select(Device).where(Device.retired_at.is_(None), Device.license_enc.is_not(None)).order_by(Device.name)):
            if scope is not None and d.id not in scope:
                continue
            for g in groups(try_decrypt(d.license_enc) or "", today):
                if g["days"] <= days:
                    rows += [(d.name, d.model or "", n, g["date"], g["days"]) for n in g["names"]]
    return sorted(rows, key=lambda r: (r[4], r[0], r[2]))
