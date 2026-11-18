"""Pedir una prueba gratis de un módulo Pro desde Administración › Licencia y módulos (contrato «CONTRATO-PRUEBAS.md»
de Diseño UX, secciones 4, 6 y 7). Una sola prueba por módulo en cada servidor: la web la cuenta por la huella.

* Pedir: POST /api/pruebas/pedir con correo, organización, módulos, para qué, origen «sistema», huella, instalación y
  versión. La web responde el número del pedido y un secreto: se guardan cifrados (ajuste «licencia_pedido»).
* En revisión: se pregunta GET /api/pruebas/estado (cabecera X-OPNC-Secreto) cada 30 minutos y al abrir la pantalla,
  hasta 14 días. Las pruebas se aprueban a mano en la web.
* Aprobada: «Activar prueba», un clic. Con clave en este servidor es un «actualizar» al momento (la prueba se agregó
  como módulo a esa licencia); sin clave, se activa la que devuelve el estado.
* Rechazada: se muestra el motivo hasta que se cierra. Cancelar: POST /api/pruebas/cancelar.
Solo se envía lo que se escribe en la ventana, la huella, el id de la instalación y la versión."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import requests

from . import licencia
from .db import get_setting, set_setting
from .i18n import _, _f

log = logging.getLogger("opennccm")
KEY = "licencia_pedido"
POLL_DAYS = 14
TIMEOUT = 15


class TrialError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current() -> dict | None:
    try:
        p = json.loads(get_setting(KEY, "") or "null")
    except ValueError:
        return None
    return p if isinstance(p, dict) and p.get("pedido") else None


def _put(p: dict | None) -> None:
    set_setting(KEY, json.dumps(p) if p else "")


def view() -> dict | None:
    """Lo que dibuja la pantalla: estado, módulos en palabras, cuándo se envió (hora local) y lo que haga falta."""
    p = current()
    if not p:
        return None
    sent = licencia._when(p.get("enviado"))
    return {**p, "names": licencia.module_names(p.get("modulos") or []),
            "sent": sent.astimezone().replace(tzinfo=None) if sent else None,
            "has_key": bool(get_setting("licencia_clave", ""))}


def ask(correo: str, organizacion: str, modulos: list[str], motivo: str) -> dict:
    """Envía el pedido. TrialError con el motivo en palabras si no se puede."""
    from . import __version__
    correo, organizacion, motivo = (correo or "").strip(), " ".join((organizacion or "").split()), (motivo or "").strip()
    mods = [m for m in dict.fromkeys(modulos) if m in licencia.MODULES and not licencia.MODULES[m].get("soon")]  # sin paquete todavía: no se prueba
    if not mods:
        raise TrialError(_("Elige al menos un módulo."))
    if "@" not in correo or " " in correo or len(correo) > 200:
        raise TrialError(_("Escribe un correo válido."))
    if not organizacion:
        raise TrialError(_("Escribe la organización."))
    if current() and current().get("estado") in ("en_revision", "aprobado"):
        raise TrialError(_("Ya hay un pedido de prueba en curso en este servidor."))
    body = {"correo": correo, "organizacion": organizacion[:200], "modulos": mods, "motivo": motivo[:1000], "origen": "sistema",
            "huella": licencia.fingerprint(), "instalacion": licencia.install_id(), "version": __version__}
    try:
        r = requests.post(licencia.LICENSE_URL + "/api/pruebas/pedir", json=body, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TrialError(_f("No se pudo conectar con {0}: {1}", licencia.LICENSE_URL, str(e).splitlines()[0][:160])) from e
    e = licencia._err(r)
    if r.status_code == 409:
        code = e.get("error")
        if code == "prueba_usada":
            used = licencia.used_trials()
            used.update({m: str(e.get("fecha") or "") for m in e.get("modulos") or mods})
            set_setting("licencia_pruebas_usadas", json.dumps(used))
            raise TrialError(licencia.trial_used_text(e.get("modulos") or mods, str(e.get("fecha") or "")))
        if code == "pedido_abierto":
            raise TrialError(_("Ya hay un pedido de prueba en revisión para este servidor o este correo. Espera la respuesta o cancélalo."))
        if code == "licencia_en_otro_servidor":
            raise TrialError(_("Tu equipo ya usa su licencia en otro servidor. Pide la prueba desde ese servidor, o libera la instalación en Mi cuenta › Licencias."))  # noqa: E501
        if code == "actualiza_el_sistema":
            raise TrialError(_(licencia.UPDATE_MSG))
    if r.status_code == 503:
        raise TrialError(_("Por ahora no se aceptan pedidos de prueba. Vuelve a intentarlo más adelante."))
    if r.status_code == 429:
        raise TrialError(_("Demasiados pedidos desde esta conexión. Prueba mañana."))
    if r.status_code not in (200, 201) or not e.get("pedido") or not e.get("secreto"):
        raise TrialError(_f("comunidad.opennccm.com respondió {0}. Pruebe más tarde.", r.status_code))
    dropped = [m for m in e.get("descartados") or [] if isinstance(m, str)]
    if dropped:
        used = licencia.used_trials()
        used.update({m: used.get(m, "") for m in dropped})
        set_setting("licencia_pruebas_usadas", json.dumps(used))
    p = {"pedido": e["pedido"], "secreto": e["secreto"], "estado": e.get("estado") or "en_revision",
         "modulos": [m for m in mods if m not in dropped], "correo": correo, "enviado": _now().isoformat(timespec="seconds"),
         "descartados": dropped, "confirmar": bool(e.get("confirmar_correo"))}
    _put(p)
    return p


def poll(force: bool = False) -> dict | None:
    """Pregunta el estado del pedido en revisión (cada 30 minutos y al abrir la pantalla). Sin red no pasa nada."""
    p = current()
    if p and p.get("estado") == "esperando":
        _check_waiting(p)
        return current()
    if not p or p.get("estado") != "en_revision":
        return p
    sent = licencia._when(p.get("enviado")) or _now()
    if _now() - sent > timedelta(days=POLL_DAYS) and not force:
        return p  # a los 14 días deja de preguntar; el recuadro sigue con «Cancelar pedido»
    try:
        r = requests.get(licencia.LICENSE_URL + "/api/pruebas/estado", params={"pedido": p["pedido"]},
                         headers={"X-OPNC-Secreto": p["secreto"]}, timeout=8)
    except requests.RequestException:
        return p
    if r.status_code == 404:  # la web ya no lo tiene (cancelado desde la cuenta, por ejemplo)
        _put(None)
        return None
    if r.status_code != 200:
        return p
    b = licencia._err(r)
    st = b.get("estado")
    if st == "en_revision":
        if bool(b.get("correo_sin_confirmar")) != bool(p.get("confirmar")):
            p["confirmar"] = bool(b.get("correo_sin_confirmar"))
            _put(p)
    elif st == "cancelado":  # desde la cuenta, o solo a los 7 días sin confirmar el correo
        _put(None)
        return None
    if st == "aprobado":
        p.update({"estado": "aprobado", "clave": b.get("clave") or "", "dias": b.get("dias"), "vence": b.get("vence") or ""})
        _put(p)
        _notify(_f("Tu prueba de {0} fue aprobada: actívala en Licencia y módulos.", licencia.module_names(p["modulos"])))
    elif st == "rechazado":
        p.update({"estado": "rechazado", "motivo": (b.get("motivo") or "")[:500]})
        _put(p)
        _notify(_f("Tu pedido de prueba de {0} no fue aprobado.", licencia.module_names(p["modulos"])))
    return p


def _notify(msg: str) -> None:
    try:
        from .services import add_event
        add_event("warning", "Licencia", msg, notify=False)  # «warning»: la campana no muestra los «info»
    except Exception:  # noqa: BLE001
        log.info("%s", msg)


def poll_job() -> None:
    poll()


def cancel() -> None:
    p = current()
    if not p:
        return
    if p.get("estado") == "en_revision":
        try:
            r = requests.post(licencia.LICENSE_URL + "/api/pruebas/cancelar", json={"pedido": p["pedido"]},
                              headers={"X-OPNC-Secreto": p["secreto"]}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise TrialError(_f("No se pudo conectar con {0}: {1}", licencia.LICENSE_URL, str(e).splitlines()[0][:160])) from e
        if r.status_code not in (200, 204, 404):
            raise TrialError(_f("comunidad.opennccm.com respondió {0}. Pruebe más tarde.", r.status_code))
    _put(None)


def dismiss() -> None:
    """Cerrar el recuadro de «rechazada»."""
    p = current()
    if p and p.get("estado") == "rechazado":
        _put(None)


def activate() -> dict:
    """«Activar prueba»: con clave aquí, actualizar ahora; sin clave, activar la de la prueba."""
    p = current()
    if p and p.get("estado") == "esperando":
        _check_waiting(p)
        return {}
    if not p or p.get("estado") != "aprobado":
        raise TrialError(_("No hay una prueba aprobada para activar."))
    if get_setting("licencia_clave", ""):
        res = licencia.refresh()
        if res != "ok":
            raise TrialError(_(licencia.EN_USO_MSG) if res == "en_uso" else _(licencia.FALTA_SELLO_MSG) if res == "falta_sello" else res if res not in ("revocada", "no_existe", "liberada", "sin_clave")
                             else _("No se pudo actualizar la licencia. Revísala arriba y vuelve a intentarlo."))
        data = licencia.verify(get_setting("licencia", ""))
    else:
        if not p.get("clave"):
            raise TrialError(_("La web todavía no mandó la clave de la prueba. Vuelve a intentarlo en unos minutos."))
        try:
            data = licencia.activate(p["clave"])
        except licencia.TrialUsed:
            _put(None)
            raise
    _after_activate(p)
    return data


def _after_activate(p: dict) -> None:
    """Después de «Activar prueba»: lo que llegó en el archivo ya está; lo que espera paquete deja el recuadro verde
    «el módulo todavía no salió»; y lo que no llegó porque este servidor ya lo probó muestra el texto del 409 en su fila."""
    st = licencia.state()
    waiting = licencia.waiting_trials()
    used = licencia.used_trials()
    arrived = [m for m in p["modulos"] if st["modules"].get(m, {}).get("tipo") == "prueba" and st["modules"][m]["state"] != "no_incluido"]
    wait = [m for m in p["modulos"] if m in waiting and m not in arrived]
    blocked = [m for m in p["modulos"] if m not in arrived and m not in wait and m in used]
    if blocked:
        set_setting("licencia_prueba_no_llego", json.dumps({m: used.get(m, "") for m in blocked}))
    if wait:
        _put({**p, "estado": "esperando", "modulos": wait})
    else:
        _put(None)


def _check_waiting(p: dict) -> None:
    """El recuadro «esperando» se va solo cuando sus módulos llegan en el archivo (o la web deja de mandarlos)."""
    waiting = licencia.waiting_trials()
    left = [m for m in p.get("modulos") or [] if m in waiting]
    if not left:
        _put(None)
    elif left != p.get("modulos"):
        _put({**p, "modulos": left})


def blocked() -> dict[str, str]:
    """Módulos de una prueba activada que no llegaron porque este servidor ya los había probado: {módulo: fecha}."""
    try:
        v = json.loads(get_setting("licencia_prueba_no_llego", "") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}
