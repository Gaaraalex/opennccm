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
        "items": [N_("Informe ejecutivo mensual con tendencia contra el mes anterior"),
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
STATE_LABEL = {"activo": N_("Activo"), "gracia": N_("En gracia"), "vencido": N_("Vencido"), "no_incluido": N_("No incluido")}


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
        return page(request, user, m)
    return render(request, tab_template, user, mid=mid, m=m, offer=OFFERS[mid], buy_url=licencia.BUY_URL,
                  state_label=STATE_LABEL, soon=licencia.soon(mid))


@router.get("/reports/pro")
def reports_pro(request: Request):
    return _module_page(request, "informes_pro", "pro_reports.html")


@router.get("/recolectores")
def collectors(request: Request):
    return _module_page(request, "recolectores", "pro_admin.html")


@router.get("/alta-disponibilidad")
def high_availability(request: Request):
    return _module_page(request, "alta_disponibilidad", "pro_admin.html")


@router.get("/license")
def license_page(request: Request):
    user = require(request, "admin")
    st = licencia.state()
    from . import promod
    return render(request, "license.html", user, st=st, modules=licencia.MODULES, offers=OFFERS, state_label=STATE_LABEL,
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
        data = licencia.activate(form.get("key") or "")
    except licencia.LicenseError as e:
        flash(request, str(e), "error")
        return go("/license")
    log_action(request, user, "license_activate", licencia.mask_key(licencia.normalize_key(form.get("key") or "")))
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
    elif res == "liberada":
        flash(request, _("Esta clave se liberó desde Mi cuenta; vuelve a activarla."), "error")
    elif res == "sin_clave":
        flash(request, _("No hay una clave para actualizar."), "error")
    else:
        flash(request, res, "error")
    return go("/license")


@router.post("/license/upload")
async def license_upload(request: Request):
    """Servidor sin internet: el archivo de licencia que se baja de Mi cuenta, o un módulo (.tar.gz con su .firma)."""
    user = require(request, "admin")
    form = await request.form(max_files=5)
    files = [f for f in form.getlist("file") if getattr(f, "filename", "")]
    pkg = next((f for f in files if f.filename.endswith(".tar.gz")), None)
    if pkg is not None:
        from . import promod
        sig = next((f for f in files if f.filename.endswith(".firma")), None)
        mid = pkg.filename.split("-")[0].removesuffix(".tar.gz")
        try:
            if sig is None:
                raise promod.ModuleError(_("Suba el .tar.gz del módulo junto con su archivo .firma."))
            man = promod.install(mid, await pkg.read(promod.MAX_SIZE + 1), (await sig.read(4096)).decode("ascii", "replace"))
            from .main import app
            promod.load(app, mid)
        except promod.ModuleError as e:
            flash(request, str(e), "error")
            return go("/license")
        log_action(request, user, "module_upload", f"{mid} {man.get('version')}")
        flash(request, _f("Módulo instalado: {0}", _(licencia.MODULES[mid]["name"])))
        return go("/license")
    if not files:
        flash(request, _("Elija el archivo de licencia."), "error")
        return go("/license")
    raw = (await files[0].read(65536)).decode("utf-8", "replace")
    try:
        data = licencia.upload(raw)
    except licencia.LicenseError as e:
        flash(request, str(e), "error")
        return go("/license")
    log_action(request, user, "license_upload", (data.get("cliente") or {}).get("nombre", ""))
    flash(request, _("Archivo de licencia cargado."))
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
