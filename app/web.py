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
templates.env.globals["SENAL_HASH"] = __import__("hashlib").md5(
    (BASE_DIR / "static" / "senal.css").read_bytes()).hexdigest()[:8]
templates.env.globals["SHELL_HASH"] = __import__("hashlib").md5(
    (BASE_DIR / "static" / "shell.js").read_bytes()).hexdigest()[:8]
# muestras del selector de acento de la barra superior: (claro, oscuro), los mismos valores que senal.css
templates.env.globals["ACCENT_SWATCHES"] = {
    "cobalto": ("#2347e8", "#7c96ff"), "bosque": ("#1f7a4d", "#5fd09a"), "oceano": ("#0b7f86", "#3cc6c0"),
    "lavanda": ("#6a3fc2", "#b39bff"), "atardecer": ("#c2410c", "#ff8c5a"), "arena": ("#8f5410", "#e0b46a"),
    "grafito": ("#3b4452", "#c9d1d9")}
templates.env.globals["ACCENT_NAMES"] = {"cobalto": i18n.N_("Cobalto"), "bosque": i18n.N_("Bosque"), "oceano": i18n.N_("Océano"),
                                         "lavanda": i18n.N_("Lavanda"), "atardecer": i18n.N_("Atardecer"),
                                         "arena": i18n.N_("Arena"), "grafito": i18n.N_("Grafito")}


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
        n = int(secs // 86400)
        return _("ayer") if n == 1 else _f("hace {0} días", n)
    return d.strftime("%Y-%m-%d")


templates.env.globals["icon"] = icon


def logo(size: int = 30):
    """Marca de OpenNCCM dibujada con los colores del tema (cambia con el acento), como en el prototipo."""
    from markupsafe import Markup
    return Markup(f'<svg viewBox="0 0 32 32" width="{size}" height="{size}" aria-hidden="true" class="logo">'
                  '<rect width="32" height="32" rx="9" fill="var(--brand)"/><path d="M10 10H22M10 10l6 11.5M22 10l-6 11.5" '
                  'stroke="var(--on-brand)" stroke-width="2.4" stroke-linecap="round"/><circle cx="10" cy="10" r="3.6" '
                  'fill="var(--on-brand)"/><circle cx="22" cy="10" r="3.6" fill="var(--on-brand)"/><circle cx="16" cy="21.5" '
                  'r="5" fill="var(--signal)"/></svg>')


templates.env.globals["logo"] = logo


def spark(pts, w: int = 90, h: int = 36):
    """Minigráfica de tendencia de los indicadores (la misma que el prototipo): línea, área y punto final."""
    from markupsafe import Markup
    pts = [float(p) for p in pts] or [0.0]
    if len(pts) == 1:
        pts = pts * 2
    mx, mn = max(pts), min(pts)
    sx = w / (len(pts) - 1)

    def y(v):
        return h - 4 - (v - mn) / ((mx - mn) or 1) * (h - 8)
    d = " ".join(f"{'L' if i else 'M'}{i * sx:.1f} {y(v):.1f}" for i, v in enumerate(pts))
    return Markup(f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
                  f'<path class="ar" d="{d} L{w} {h} L0 {h}Z"/><path class="ln" d="{d}"/>'
                  f'<circle class="pt" cx="{w}" cy="{y(pts[-1]):.1f}" r="3"/></svg>')


templates.env.globals["spark"] = spark

_MESES = [i18n.N_("ene"), i18n.N_("feb"), i18n.N_("mar"), i18n.N_("abr"), i18n.N_("may"), i18n.N_("jun"),
          i18n.N_("jul"), i18n.N_("ago"), i18n.N_("sep"), i18n.N_("oct"), i18n.N_("nov"), i18n.N_("dic")]


def fecha(d) -> str:
    """Fecha corta legible, como en el prototipo: «31 dic 2022»."""
    if not d:
        return "—"
    return f"{d.day} {i18n._(_MESES[d.month - 1])} {d.year}"


templates.env.filters["fecha"] = fecha
_DIAS = [i18n.N_("lunes"), i18n.N_("martes"), i18n.N_("miércoles"), i18n.N_("jueves"), i18n.N_("viernes"),
         i18n.N_("sábado"), i18n.N_("domingo")]


def cuando(d) -> str:
    """Próxima ejecución legible, como en el prototipo: «hoy 06:00», «mañana 03:00», «lun 07:00», «1 nov 22:00»."""
    if not d:
        return "—"
    from datetime import datetime
    days = (d.date() - datetime.now(d.tzinfo).date()).days
    hm = d.strftime("%H:%M")
    if days == 0:
        return i18n._f("hoy {0}", hm)
    if days == 1:
        return i18n._f("mañana {0}", hm)
    if 1 < days < 7:
        return f"{i18n._(_DIAS[d.weekday()])[:3]} {hm}"
    return f"{d.day} {i18n._(_MESES[d.month - 1])} {hm}"


templates.env.filters["cuando"] = cuando
templates.env.filters["cuando_ts"] = lambda ts: cuando(__import__("datetime").datetime.fromtimestamp(ts)) if ts else "—"


def area(pts, label: str = ""):
    """Gráfica de área de 24 h del prototipo (Sistema): 0-100 %, líneas a 0/50/100 y «hace 24 h · ahora»."""
    from markupsafe import Markup, escape
    pts = [max(0.0, min(100.0, float(p))) for p in pts] or [0.0, 0.0]
    if len(pts) == 1:
        pts = pts * 2
    W, H, pl, pb = 600, 140, 34, 20
    sx = (W - pl) / (len(pts) - 1)
    y = lambda v: H - pb - v / 100 * (H - pb - 8)  # noqa: E731
    d = " ".join(f"{'L' if i else 'M'}{pl + i * sx:.1f} {y(v):.1f}" for i, v in enumerate(pts))
    grid = "".join(f'<line class="g" x1="{pl}" x2="{W}" y1="{y(v):.1f}" y2="{y(v):.1f}"/><text x="0" y="{y(v) + 4:.1f}">{v}%</text>'
                   for v in (0, 50, 100))
    return Markup(f'<svg class="areachart" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" aria-label="{escape(label)}">'
                  f'{grid}<path class="ar" d="{d} L{W} {H - pb} L{pl} {H - pb}Z"/><path class="ln" d="{d}"/>'
                  f'<text x="{pl}" y="{H - 4}">{escape(i18n._("hace 24 h"))}</text>'
                  f'<text x="{W - 36}" y="{H - 4}">{escape(i18n._("ahora"))}</text></svg>')


templates.env.globals["area"] = area


def admin_counts() -> dict:
    """Recuentos de las pestañas de Administración (prototipo)."""
    from sqlalchemy import func, select

    from .db import Credential, DeviceGroup, SessionLocal, User
    from .drivers import PROFILES
    with SessionLocal() as db:
        return {"users": db.scalar(select(func.count(User.id))) or 0,
                "creds": db.scalar(select(func.count(Credential.id))) or 0,
                "groups": db.scalar(select(func.count(DeviceGroup.id))) or 0, "platforms": len(PROFILES)}


templates.env.globals["admin_counts"] = admin_counts


def mark(line: str, q: str, regex: int = 0, cs: int = 0):
    """Resalta las coincidencias de la búsqueda en una línea (texto escapado; solo se añade <mark>)."""
    import re as _re

    from markupsafe import Markup, escape
    try:
        pat = _re.compile(q if regex else _re.escape(q), 0 if cs else _re.IGNORECASE)
    except _re.error:
        return escape(line)
    out, last = [], 0
    for m in pat.finditer(line):
        if not m.group(0):
            continue
        out.append(str(escape(line[last:m.start()])) + "<mark>" + str(escape(m.group(0))) + "</mark>")
        last = m.end()
    out.append(str(escape(line[last:])))
    return Markup("".join(out))


templates.env.filters["mark"] = mark


def varmark(text: str):
    """Plantilla de configlet con sus variables {{ x }} resaltadas en el color de acento (tarjetas del prototipo)."""
    import re as _re

    from markupsafe import Markup, escape
    return Markup(_re.sub(r"\{\{\s*(\w+)\s*\}\}", r'<b style="color:var(--brand)">{{ \1 }}</b>', str(escape(text or ""))))


templates.env.filters["varmark"] = varmark


def unified_rows(rows):
    """Filas de services.side_by_side → vista unificada: en cada bloque de cambios, primero lo quitado y luego lo
    agregado (como el prototipo). Devuelve (tipo, nº A, nº B, texto) con tipo e | d | a | s."""
    out, dels, adds = [], [], []

    def flush():
        out.extend(dels + adds)
        dels.clear()
        adds.clear()
    for kind, na, ta, nb, tb in rows:
        if kind in ("c", "d", "a"):
            if kind in ("c", "d"):
                dels.append(("d", na, None, ta))
            if kind in ("c", "a"):
                adds.append(("a", None, nb, tb))
            continue
        flush()
        out.append(("s", None, None, ta) if kind == "s" else ("e", na, nb, ta))
    flush()
    return out


templates.env.globals["unified_rows"] = unified_rows

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


def _event_title(e) -> str:
    """Título corto de una alarma: su primera línea o, si solo dice «equipo:», el tipo de alarma y el equipo."""
    first = (e.message or "").splitlines()[0].strip() if e.message else ""
    if len(first) < 12 or first.endswith(":"):
        dev = e.device.name if e.device else ""
        return (i18n._(e.category) + (" · " + dev if dev else ""))[:90]
    return first[:90]


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
            recent = select(Event).where(Event.acknowledged.is_(False), Event.severity != "info")
            if scope is not None:
                recent = recent.where(Event.device_id.in_(scope))
            sevc = {"critical": "crit", "major": "major", "warning": "minor", "minor": "minor"}
            return {"alarms": db.scalar(stmt) or 0,
                    "pending": db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0,
                    # campana de la barra superior: las 5 alarmas abiertas más recientes
                    "recent": [{"sev": sevc.get(e.severity, "info"), "title": _event_title(e),
                                "device_id": e.device_id, "when": e.created_at}
                               for e in db.scalars(recent.order_by(Event.created_at.desc()).limit(5))]}
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
