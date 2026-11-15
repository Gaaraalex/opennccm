"""Licencia de los módulos Pro, que se verifica en el servidor sin internet y se renueva sola una vez al día.

OpenNCCM es gratis y lo gratis nunca se quita: esto solo dice qué módulos de pago tiene la instalación. Sigue el contrato
compartido con comunidad.opennccm.com (Diseño UX, «CONTRATO-LICENCIAS.md», v1):
  * Instalación: «inst_» + un UUID que se crea una vez y se guarda en la base (Setting install_id).
  * Clave del cliente: OPNC-XXXX-XXXX-XXXX-XXXX (base32 Crockford, sin I, L, O, U). Se guarda cifrada.
  * Licencia: «OPNC1.<kid>.<payload>.<firma>»: payload JSON en base64url sin relleno, firma Ed25519 de los bytes exactos
    del JSON. Se verifica con las claves PÚBLICAS de PUBLIC_KEYS {kid: clave} (más las de NCM_LICENSE_KEYS para pruebas).
  * Estado de cada módulo: activo (ahora ≤ hasta) · gracia (hasta + gracia_dias) · vencido · no incluido (firma
    inválida, otra instalación, revocada o sin el módulo). Vencido no borra nada: lo decide cada módulo.
  * Red: POST /api/licencias/activar y /actualizar. Solo se envían la clave, el id de instalación y la versión; nunca
    configuraciones ni datos de equipos. Si la red falla, se sigue con la licencia guardada.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .db import get_setting, set_setting
from .i18n import N_, _, _f

log = logging.getLogger("opennccm")

# claves públicas de firma de comunidad.opennccm.com: {kid: clave pública Ed25519 en base64url (32 bytes)}. La de
# producción («k1») la entrega la web cuando la genere; para pruebas se agregan con NCM_LICENSE_KEYS="k0=…,k9=…".
PUBLIC_KEYS: dict[str, str] = {}
LICENSE_URL = os.environ.get("NCM_LICENSE_URL", "https://comunidad.opennccm.com").rstrip("/")
BUY_URL = "https://comunidad.opennccm.com/pro"
KEY_RE = re.compile(r"^OPNC-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
TIMEOUT = 20

# módulos de pago (contrato, sección 1). DISA STIG y NIST 800-53 es gratis: no está aquí.
MODULES = {
    "informes_pro": {"name": N_("Informes profesionales"), "desc": N_("Ejecutivo, auditoría, comparar periodos, envíos y marca"),
                     "limit": "equipos"},
    "recolectores": {"name": N_("Recolectores por sede"), "desc": N_("Agente por sede con cola y avisos"), "limit": "sedes"},
    "alta_disponibilidad": {"name": N_("Alta disponibilidad"), "desc": N_("Dos servidores con conmutación automática"),
                            "limit": "pares"},
}


class LicenseError(Exception):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_keys() -> dict[str, str]:
    keys = dict(PUBLIC_KEYS)
    for part in os.environ.get("NCM_LICENSE_KEYS", "").split(","):
        if "=" in part:
            kid, k = part.split("=", 1)
            keys[kid.strip()] = k.strip()
    return keys


def install_id() -> str:
    """Id de esta instalación: se crea la primera vez y no cambia (va en la licencia a la que queda atada)."""
    iid = get_setting("install_id", "")
    if not iid:
        iid = "inst_" + uuid.uuid4().hex
        set_setting("install_id", iid)
    return iid


def normalize_key(key: str) -> str:
    k = re.sub(r"[\s-]", "", (key or "").upper())
    k = k.removeprefix("OPNC")
    k = k.translate(str.maketrans("ILOU", "110V"))  # base32 Crockford: I, L → 1 y O → 0 al escribirla a mano
    return "OPNC-" + "-".join(k[i:i + 4] for i in range(0, 16, 4)) if len(k) == 16 else (key or "").strip().upper()


def mask_key(key: str) -> str:
    """OPNC-7F3K-····-····-Q2LM"""
    p = (key or "").split("-")
    return f"{p[0]}-{p[1]}-····-····-{p[4]}" if len(p) == 5 else "····"


def verify(token: str) -> dict:
    """Comprueba la firma y devuelve el payload. LicenseError si no es una licencia válida."""
    parts = (token or "").strip().split(".")
    if len(parts) != 4 or parts[0] != "OPNC1":
        raise LicenseError(_("No es un archivo de licencia de OpenNCCM."))
    _v, kid, payload_b64, sig_b64 = parts
    pub = public_keys().get(kid)
    if not pub:
        raise LicenseError(_f("La licencia está firmada con una clave que esta versión no conoce ({0}). Actualice OpenNCCM.", kid))
    try:
        raw = _b64d(payload_b64)
        Ed25519PublicKey.from_public_bytes(_b64d(pub)).verify(_b64d(sig_b64), raw)
        data = json.loads(raw.decode("utf-8"))
    except InvalidSignature as e:
        raise LicenseError(_("La firma de la licencia no es válida.")) from e
    except (ValueError, TypeError) as e:
        raise LicenseError(_("La licencia está dañada.")) from e
    if data.get("v") != 1 or not isinstance(data.get("modulos"), list):
        raise LicenseError(_("Esta versión de OpenNCCM no entiende el formato de la licencia. Actualice OpenNCCM."))
    return data


def _when(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def state(now: datetime | None = None) -> dict:
    """Estado de la licencia guardada: cliente, problema (si lo hay) y cada módulo con su estado y límites."""
    now = now or datetime.now(timezone.utc)
    token = get_setting("licencia", "")
    out = {"key": get_setting("licencia_clave", ""), "token": bool(token), "cliente": "", "problem": "", "prueba": False,
           "checked": get_setting("licencia_validada", ""), "modules": {}}
    data = None
    if token:
        try:
            data = verify(token)
        except LicenseError as e:
            out["problem"] = str(e)
    if get_setting("licencia_revocada", "") == "1":
        out["problem"] = _("La licencia fue revocada. Revise su cuenta en comunidad.opennccm.com.")
        data = None
    if data and data.get("instalacion") not in (None, install_id()):
        out["problem"] = _("La licencia es de otra instalación de OpenNCCM.")
        data = None
    if data:
        out["cliente"] = (data.get("cliente") or {}).get("nombre", "")
        out["prueba"] = bool(data.get("prueba"))
    grace = timedelta(days=int((data or {}).get("gracia_dias") or 15))
    lic = {m.get("id"): m for m in (data or {}).get("modulos", []) if isinstance(m, dict)}
    for mid in MODULES:
        m = lic.get(mid)
        until = _when(m.get("hasta")) if m else None
        if not m or until is None:
            st = "no_incluido"
        elif now <= until:
            st = "activo"
        elif now <= until + grace:
            st = "gracia"
        else:
            st = "vencido"
        out["modules"][mid] = {"id": mid, "state": st, "until": until, "grace_until": until + grace if until else None,
                               "limits": (m or {}).get("limites") or {}, "days_left": (until - now).days if until else None}
    return out


def module(mid: str) -> dict:
    return state()["modules"].get(mid, {"id": mid, "state": "no_incluido", "limits": {}})


def usable(mid: str) -> bool:
    """El módulo funciona del todo (activo o en gracia)."""
    return module(mid)["state"] in ("activo", "gracia")


def limit(mid: str, name: str) -> int | None:
    """Límite comprado (equipos, sedes, pares); None o 0 = sin límite."""
    v = module(mid).get("limits", {}).get(name)
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


# --------------------------------------------------------------------------- red
def _post(path: str, body: dict) -> requests.Response:
    try:
        return requests.post(LICENSE_URL + path, json=body, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise LicenseError(_f("No se pudo conectar con {0}: {1}", LICENSE_URL, str(e).splitlines()[0][:160])) from e


def _save(token: str) -> dict:
    data = verify(token)
    if data.get("instalacion") not in (None, install_id()):
        raise LicenseError(_("La licencia es de otra instalación de OpenNCCM."))
    set_setting("licencia", token)
    set_setting("licencia_revocada", "0")
    set_setting("licencia_validada", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return data


def activate(key: str) -> dict:
    """«Activar» con la clave del cliente: pide la licencia firmada para esta instalación y la guarda."""
    from . import __version__
    key = normalize_key(key)
    if not KEY_RE.match(key):
        raise LicenseError(_("La clave tiene la forma OPNC-XXXX-XXXX-XXXX-XXXX."))
    r = _post("/api/licencias/activar", {"clave": key, "instalacion": install_id(), "version": __version__})
    errs = {404: _("Esa clave no existe. Revise que esté bien escrita."),
            409: _("Esa clave ya está activa en otra instalación. Libérela en Mi cuenta de comunidad.opennccm.com."),
            410: _("Esa clave fue revocada."), 429: _("Demasiados intentos. Espere una hora y vuelva a probar.")}
    if r.status_code in errs:
        raise LicenseError(errs[r.status_code])
    if r.status_code != 200:
        raise LicenseError(_f("comunidad.opennccm.com respondió {0}. Pruebe más tarde.", r.status_code))
    try:
        token = r.json()["licencia"]
    except (ValueError, KeyError, TypeError) as e:
        raise LicenseError(_("La respuesta de comunidad.opennccm.com no es válida.")) from e
    data = _save(token)
    set_setting("licencia_clave", key)
    return data


def refresh() -> str:
    """«Actualizar» (y una vez al día): trae la licencia más nueva. Devuelve "ok", "revocada", "sin_clave" o el error."""
    key = get_setting("licencia_clave", "")
    if not key:
        return "sin_clave"
    try:
        r = _post("/api/licencias/actualizar", {"clave": key, "instalacion": install_id()})
        if r.status_code == 410:
            set_setting("licencia_revocada", "1")
            return "revocada"
        if r.status_code != 200:
            return _f("comunidad.opennccm.com respondió {0}. Se sigue con la licencia guardada.", r.status_code)
        _save(r.json()["licencia"])
        return "ok"
    except (LicenseError, ValueError, KeyError, TypeError) as e:
        return str(e)


def upload(text: str) -> dict:
    """Servidor sin internet: el archivo de licencia que se baja de Mi cuenta («instalacion» null o esta)."""
    return _save((text or "").strip())


def clear() -> None:
    """«Cambiar clave»: quita la clave y la licencia (los módulos quedan como no incluidos; no se borra nada más)."""
    for k in ("licencia", "licencia_clave", "licencia_validada"):
        set_setting(k, "")
    set_setting("licencia_revocada", "0")


# --------------------------------------------------------------------------- avisos (tarea diaria)
def daily() -> None:
    """Actualiza la licencia y avisa en la campana 30 días antes de que venza un módulo y al entrar en gracia (una vez
    por módulo y fecha: ajuste licencia_aviso_<módulo>)."""
    if not get_setting("licencia_clave", "") and not get_setting("licencia", ""):
        return
    res = refresh()
    if res not in ("ok", "sin_clave", "revocada"):
        log.info("Licencia: %s", res)
    from .services import add_event
    st = state()
    for mid, m in st["modules"].items():
        if m["state"] == "no_incluido" or not m["until"]:
            continue
        name = _(MODULES[mid]["name"])
        tag = None
        if m["state"] == "activo" and m["days_left"] is not None and m["days_left"] <= 30 \
                and get_setting("licencia_aviso30", "1") == "1":
            tag, msg = "30", _f("{0} vence el {1}. Renuévelo en comunidad.opennccm.com para no pausar sus envíos.", name,
                                m["until"].strftime("%d-%m-%Y"))
        elif m["state"] == "gracia":
            tag, msg = "gracia", _f("{0} venció el {1}: sigue funcionando hasta el {2}.", name, m["until"].strftime("%d-%m-%Y"),
                                    m["grace_until"].strftime("%d-%m-%Y"))
        elif m["state"] == "vencido":
            tag, msg = "vencido", _f("{0} venció. No se borró nada; se pausaron los envíos y la creación de nuevos.", name)
        if tag:
            key = f"{tag}:{m['until'].date().isoformat()}"
            if get_setting(f"licencia_aviso_{mid}", "") != key:
                set_setting(f"licencia_aviso_{mid}", key)
                add_event("warning", "Licencia", msg, notify=False)
