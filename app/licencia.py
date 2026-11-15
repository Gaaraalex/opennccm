"""Licencia de los módulos Pro: se activa y se comprueba siempre contra comunidad.opennccm.com, una vez al día.

OpenNCCM es gratis y lo gratis nunca se quita: esto solo dice qué módulos de pago tiene la instalación. Sigue el contrato
compartido con comunidad.opennccm.com (Diseño UX, «CONTRATO-LICENCIAS.md», v1):
  * Instalación: «inst_» + un UUID que se crea una vez y se guarda en la base (Setting install_id).
  * Clave del cliente: OPNC-XXXX-XXXX-XXXX-XXXX (base32 Crockford, sin I, L, O, U). Se guarda cifrada.
  * Licencia: «OPNC1.<kid>.<payload>.<firma>»: payload JSON en base64url sin relleno, firma Ed25519 de los bytes exactos
    del JSON. Se verifica con las claves PÚBLICAS de PUBLIC_KEYS {kid: clave} (más las de NCM_LICENSE_KEYS para pruebas).
  * Estado de cada módulo: activo (ahora ≤ hasta) · gracia (hasta + gracia_dias) · vencido · pausado (sin comprobar
    pasado «comprobar_antes», o la web dijo 409 «en_uso») · no incluido (firma inválida, otra instalación, revocada o
    sin el módulo). Vencido y pausado no borran nada: lo decide cada módulo.
  * Solo con internet (decisión del usuario, 14 nov 2026): no hay archivo de licencia ni activación sin conexión. La
    licencia siempre trae su «instalacion»; si no logra comprobarse antes de «comprobar_antes» (emitida + 7 días), los
    módulos se pausan hasta que vuelva la conexión.
  * Sello contra copias: activar y actualizar devuelven un «sello» que se manda en el siguiente actualizar. Si otro
    servidor con la misma instalación copiada ya lo usó, la web responde 409 «en_uso»: aquí se pausan los módulos y se
    crea un id de instalación nuevo (el viejo queda en licencia_inst_anteriores). Si este es el servidor correcto, el
    cliente libera la instalación anterior en Mi cuenta y vuelve a activar la clave; si es la copia, queda sin licencia.
    El sello se guarda en la base (ajuste cifrado), así un respaldo y restauración del mismo servidor no lo pierde.
  * Red: POST /api/licencias/activar y /actualizar. Solo se envían la clave, el id de instalación, la huella, el sello
    y la versión; nunca configuraciones ni datos de equipos.
  * Huella (contrato de pruebas, «CONTRATO-PRUEBAS.md», sección 1): «h_» + SHA-256 de "opennccm-huella:" + el id de la
    máquina (/etc/machine-id en Linux y en Docker con el del anfitrión montado; MachineGuid en Windows; si no hay, un
    UUID guardado en el volumen de datos). No cambia al reinstalar: la web la usa para «una prueba por módulo en cada
    servidor». Es un hash: no dice de qué máquina se trata.
  * Tipo de cada módulo (contrato de pruebas, sección 7): «compra» y «manual» con 15 días de gracia; «prueba» sin
    gracia (el módulo se pausa el día que vence y no se borra nada).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
CONTACT_URL = "mailto:soporte@opennccm.com"
TERMS_URL = "https://comunidad.opennccm.com/terminos#pro"  # Términos de OpenNCCM Pro
KEY_RE = re.compile(r"^OPNC-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
TIMEOUT = 20
CHECK_DAYS = 7  # si la licencia no trae «comprobar_antes»: emitida (o la última comprobación) + 7 días

# módulos de pago (contrato, sección 1). DISA STIG y NIST 800-53 es gratis: no está aquí.
MODULES = {
    "informes_pro": {"name": N_("Informes profesionales"), "desc": N_("Documento de la red, ejecutivo, auditoría, comparar periodos, envíos y marca"),
                     "limit": "equipos"},
    "recolectores": {"name": N_("Recolectores por sede"), "desc": N_("Agente por sede con cola y avisos"), "limit": "sedes",
                     "soon": True},
    "alta_disponibilidad": {"name": N_("Alta disponibilidad"), "desc": N_("Dos servidores con conmutación automática"),
                            "limit": "pares", "soon": True},
}


def soon(mid: str) -> bool:
    """«Próximamente» en lugar de «Comprar» para los módulos aún no terminados. Detrás de una bandera, apagada de fábrica:
    Pro se lanza con los 3 módulos terminados (decisión del usuario, 14 nov 2026); NCM_PRO_PROXIMAMENTE=1 la enciende."""
    return os.environ.get("NCM_PRO_PROXIMAMENTE", "0") == "1" and MODULES.get(mid, {}).get("soon", False)


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


EN_USO_MSG = N_("Esta licencia se está usando en otro servidor. Si este es el servidor correcto, libera la instalación anterior en Mi cuenta › Licencias y vuelve a activar la clave aquí.")  # noqa: E501


def install_id() -> str:
    """Id de esta instalación: se crea la primera vez y no cambia (va en la licencia a la que queda atada)."""
    iid = get_setting("install_id", "")
    if not iid:
        iid = "inst_" + uuid.uuid4().hex
        set_setting("install_id", iid)
    return iid


def _machine_id() -> str:
    for f in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(f).read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if v:
            return v
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
                v = str(winreg.QueryValueEx(k, "MachineGuid")[0]).strip()
                if v:
                    return v
        except OSError:
            pass
    return ""


def fingerprint() -> str:
    """Huella de este servidor: «h_» + SHA-256 de "opennccm-huella:" + id de la máquina (sin id: un UUID guardado en el
    volumen de datos, que cambia si se borran los datos)."""
    mid = _machine_id()
    if not mid:
        from .config import DATA_DIR
        f = DATA_DIR / "huella.id"
        try:
            mid = f.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            mid = ""
        if not mid:
            mid = uuid.uuid4().hex
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(mid, encoding="ascii")
            except OSError:
                log.warning("No se pudo guardar %s: la huella cambiará al reiniciar", f)
    return "h_" + hashlib.sha256(("opennccm-huella:" + mid).encode()).hexdigest()


class TrialUsed(Exception):
    """409 «prueba_usada»: este servidor ya probó alguno de esos módulos."""

    def __init__(self, mods: list[str], fecha: str):
        self.mods, self.fecha = mods, fecha
        super().__init__(trial_used_text(mods, fecha))


def module_names(mods: list[str]) -> str:
    names = [_(MODULES[m]["name"]) if m in MODULES else m for m in mods]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " " + _("y") + " " + names[-1] if names else ""


def short_date(d: datetime | str | None) -> str:
    """«2 mar 2026» (como el resto de la pantalla de pruebas)."""
    if isinstance(d, str):
        d = _when(d[:10] if len(d) >= 10 else d)
    if not d:
        return ""
    return f"{d.day} {_(SHORT_MONTHS[d.month - 1])} {d.year}"


SHORT_MONTHS = [N_("ene"), N_("feb"), N_("mar"), N_("abr"), N_("may"), N_("jun"), N_("jul"), N_("ago"), N_("sep"), N_("oct"),
                N_("nov"), N_("dic")]


def trial_used_text(mods: list[str], fecha: str) -> str:
    """El texto de la sección 3 del contrato de pruebas."""
    when = short_date(fecha)
    return (_f("Este servidor ya usó una prueba de {0} ({1}). Cada servidor puede probar un módulo una sola vez. Para seguir usándolo, compra la licencia.",  # noqa: E501
               module_names(mods), when) if when else
            _f("Este servidor ya usó una prueba de {0}. Cada servidor puede probar un módulo una sola vez. Para seguir usándolo, compra la licencia.",  # noqa: E501
               module_names(mods)))


UPDATE_MSG = N_("Para usar pruebas, actualiza OpenNCCM a la versión 2.45 o más nueva.")


def _err(r) -> dict:
    try:
        b = r.json()
        return b if isinstance(b, dict) else {}
    except ValueError:
        return {}


def used_trials() -> dict[str, str]:
    """Módulos que este servidor (su huella) ya probó: {módulo: fecha o ""}. Los manda la web en «actualizar» o, sin
    licencia, en POST /api/pruebas/usadas."""
    try:
        v = json.loads(get_setting("licencia_pruebas_usadas", "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _store_used(raw) -> None:
    """pruebas_usadas de la web: [{"modulo": "informes_pro", "fecha": "2026-03-02"}]."""
    if not isinstance(raw, list):
        return
    out = {str(x["modulo"]): str(x.get("fecha") or "") for x in raw if isinstance(x, dict) and x.get("modulo")}
    set_setting("licencia_pruebas_usadas", json.dumps(out))


def waiting_trials() -> dict[str, int]:
    """Pruebas aprobadas de módulos que todavía no tienen paquete para esta versión: {módulo: días}. La web las manda
    en «pruebas_esperando» (fuera del archivo firmado); los días empiezan a contar cuando el módulo sale."""
    try:
        v = json.loads(get_setting("licencia_pruebas_esperando", "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _store_waiting(raw) -> None:
    """Guarda «pruebas_esperando» y avisa en la campana por cada prueba que dejó de esperar y ya llegó en el archivo."""
    if not isinstance(raw, list):
        return
    new = {str(x["modulo"]): int(x.get("dias") or 0) for x in raw if isinstance(x, dict) and x.get("modulo")}
    old = waiting_trials()
    set_setting("licencia_pruebas_esperando", json.dumps(new))
    gone = [m for m in old if m not in new]
    if not gone:
        return
    st = state()
    from .services import add_event
    for mid in gone:
        m = st["modules"].get(mid)
        # solo si ya hay paquete para esta versión: si no, la fila sigue en gris «Disponible pronto»
        if m and m["tipo"] == "prueba" and m["state"] == "activo" and m["until_local"] and not MODULES[mid].get("soon"):
            add_event("warning", "Licencia", _f("Ya puedes usar tu prueba de {0}: vence el {1}.", _(MODULES[mid]["name"]),
                                             short_date(m["until_local"])), notify=False)


def fetch_used() -> None:
    """Sin licencia: pregunta a la web qué módulos ya probó esta huella (al abrir Licencia y módulos). Sin red, sigue
    con lo último que se supo."""
    try:
        r = requests.post(LICENSE_URL + "/api/pruebas/usadas", json={"huella": fingerprint()}, timeout=6)
        if r.status_code == 200:
            b = r.json()
            _store_used(b.get("pruebas_usadas") if isinstance(b, dict) else b)
    except (requests.RequestException, ValueError):
        pass


def in_use() -> None:
    """409 «en_uso»: pausa los módulos y da a este servidor un id de instalación nuevo (guarda el viejo)."""
    old = get_setting("install_id", "")
    if old:
        prev = [x for x in get_setting("licencia_inst_anteriores", "").split(",") if x]
        set_setting("licencia_inst_anteriores", ",".join((prev + [old])[-10:]))
    set_setting("install_id", "inst_" + uuid.uuid4().hex)
    set_setting("licencia_sello", "")
    set_setting("licencia_revocada", "en_uso")
    log.warning("Licencia en uso en otro servidor: instalación %s reemplazada por %s", old, install_id())


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


def checked_at() -> datetime | None:
    """Última comprobación con la web que salió bien (UTC)."""
    return _when(get_setting("licencia_validada", ""))


def check_before(data: dict) -> datetime | None:
    """Fecha límite para la próxima comprobación: «comprobar_antes» de la licencia, o emitida + 7 días."""
    d = _when(data.get("comprobar_antes"))
    if d is None:
        base = _when(data.get("emitida")) or checked_at()
        d = base + timedelta(days=CHECK_DAYS) if base else None
    return d


def state(now: datetime | None = None) -> dict:
    """Estado de la licencia guardada: cliente, problema (si lo hay) y cada módulo con su estado y límites."""
    now = now or datetime.now(timezone.utc)
    token = get_setting("licencia", "")
    checked = checked_at()
    out = {"key": get_setting("licencia_clave", ""), "token": bool(token), "cliente": "", "problem": "", "prueba": False,
           "checked": checked.astimezone().replace(tzinfo=None) if checked else None, "paused": "", "modules": {}}
    data = None
    if token:
        try:
            data = verify(token)
        except LicenseError as e:
            out["problem"] = str(e)
    if get_setting("licencia_revocada", "") == "1":
        out["problem"] = _("La licencia fue revocada. Revise su cuenta en comunidad.opennccm.com.")
        data = None
    elif get_setting("licencia_revocada", "") == "liberada":
        out["problem"] = _("Esta clave se liberó desde Mi cuenta; vuelve a activarla.")
        data = None
    en_uso = get_setting("licencia_revocada", "") == "en_uso"
    if data and data.get("instalacion") != install_id() and not en_uso:  # en_uso: ya se cambió el id de esta instalación
        out["problem"] = _("La licencia es de otra instalación de OpenNCCM.")
        data = None
    if data:
        out["cliente"] = (data.get("cliente") or {}).get("nombre", "")
        deadline = check_before(data)
        if en_uso:
            out["paused"] = "en_uso"
            out["problem"] = _(EN_USO_MSG)
        elif deadline and now > deadline:
            out["paused"] = "sin_comprobar"
            since = (checked or deadline - timedelta(days=CHECK_DAYS)).astimezone()
            out["problem"] = _f("No se pudo comprobar la licencia desde el {0}. Revisa que el servidor llegue a comunidad.opennccm.com.",
                                since.strftime("%Y-%m-%d"))  # como «ago» y «dt» en el resto del sistema
    lic_grace = int((data or {}).get("gracia_dias") or 15)
    lic = {m.get("id"): m for m in (data or {}).get("modulos", []) if isinstance(m, dict)}
    for mid in MODULES:
        m = lic.get(mid)
        until = _when(m.get("hasta")) if m else None
        # tipo de cada módulo (compra, manual o prueba); las licencias de antes lo decían para toda la licencia
        tipo = (m or {}).get("tipo") or ("prueba" if (data or {}).get("prueba") else "compra")
        g = (m or {}).get("gracia_dias")
        grace = timedelta(days=int(g) if isinstance(g, (int, float)) else (0 if tipo == "prueba" else lic_grace))
        if not m or until is None:
            st = "no_incluido"
        elif now <= until:
            st = "activo"
        elif now <= until + grace:
            st = "gracia"
        else:
            st = "vencido"
        if out["paused"] and st in ("activo", "gracia"):
            st = "pausado"  # igual que vencido: no se borra nada y sigue solo al volver a comprobar
        out["modules"][mid] = {"id": mid, "state": st, "until": until, "grace_until": until + grace if until else None,
                               "limits": (m or {}).get("limites") or {}, "days_left": (until - now).days if until else None,
                               "tipo": tipo if m else "",
                               # «quedan 15 días» y «hasta el 30 nov 2026»: en días del calendario de este servidor
                               "days_cal": (until.astimezone().date() - now.astimezone().date()).days if until else None,
                               "until_local": until.astimezone().replace(tzinfo=None) if until else None}
    act = [x for x in out["modules"].values() if x["state"] in ("activo", "gracia")]  # «licencia de prueba»: solo si todo lo activo es prueba
    out["prueba"] = bool(act) and all(x["tipo"] == "prueba" for x in act)
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


def _save(token: str, seal: str | None = None) -> dict:
    data = verify(token)
    if data.get("instalacion") != install_id():  # siempre viene atada a una instalación; nunca null
        raise LicenseError(_("La licencia es de otra instalación de OpenNCCM."))
    set_setting("licencia", token)
    if seal:
        set_setting("licencia_sello", seal)
    set_setting("licencia_revocada", "0")
    set_setting("licencia_validada", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return data


def activate(key: str) -> dict:
    """«Activar» con la clave del cliente: pide la licencia firmada para esta instalación y la guarda."""
    from . import __version__
    key = normalize_key(key)
    if not KEY_RE.match(key):
        raise LicenseError(_("La clave tiene la forma OPNC-XXXX-XXXX-XXXX-XXXX."))
    r = _post("/api/licencias/activar", {"clave": key, "instalacion": install_id(), "huella": fingerprint(), "version": __version__})
    if r.status_code == 409:
        e = _err(r)
        if e.get("error") == "prueba_usada":  # nada de esa clave se activa (contrato de pruebas, sección 3)
            mods = [m for m in e.get("modulos") or [] if isinstance(m, str)]
            set_setting("licencia_prueba_usada", json.dumps({"clave": key, "modulos": mods, "fecha": str(e.get("fecha") or "")}))
            used = used_trials()
            used.update({m: str(e.get("fecha") or "") for m in mods})
            set_setting("licencia_pruebas_usadas", json.dumps(used))
            raise TrialUsed(mods, str(e.get("fecha") or ""))
        if e.get("error") == "actualiza_el_sistema":
            raise LicenseError(_(UPDATE_MSG))
    errs = {404: _("Esa clave no existe. Revise que esté bien escrita."),
            409: _("Esa clave ya está activa en otra instalación. Libérela en Mi cuenta de comunidad.opennccm.com."),
            410: _("Esa clave fue revocada."), 429: _("Demasiados intentos. Espere una hora y vuelva a probar.")}
    if r.status_code in errs:
        raise LicenseError(errs[r.status_code])
    if r.status_code != 200:
        raise LicenseError(_f("comunidad.opennccm.com respondió {0}. Pruebe más tarde.", r.status_code))
    try:
        body = r.json()
        token = body["licencia"]
    except (ValueError, KeyError, TypeError) as e:
        raise LicenseError(_("La respuesta de comunidad.opennccm.com no es válida.")) from e
    data = _save(token, body.get("sello"))
    set_setting("licencia_clave", key)
    set_setting("licencia_prueba_usada", "")
    if "pruebas_usadas" in body:
        _store_used(body["pruebas_usadas"])
    if "pruebas_esperando" in body:
        _store_waiting(body["pruebas_esperando"])
    return data


def refresh() -> str:
    """«Actualizar» (y una vez al día): trae la licencia más nueva. Devuelve "ok", "revocada", "liberada", "en_uso",
    "sin_clave" o el error (sin red se sigue con la guardada hasta «comprobar_antes»)."""
    key = get_setting("licencia_clave", "")
    if not key:
        return "sin_clave"
    if get_setting("licencia_revocada", "") == "en_uso":
        return "en_uso"  # hasta volver a activar la clave en este servidor (con su id nuevo)
    try:
        from . import __version__
        body = {"clave": key, "instalacion": install_id(), "huella": fingerprint(), "version": __version__}
        if get_setting("licencia_sello", ""):
            body["sello"] = get_setting("licencia_sello", "")
        r = _post("/api/licencias/actualizar", body)
        if r.status_code == 410:
            set_setting("licencia_revocada", "1")
            return "revocada"
        if r.status_code == 409:
            try:
                err = r.json().get("error")
            except ValueError:
                err = None
            if err == "en_uso":  # otro servidor usó el sello: copia de esta instalación (contrato, sección 5)
                in_use()
                return "en_uso"
            if err == "actualiza_el_sistema":
                return _(UPDATE_MSG)
            set_setting("licencia_revocada", "liberada")  # «no_activa»: se liberó desde Mi cuenta
            return "liberada"
        if r.status_code != 200:
            return _f("comunidad.opennccm.com respondió {0}. Se sigue con la licencia guardada.", r.status_code)
        out = r.json()
        _save(out["licencia"], out.get("sello"))
        if "pruebas_usadas" in out:
            _store_used(out["pruebas_usadas"])
        if "pruebas_esperando" in out:
            _store_waiting(out["pruebas_esperando"])
        return "ok"
    except (LicenseError, ValueError, KeyError, TypeError) as e:
        return str(e)


def clear() -> None:
    """«Cambiar clave»: quita la clave y la licencia (los módulos quedan como no incluidos; no se borra nada más)."""
    for k in ("licencia", "licencia_clave", "licencia_validada", "licencia_sello", "licencia_prueba_usada", "licencia_pruebas_esperando",
              "licencia_prueba_no_llego"):
        set_setting(k, "")
    set_setting("licencia_revocada", "0")


# --------------------------------------------------------------------------- avisos (tarea diaria)
def daily() -> None:
    """Actualiza la licencia y avisa en la campana 30 días antes de que venza un módulo y al entrar en gracia (una vez
    por módulo y fecha: ajuste licencia_aviso_<módulo>)."""
    if not get_setting("licencia_clave", "") and not get_setting("licencia", ""):
        return
    res = refresh()
    if res not in ("ok", "sin_clave", "revocada", "liberada", "en_uso"):
        log.info("Licencia: %s", res)
    try:
        from . import promod
        from .main import app
        promod.sync(app)  # baja los módulos de la licencia que falten o sean de otra versión de OpenNCCM
    except Exception:  # noqa: BLE001
        log.exception("No se pudieron bajar los módulos Pro")
    from .services import add_event
    st = state()
    for mid, m in st["modules"].items():
        if m["state"] == "no_incluido" or not m["until"]:
            continue
        name = _(MODULES[mid]["name"])
        tag = None
        if m["tipo"] == "prueba":
            if m["state"] == "activo" and m["days_cal"] is not None and m["days_cal"] <= 3:
                tag, msg = "prueba3", _f("La prueba de {0} vence el {1}. Ese día el módulo se pausa; no se borra nada.", name,
                                         short_date(m["until_local"]))
            elif m["state"] == "vencido":
                tag, msg = "prueba_fin", _f("Terminó la prueba de {0}. No se borró nada; para seguir usándolo, compra la licencia.", name)
        elif m["state"] == "activo" and m["days_left"] is not None and m["days_left"] <= 30 \
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
