"""Puertas de los módulos Pro en OpenNCCM gratis: Administración › Licencia y módulos, y las páginas de cada módulo
(Informes › Profesionales, Administración › Recolectores y › Alta disponibilidad). Diseño de «Diseño UX» aprobado por
el usuario (tableros Pro 1 a Pro 10).

Sin licencia, cada página muestra qué incluye el módulo, un precio y «Tengo una clave» / «Ver planes y precios». Nunca
hay ventanas que aparezcan solas y lo gratis no cambia. Lo que hace cada módulo vive en el repositorio privado
opennccm-pro y se instala aparte (no viaja en este código)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from . import licencia
from .auth import require
from .config import DATA_DIR
from .db import get_setting, set_setting
from .i18n import N_, _, _f
from .web import flash, go, log_action, render

router = APIRouter()

# lo que se ve sin licencia (Pro 5 a 8). Sin precios: el usuario los cambia desde el /admin de la web y aquí quedarían
# otros; «Ver planes y precios» lleva a la página con los vigentes.
OFFERS = {
    "informes_pro": {
        "title": N_("Informes profesionales"),
        "lead": N_("Informes listos para gerencia y auditores, con tu marca, que se envían solos por correo."),
        "items": [N_("Documento de la red en PDF y Word: inventario, mapa, VLAN e IP, enlaces y cumplimiento"),
                  N_("Informe ejecutivo mensual con tendencia contra el mes anterior"),
                  N_("Auditoría de cumplimiento por norma (PCI DSS, ISO 27001, CIS) con evidencias y firma"),
                  N_("Envío programado por correo: diario, semanal o mensual"),
                  N_("Comparar periodos y plantilla con tu logo y colores")],
    },
    "recolectores": {
        "title": N_("Recolectores por sede"),
        "lead": N_("Instala un agente en cada sede: respalda los equipos de su red y envía las copias al servidor central, aunque la sede no tenga acceso directo."),
        "items": [N_("Agente liviano para Linux o Windows, uno por sede"), N_("Envía por HTTPS con un token por sede"),
                  N_("Cola local: si se corta el enlace, no se pierde nada"), N_("Avisos cuando una sede deja de reportar")],
    },
    "alta_disponibilidad": {
        "title": N_("Alta disponibilidad"),
        "lead": N_("Dos servidores OpenNCCM: uno trabaja y el otro espera listo. Si el primero falla, el segundo sigue solo, con todos los datos."),
        "items": [N_("Latido entre los dos servidores"), N_("Réplica continua de PostgreSQL"),
                  N_("Conmutación automática o manual"), N_("Historial de cada conmutación")],
    },
}
# el Documento de la red era gratis hasta esta versión: lo ya generado se sigue viendo y descargando sin licencia
NETDOC_PRO_SINCE = "2.45.0"
NETDOC_DIR = DATA_DIR / "netdocs"
STATE_LABEL = {"activo": N_("Activo"), "gracia": N_("En gracia"), "vencido": N_("Vencido"), "pausado": N_("Pausado"),
               "no_incluido": N_("No incluido")}


def show_locked() -> bool:
    """Preferencia «Mostrar las funciones Pro que no tengo, con su etiqueta PRO» (de fábrica, sí)."""
    return get_setting("licencia_mostrar_pro", "1") == "1"


def visible(mid: str) -> bool:
    """La entrada del módulo (pestaña o página) se muestra: lo tiene, o se quieren ver las que no se tienen."""
    return show_locked() or licencia.module(mid)["state"] != "no_incluido"


def _module_page(request: Request, mid: str, tab_template: str):
    user = require(request, "admin" if mid != "informes_pro" else "viewer")
    m = licencia.module(mid)
    from . import promod
    page = promod.page(mid)
    if page is not None and m["state"] != "no_incluido":  # el módulo instalado dibuja su página
        out = page(request, user, m)
        if out is not None:
            return out
    return render(request, tab_template, user, mid=mid, m=m, offer=OFFERS[mid], buy_url=licencia.BUY_URL,
                  state_label=STATE_LABEL, soon=licencia.soon(mid))


def old_netdocs(user) -> list[dict]:
    """Documentos de la red generados antes (cuando era gratis) que este usuario puede ver: los suyos, o todos si es
    administrador. Solo leer y descargar: generar uno nuevo es del módulo Informes profesionales."""
    import json
    out = []
    for p in NETDOC_DIR.glob("*.json") if NETDOC_DIR.is_dir() else []:
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if user.role == "admin" or m.get("user") == user.username:
            from datetime import datetime
            try:
                m["when"] = datetime.fromisoformat(m.get("created", ""))
            except ValueError:
                m["when"] = None
            out.append(m)
    return sorted(out, key=lambda m: m.get("created", ""), reverse=True)


@router.get("/reports/netdoc-anteriores")
def old_netdocs_page(request: Request):
    user = require(request)
    return render(request, "netdoc_old.html", user, docs=old_netdocs(user), since=NETDOC_PRO_SINCE)


@router.get("/reports/netdoc-anteriores/{doc_id}/file")
def old_netdoc_file(request: Request, doc_id: str):
    import re

    from fastapi.responses import Response

    from .auth import Forbidden
    user = require(request)
    m = next((d for d in old_netdocs(user) if d.get("id") == doc_id), None)
    if m is None or not re.fullmatch(r"[0-9]{14}-[0-9a-f]{6}", doc_id) or m.get("fmt") not in ("pdf", "docx"):
        raise Forbidden()
    path = NETDOC_DIR / f"{doc_id}.{m['fmt']}"
    if not path.is_file():
        raise Forbidden()
    log_action(request, user, "netdoc_download", m.get("file", doc_id))
    mt = "application/pdf" if m["fmt"] == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return Response(path.read_bytes(), media_type=mt, headers={"Content-Disposition": f'attachment; filename="{m.get("file", doc_id)}"'})


@router.get("/reports/pro")
def reports_pro(request: Request):
    return _module_page(request, "informes_pro", "pro_reports.html")


@router.get("/recolectores")
def collectors(request: Request):
    return _module_page(request, "recolectores", "pro_admin.html")


@router.get("/alta-disponibilidad")
def high_availability(request: Request):
    return _module_page(request, "alta_disponibilidad", "pro_admin.html")


def _rows(st: dict, ped: dict | None, used: dict) -> dict:
    """Qué muestra cada módulo (Pruebas 5 a 9): prueba activa o terminada, pedida, aprobada, esperando paquete, ya probada
    o «Probar gratis». Los módulos sin paquete para esta versión (marcados «soon» en licencia.MODULES) no ofrecen prueba."""
    from . import pruebas
    out = {}
    waiting = licencia.waiting_trials()
    blocked = pruebas.blocked()
    for mid, m in st["modules"].items():
        in_ped = ped and mid in (ped.get("modulos") or [])
        if mid in waiting and m["state"] == "no_incluido":
            out[mid] = {"kind": "waiting", "days": waiting[mid], "used_on": ""}
            continue
        if mid in blocked and m["state"] == "no_incluido":
            out[mid] = {"kind": "blocked", "text": licencia.trial_used_text([mid], blocked[mid]), "used_on": ""}
            continue
        if m["tipo"] == "prueba" and m["state"] == "activo":
            kind = "trial"
            from . import promod
            if licencia.MODULES[mid].get("soon") or promod.info(mid).get("problem"):
                kind = "trial_nopkg"  # la prueba está en el archivo pero no hay paquete para esta versión: gris, nunca rojo
        elif m["tipo"] == "prueba" and m["state"] in ("vencido", "pausado"):
            kind = "trial_over" if m["state"] == "vencido" else "paused"
        elif m["state"] != "no_incluido":
            kind = "paused" if m["state"] == "pausado" else "owned"
        elif in_ped and ped["estado"] == "en_revision":
            kind = "pending"
        elif in_ped and ped["estado"] == "aprobado":
            kind = "approved"
        elif licencia.soon(mid):
            kind = "soon"
        elif mid in used:
            kind = "used"
        elif licencia.MODULES[mid].get("soon"):
            kind = "nopkg"  # sin paquete para esta versión todavía: se puede comprar, no probar
        else:
            kind = "free"
        out[mid] = {"kind": kind, "used_on": licencia.short_date(used.get(mid)) if used.get(mid) else ""}
    return out


@router.get("/license")
def license_page(request: Request):
    user = require(request, "admin")
    from . import pruebas
    if not get_setting("licencia_clave", ""):
        licencia.fetch_used()  # sin licencia: qué módulos ya probó este servidor (con licencia lo trae «actualizar»)
    pruebas.poll()
    st = licencia.state()
    ped = pruebas.view()
    used = licencia.used_trials()
    trials = [m for m in st["modules"].values() if m["tipo"] == "prueba" and m["state"] == "activo"]
    import json as _json
    try:
        tu = _json.loads(get_setting("licencia_prueba_usada", "") or "null")
    except ValueError:
        tu = None
    from . import promod
    return render(request, "license.html", user, st=st, modules=licencia.MODULES, offers=OFFERS, state_label=STATE_LABEL,
                  ped=ped, rows=_rows(st, ped, used), used=used, short=licencia.short_date, contact_url=licencia.CONTACT_URL,
                  trial_end=min((m["until_local"] for m in trials), default=None),
                  trial_used=tu if tu and not st["key"] else None,
                  trial_used_text=licencia.trial_used_text(tu["modulos"], tu["fecha"]) if tu and not st["key"] else "",
                  trial_email=user.email or "", trial_org=get_setting("org_name", ""),
                  mods={mid: promod.info(mid) for mid in licencia.MODULES},
                  soon={mid: licencia.soon(mid) for mid in licencia.MODULES},
                  masked=licencia.mask_key(st["key"]) if st["key"] else "", buy_url=licencia.BUY_URL,
                  show_pro=show_locked(), notify30=get_setting("licencia_aviso30", "1") == "1",
                  install=licencia.install_id())


@router.post("/license/activate")
async def license_activate(request: Request):
    user = require(request, "admin")
    form = await request.form()
    try:
        key = form.get("key") or (get_setting("licencia_clave", "") if form.get("again") else "")
        data = licencia.activate(key)
    except licencia.TrialUsed:
        return go("/license#clave")  # el recuadro rojo de «prueba ya usada» (Pruebas 9) lo dibuja la pantalla
    except licencia.LicenseError as e:
        flash(request, str(e), "error")
        return go("/license")
    log_action(request, user, "license_activate", licencia.mask_key(licencia.normalize_key(key)))
    flash(request, _f("Licencia activada: {0}", (data.get("cliente") or {}).get("nombre") or _("lista")))
    return go("/license")


@router.post("/license/refresh")
def license_refresh(request: Request):
    user = require(request, "admin")
    res = licencia.refresh()
    if res == "ok":
        from . import promod
        from .main import app
        got = promod.sync(app)
        if got:
            flash(request, _f("Módulos instalados: {0}", ", ".join(_(licencia.MODULES[m]["name"]) for m in got)))
    log_action(request, user, "license_refresh", res)
    if res == "ok":
        flash(request, _("Licencia actualizada."))
    elif res == "revocada":
        flash(request, _("La licencia fue revocada. Revise su cuenta en comunidad.opennccm.com."), "error")
    elif res == "no_existe":
        flash(request, _(licencia.NO_EXISTE_MSG), "error")
    elif res == "liberada":
        flash(request, _("Esta clave se liberó desde Mi cuenta; vuelve a activarla."), "error")
    elif res == "en_uso":
        flash(request, _(licencia.EN_USO_MSG), "error")
    elif res == "sin_clave":
        flash(request, _("No hay una clave para actualizar."), "error")
    else:
        flash(request, res, "error")
    return go("/license")


@router.post("/license/change")
def license_change(request: Request):
    user = require(request, "admin")
    licencia.clear()
    log_action(request, user, "license_clear")
    flash(request, _("Se quitó la clave. Escriba la nueva para activarla."))
    return go("/license#clave")


@router.post("/license/prefs")
async def license_prefs(request: Request):
    user = require(request, "admin")
    form = await request.form()
    set_setting("licencia_mostrar_pro", "1" if form.get("show_pro") == "on" else "0")
    set_setting("licencia_aviso30", "1" if form.get("notify30") == "on" else "0")
    log_action(request, user, "license_prefs")
    flash(request, _("Preferencias guardadas."))
    return go("/license")


# ------------------------------------------------------------------ pruebas gratis (contrato de pruebas, secciones 4 y 6)
@router.post("/license/trial")
async def trial_ask(request: Request):
    """«Enviar pedido» de la ventana «Pedir una prueba gratis»."""
    from . import pruebas
    user = require(request, "admin")
    form = await request.form()
    try:
        p = pruebas.ask(form.get("correo", ""), form.get("organizacion", ""), form.getlist("modulos"), form.get("motivo", ""))
    except pruebas.TrialError as e:
        flash(request, str(e), "error")
        return go("/license")
    log_action(request, user, "license_trial_ask", f"{p['pedido']} · {', '.join(p['modulos'])}")
    flash(request, _f("Pedido enviado. Confirma el correo que te llegó a {0}; te avisamos aquí cuando se revise.", p["correo"]))
    if p.get("descartados"):
        flash(request, _f("No se pidió {0}: este servidor ya lo probó.", licencia.module_names(p["descartados"])), "error")
    return go("/license")


@router.post("/license/trial/cancel")
def trial_cancel(request: Request):
    from . import pruebas
    user = require(request, "admin")
    try:
        pruebas.cancel()
    except pruebas.TrialError as e:
        flash(request, str(e), "error")
        return go("/license")
    log_action(request, user, "license_trial_cancel")
    flash(request, _("Pedido de prueba cancelado."))
    return go("/license")


@router.post("/license/trial/activate")
def trial_activate(request: Request):
    """«Activar prueba»: un clic, sin pegar nada."""
    from . import pruebas
    user = require(request, "admin")
    try:
        pruebas.activate()
    except licencia.TrialUsed:
        return go("/license#clave")
    except (pruebas.TrialError, licencia.LicenseError) as e:
        flash(request, str(e), "error")
        return go("/license")
    from . import promod
    from .main import app
    got = promod.sync(app)
    log_action(request, user, "license_trial_activate", ", ".join(got))
    from . import pruebas as _p
    flash(request, _("Prueba aprobada · el módulo todavía no salió; te avisamos aquí cuando empiece.")
          if (_p.current() or {}).get("estado") == "esperando" else _("Prueba activada."))
    return go("/license")


@router.post("/license/trial/dismiss")
def trial_dismiss(request: Request):
    from . import pruebas
    require(request, "admin")
    pruebas.dismiss()
    return go("/license")


@router.post("/license/trial-used/dismiss")
def trial_used_dismiss(request: Request):
    require(request, "admin")
    set_setting("licencia_prueba_usada", "")
    return go("/license")
