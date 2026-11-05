"""Comandos favoritos de la consola (Dispositivos y Operar › Consola).

Cada usuario guarda sus comandos de verificación más usados por familia de equipos (Cisco IOS / IOS-XE, MikroTik
RouterOS, WatchGuard…) o para todos los equipos («*»). Un administrador puede compartirlos con todo el equipo. En la
consola, un clic escribe el comando y lo envía; si el comando cambia la configuración se escribe sin enviar, aunque el
favorito diga «Enviar»: lo confirma la persona con Enter."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from . import consolecore as cc
from .auth import Forbidden, require
from .db import ConsoleFavorite, Device, SessionLocal, audit
from .drivers import PROFILE_MAP
from .i18n import _
from .web import render

router = APIRouter()

ALL = "*"
FAMILY_LABEL = {"ros": "MikroTik RouterOS", "ios": "Cisco IOS / IOS-XE"}
# sugeridos cuando una familia todavía no tiene favoritos (solo comandos de consulta)
SUGGESTED = {"ros": ["/interface print", "/ip neighbor print", '/log print where topics~"error"', "/system resource print"]}
MAX_PER_USER = 300


def family_of(device_type: str) -> str:
    """La familia de la consola (consolecore.os_of) o, si no tiene una propia, el fabricante («watchguard»)."""
    k = cc.os_of(device_type or "")
    if k != "generic":
        return k
    prof = PROFILE_MAP.get(device_type or "")
    return (prof.vendor if prof else (device_type or "generic")).lower().replace(" ", "")[:32] or "generic"


def family_label(fam: str) -> str:
    if fam == ALL:
        return _("Todos los equipos")
    if fam in FAMILY_LABEL:
        return FAMILY_LABEL[fam]
    if cc.OS_LABEL.get(fam):
        return cc.OS_LABEL[fam]
    vend = next((p.vendor for p in PROFILE_MAP.values() if p.vendor.lower().replace(" ", "") == fam), "")
    return vend or fam


def _sample_type(fam: str) -> str:
    """Un tipo de equipo de la familia (para saber si un comando cambia la configuración)."""
    return next((t for t in PROFILE_MAP if family_of(t) == fam), "")


def is_change(fam: str, command: str) -> bool:
    """¿Cambia la configuración? Para «Todos los equipos» basta con que la cambie en alguna familia conocida."""
    lines = [x for x in (command or "").splitlines() if x.strip()]
    if fam == ALL:
        fams = ["ios", "ros", "nxos", "eos", "vrp", "junos", "exos", "voss", "fastpath", "linux", "generic"]
        return any(cc.is_change(f, ln) for f in fams for ln in lines)
    t = _sample_type(fam)
    return any(cc.is_change(cc.os_of(t) if t else fam, ln, t or None) for ln in lines)


def suggested(fam: str) -> list[str]:
    if fam in SUGGESTED:
        return SUGGESTED[fam]
    return [c for c in cc.FREQUENT.get(fam, []) if not cc.is_change(fam, c)][:4]


def _visible(db, user, fams: list[str] | None = None):
    q = select(ConsoleFavorite).where(or_(ConsoleFavorite.owner == user.username, ConsoleFavorite.shared.is_(True)))
    if fams is not None:
        q = q.where(ConsoleFavorite.family.in_(fams))
    return list(db.scalars(q.order_by(ConsoleFavorite.family, ConsoleFavorite.pos, ConsoleFavorite.id)))


def _row(f: ConsoleFavorite, user) -> dict:
    return {"id": f.id, "name": f.name, "command": f.command, "mode": f.mode, "family": f.family,
            "family_label": family_label(f.family), "shared": f.shared, "owner": f.owner,
            "change": is_change(f.family, f.command), "mine": f.owner == user.username,
            "can_edit": f.owner == user.username if not f.shared else user.role == "admin"}


def _console_user(request: Request):
    from .console import role_mode
    user = require(request)
    if role_mode(user.role) == "none":
        raise Forbidden()
    return user


def for_family(user, fam: str) -> dict:
    """Lo que necesita la barra de favoritos de la consola: los de la familia y después los de todos los equipos."""
    with SessionLocal() as db:
        rows = [_row(f, user) for f in _visible(db, user, [fam, ALL] if fam != ALL else [ALL])]
    rows.sort(key=lambda r: r["family"] == ALL)
    return {"family": fam, "label": family_label(fam), "items": rows, "suggested": suggested(fam) if fam != ALL else []}


def for_console(user, device_type: str) -> dict:
    return for_family(user, family_of(device_type))


@router.get("/api/console/favorites")
def api_list(request: Request, family: str = ""):
    user = _console_user(request)
    if family:
        return JSONResponse(for_family(user, family[:32]))
    with SessionLocal() as db:
        return JSONResponse({"items": [_row(f, user) for f in _visible(db, user)]})


@router.get("/api/console/favorites/check")
def api_check(request: Request, family: str = ALL, command: str = ""):
    """¿Cambia la configuración? (aviso del formulario: esos siempre se escriben sin enviar)."""
    _console_user(request)
    return JSONResponse({"change": is_change(family[:32], command[:2000])})


@router.post("/api/console/favorites")
async def api_save(request: Request):
    """Crea o actualiza un favorito. Los compartidos solo los crea y cambia un administrador."""
    user = _console_user(request)
    body = await request.json()
    name = str(body.get("name") or "").strip()[:64]
    command = str(body.get("command") or "").strip()[:2000]
    fam = str(body.get("family") or ALL).strip()[:32] or ALL
    mode = "type" if body.get("mode") == "type" else "send"
    shared = bool(body.get("shared")) and user.role == "admin"
    if not command:
        return JSONResponse({"error": _("Escribe el comando.")}, 400)
    if not name:
        name = command.splitlines()[0][:64]
    with SessionLocal() as db:
        fid = body.get("id")
        if fid:
            f = db.get(ConsoleFavorite, int(fid))
            if f is None or not _row(f, user)["can_edit"]:
                return JSONResponse({"error": _("No puedes cambiar este favorito.")}, 403)
            if f.shared and not shared and f.owner != user.username:
                f.owner = user.username
        else:
            mine = db.scalar(select(func.count()).select_from(ConsoleFavorite).where(ConsoleFavorite.owner == user.username)) or 0
            if mine >= MAX_PER_USER:
                return JSONResponse({"error": _("Llegaste al máximo de favoritos.")}, 400)
            last = db.scalar(select(func.max(ConsoleFavorite.pos)).where(ConsoleFavorite.family == fam)) or 0
            f = ConsoleFavorite(owner=user.username, family=fam, pos=last + 1)
            db.add(f)
        f.name, f.command, f.family, f.mode, f.shared = name, command, fam, mode, shared
        db.commit()
        row = _row(f, user)
    if shared:
        audit(user.username, "console_favorite_shared", f"{family_label(fam)} · {name}: {command[:200]}")
    return JSONResponse({"ok": True, "item": row})


@router.post("/api/console/favorites/{fid}/delete")
def api_delete(request: Request, fid: int):
    """Quita un favorito y devuelve sus datos (para «Deshacer»)."""
    user = _console_user(request)
    with SessionLocal() as db:
        f = db.get(ConsoleFavorite, fid)
        if f is None or not _row(f, user)["can_edit"]:
            return JSONResponse({"error": _("No puedes quitar este favorito.")}, 403)
        data = {"name": f.name, "command": f.command, "family": f.family, "mode": f.mode, "shared": f.shared, "pos": f.pos}
        db.delete(f)
        db.commit()
    return JSONResponse({"ok": True, "item": data})


@router.post("/api/console/favorites/restore")
async def api_restore(request: Request):
    """«Deshacer» después de quitar: vuelve a crearlo en el mismo lugar."""
    user = _console_user(request)
    b = await request.json()
    shared = bool(b.get("shared")) and user.role == "admin"
    with SessionLocal() as db:
        f = ConsoleFavorite(owner=user.username, family=str(b.get("family") or ALL)[:32], name=str(b.get("name") or "")[:64],
                            command=str(b.get("command") or "")[:2000], mode="type" if b.get("mode") == "type" else "send",
                            shared=shared, pos=int(b.get("pos") or 0))
        if not f.command:
            return JSONResponse({"error": _("Escribe el comando.")}, 400)
        db.add(f)
        db.commit()
        return JSONResponse({"ok": True, "item": _row(f, user)})


@router.post("/api/console/favorites/order")
async def api_order(request: Request):
    """Nuevo orden de una pestaña (arrastrar): solo se mueven los que la persona puede cambiar."""
    user = _console_user(request)
    ids = [int(x) for x in (await request.json()).get("ids", []) if str(x).isdigit()]
    with SessionLocal() as db:
        for i, fid in enumerate(ids):
            f = db.get(ConsoleFavorite, fid)
            if f is not None and (f.owner == user.username or user.role == "admin"):
                f.pos = i + 1
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/console/favorites/suggested")
async def api_add_suggested(request: Request):
    """«Agrega los N sugeridos» de una familia sin favoritos."""
    user = _console_user(request)
    fam = str((await request.json()).get("family") or "")[:32]
    with SessionLocal() as db:
        have = {f.command for f in _visible(db, user, [fam])}
        for i, c in enumerate(suggested(fam)):
            if c not in have:
                db.add(ConsoleFavorite(owner=user.username, family=fam, name=_short_name(c), command=c, mode="send", pos=i + 1))
        db.commit()
    return JSONResponse(for_family(user, fam))


def _short_name(cmd: str) -> str:
    """Nombre corto para un sugerido: «/system resource print» → «System resource»; «show ip interface brief» → «Ip interface brief»."""
    words = [w for w in cmd.replace("/", " ").split() if w not in ("print", "show", "display")][:3]
    words = [w for w in words if "~" not in w and "=" not in w and '"' not in w] or [cmd.split()[0]]
    t = " ".join(words)
    return (t[:1].upper() + t[1:])[:64]


@router.get("/console/favorites")
def page(request: Request, family: str = ""):
    """Ventana «Comandos favoritos»: pestañas por tipo de equipo con su cantidad."""
    user = _console_user(request)
    with SessionLocal() as db:
        rows = [_row(f, user) for f in _visible(db, user)]
        types = {t for (t,) in db.execute(select(Device.device_type).distinct())}
    fams = ({family_of(t) for t in types} | {r["family"] for r in rows}) - {ALL}
    if family and family != ALL:
        fams.add(family)
    order = sorted(fams - {ALL}, key=lambda f: (f not in ("ios", "ros"), family_label(f).lower())) + [ALL]
    tabs = [{"family": f, "label": family_label(f), "n": sum(r["family"] == f for r in rows)} for f in order]
    cur = family if family in order else (order[0] if order else ALL)
    return render(request, "favorites.html", user, tabs=tabs, items=rows, cur=cur)
