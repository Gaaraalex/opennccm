"""Utilidades compartidas por las rutas web."""
from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import __version__
from .auth import ROLES
from . import cronutil
from .config import BASE_DIR
from .db import audit
from .drivers import PROFILE_MAP, PROFILES
from . import i18n

templates = Jinja2Templates(directory=BASE_DIR / "templates")
i18n.install(templates.env)  # textos de las plantillas en el idioma de cada usuario (app/locales)
templates.env.globals["ROLES"] = ROLES
templates.env.globals["VERSION"] = __version__
# huella del CSS: fuerza a los navegadores a recargarlo tras cada actualización
templates.env.globals["CSS_HASH"] = __import__("hashlib").md5(
    (BASE_DIR / "static" / "style.css").read_bytes()).hexdigest()[:8]


def icon(name: str, cls: str = "", label: str = ""):
    """Icono SVG del sprite local (static/icons.svg). Decorativo salvo que se indique label (texto accesible)."""
    from markupsafe import Markup, escape
    a11y = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    return Markup(f'<svg class="ico {escape(cls)}" {a11y} focusable="false"><use href="/static/icons.svg?v={__version__}'
                  f'#i-{escape(name)}"/></svg>')


def ago(d) -> str:
    """Tiempo relativo para lo reciente («hace 3 min»), fecha corta para lo antiguo; la fecha exacta va en title."""
    if not d:
        return "—"
    from .db import now
    from .i18n import _, _f
    secs = (now() - d).total_seconds()
    if secs < 60:
        return _("ahora mismo")
    if secs < 3600:
        return _f("hace {0} min", int(secs // 60))
    if secs < 86400:
        return _f("hace {0} h", int(secs // 3600))
    if secs < 7 * 86400:
        return _f("hace {0} d", int(secs // 86400))
    return d.strftime("%Y-%m-%d")


templates.env.globals["icon"] = icon

# severidad -> forma y texto traducido (nunca el valor crudo de la base de datos)
SEV_CLASS = {"critical": "crit", "major": "major", "error": "major", "minor": "minor", "warning": "warn",
             "info": "info", "high": "major", "medium": "minor", "low": "info"}
SEV_LABEL = {"crit": i18n.N_("Crítica"), "major": i18n.N_("Mayor"), "minor": i18n.N_("Menor"),
             "warn": i18n.N_("Aviso"), "info": i18n.N_("Info")}
CR_STATUS = {"pending": i18n.N_("Pendiente"), "approved": i18n.N_("Aprobada"), "rejected": i18n.N_("Rechazada"),
             "running": i18n.N_("En curso"), "done": i18n.N_("Aplicada"), "failed": i18n.N_("Fallida"),
             "partial": i18n.N_("Parcial")}


def sev(value):
    """Marca de severidad con forma (rombo, triángulo, círculo, cuadrado) y texto en el idioma del usuario."""
    from markupsafe import Markup
    c = SEV_CLASS.get((value or "").lower(), "info")
    return Markup(f'<span class="sev {c}">{i18n._(SEV_LABEL[c])}</span>')


def crstatus(value):
    """Estado de una solicitud de cambio, traducido (la clase b-* conserva el valor para el color)."""
    from markupsafe import Markup, escape
    label = i18n._(CR_STATUS[value]) if value in CR_STATUS else value
    return Markup(f'<span class="badge b-{escape(value)}">{escape(label)}</span>')


templates.env.globals["sevtag"] = sev

_EMPTY_MARK = {
    "check": '<path d="M82 30l6 6 10-11" class="ck"/>',
    "plus": '<path d="M90 22v16M82 30h16" class="ck"/>',
    "search": '<circle cx="88" cy="28" r="6" class="ck"/><path d="M92.5 32.5l5 5" class="ck"/>',
}


def empty_art(kind: str = "plus"):
    """Ilustración de los estados vacíos: nodos grises y un nodo lima con check (todo bien), + (agrega el primero)
    o lupa (sin resultados). El texto va en la plantilla, para que se traduzca."""
    from markupsafe import Markup
    return Markup('<svg class="empty-art" viewBox="0 0 180 100" aria-hidden="true">'
                  '<path d="M40 70L90 30L140 70Z" class="ln"/><circle cx="40" cy="70" r="13" class="nd"/>'
                  '<circle cx="140" cy="70" r="13" class="nd"/><circle cx="90" cy="30" r="17" class="lime"/>'
                  f'{_EMPTY_MARK.get(kind, _EMPTY_MARK["plus"])}</svg>')


templates.env.globals["empty_art"] = empty_art
templates.env.globals["crstatus"] = crstatus
templates.env.globals["now"] = __import__("app.db", fromlist=["now"]).now
templates.env.filters["ago"] = ago
templates.env.filters["dt"] =lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "—"
templates.env.filters["cron_text"] = cronutil.describe
templates.env.filters["ymd"] = lambda d: d.strftime("%Y-%m-%d") if d else "—"


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flash", [])
    undo = request.session.pop("undo", None)  # acción reversible: el aviso ofrece «Deshacer»
    popup = None
    if user is not None and user.role == "admin" and not request.url.path.startswith("/system/updat"):
        from . import updates
        st = updates.status()  # aviso emergente: una vez por sesión y por versión nueva
        if st["available"] and not st["running"] and request.session.get("upd_dismissed") != st["latest"]:
            popup = st
    counts = _nav_counts(user) if user is not None and not ctx.get("compact") else {}
    return templates.TemplateResponse(request, name, {"user": user, "flashes": flashes, "undo": undo, "update_popup": popup,
                                                      "nav_counts": counts,
                                                      "profiles": PROFILES, "profile_map": PROFILE_MAP, **ctx})


def _nav_counts(user) -> dict:
    """Contadores de la barra lateral: solicitudes de cambio por aprobar y alarmas abiertas (de sus equipos)."""
    from sqlalchemy import func, select

    from .auth import device_scope
    from .db import ChangeRequest, Event, SessionLocal
    try:
        scope = device_scope(user)
        with SessionLocal() as db:
            stmt = select(func.count(Event.id)).where(Event.acknowledged.is_(False), Event.severity != "info")
            if scope is not None:
                stmt = stmt.where(Event.device_id.in_(scope))
            return {"alarms": db.scalar(stmt) or 0,
                    "pending": db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0}
    except Exception:  # noqa: BLE001 - un contador nunca debe impedir mostrar la página
        return {}


def flash(request: Request, msg: str, kind="ok"):
    request.session.setdefault("flash", []).append([kind, msg])


def go(url: str):
    return RedirectResponse(url, status_code=303)


def ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def log_action(request, user, action, detail=""):
    audit(user.username, action, detail, ip(request))


def csv_response(name: str, data: bytes) -> Response:
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
