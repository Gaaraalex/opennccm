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

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["ROLES"] = ROLES
templates.env.globals["VERSION"] = __version__
# huella del CSS: fuerza a los navegadores a recargarlo tras cada actualización
templates.env.globals["CSS_HASH"] = __import__("hashlib").md5(
    (BASE_DIR / "static" / "style.css").read_bytes()).hexdigest()[:8]
templates.env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "—"
templates.env.filters["cron_text"] = cronutil.describe
templates.env.filters["ymd"] = lambda d: d.strftime("%Y-%m-%d") if d else "—"


def render(request: Request, name: str, user, **ctx):
    flashes = request.session.pop("flash", [])
    popup = None
    if user is not None and user.role == "admin" and not request.url.path.startswith("/system/updat"):
        from . import updates
        st = updates.status()  # aviso emergente: una vez por sesión y por versión nueva
        if st["available"] and not st["running"] and request.session.get("upd_dismissed") != st["latest"]:
            popup = st
    return templates.TemplateResponse(request, name, {"user": user, "flashes": flashes, "update_popup": popup,
                                                      "profiles": PROFILES, "profile_map": PROFILE_MAP, **ctx})


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
