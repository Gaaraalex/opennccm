"""Estadística de uso: una vez al día, este servidor avisa a comunidad.opennccm.com que está en uso.

Decisión del usuario (15 nov 2026, contrato «CONTRATO-USO.md» de Diseño UX): activado de fábrica y sin opción para
desactivarlo, pero informado con claridad: al terminar la instalación, en Administración › Sistema, en la
documentación y en «Privacidad y licencias». Nunca se oculta.

Qué se envía, y nada más: {"instalacion": "u_<uuid>", "version": "2.45.0", "pro": true}
  * instalacion: un número al azar propio de la estadística (u_), distinto del inst_ de la licencia Pro; se crea una
    vez y se guarda en la base (ajuste «uso_id»).
  * pro: true si hay al menos un módulo Pro activo o en gracia.
Nunca configuraciones, equipos, direcciones IP de la red, usuarios, contraseñas ni nombres.

Cuándo: una vez al día, a una hora al azar propia de cada servidor (ajuste «uso_hora», para que no lleguen todos
juntos), y al arrancar si pasó más de un día. Si falla (sin internet, bloqueado por el firewall), no reintenta en
bucle, no muestra ningún error y nunca afecta el funcionamiento: se vuelve a intentar al día siguiente."""
from __future__ import annotations

import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from .db import get_setting, set_setting

log = logging.getLogger("opennccm")
URL = os.environ.get("NCM_USO_URL", "https://comunidad.opennccm.com/api/uso/aviso")
TIMEOUT = 10
MIN_GAP = timedelta(hours=20)  # la tarea diaria no repite si ya se avisó (o se intentó) hace menos


def usage_id() -> str:
    """Número de esta instalación para la estadística: «u_» + UUID, se crea la primera vez y no cambia."""
    uid = get_setting("uso_id", "")
    if not uid:
        uid = "u_" + uuid.uuid4().hex
        set_setting("uso_id", uid)
    return uid


def has_pro() -> bool:
    try:
        from . import licencia
        return any(m["state"] in ("activo", "gracia") for m in licencia.state()["modules"].values())
    except Exception:  # noqa: BLE001  (la estadística nunca rompe nada)
        return False


def payload() -> dict:
    """Exactamente lo que se envía (lo mismo que muestra «Ver exactamente qué se envía» en Sistema)."""
    from . import __version__
    return {"instalacion": usage_id(), "version": __version__, "pro": has_pro()}


def _when(key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(get_setting(key, ""))
    except ValueError:
        return None


def last_sent() -> datetime | None:
    """Último aviso recibido por la web (hora local del servidor, sin zona: como el resto del sistema)."""
    d = _when("uso_ultimo")
    return d.astimezone().replace(tzinfo=None) if d else None


def send() -> bool:
    """Un aviso. Nunca lanza una excepción ni deja un error a la vista: si falla, se intenta mañana."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        set_setting("uso_intento", now)
        import requests
        r = requests.post(URL, data=json.dumps(payload()), headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        if 200 <= r.status_code < 300:
            set_setting("uso_ultimo", now)
            return True
        log.debug("Estadística de uso: la web respondió %s", r.status_code)
    except Exception as e:  # noqa: BLE001
        log.debug("Estadística de uso: no se pudo avisar (%s)", str(e).splitlines()[0][:120] if str(e) else type(e).__name__)
    return False


def due(gap: timedelta = timedelta(days=1)) -> bool:
    """¿Toca avisar? Cuenta el último intento (no solo los que salieron bien), así un servidor sin internet no
    insiste: vuelve a probar al día siguiente."""
    last = _when("uso_intento")
    return last is None or datetime.now(timezone.utc) - last >= gap


def daily() -> None:
    """Tarea diaria, a la hora al azar de este servidor."""
    if due(MIN_GAP):
        send()


def at_startup() -> None:
    """Al arrancar: si pasó más de un día desde el último intento (o nunca se avisó)."""
    if due():
        send()


def hour() -> tuple[int, int]:
    """La hora al azar de este servidor para el aviso diario (se elige una vez y se guarda)."""
    hm = get_setting("uso_hora", "")
    try:
        h, m = (int(x) for x in hm.split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    h, m = random.randrange(24), random.randrange(60)
    set_setting("uso_hora", f"{h:02d}:{m:02d}")
    return h, m
