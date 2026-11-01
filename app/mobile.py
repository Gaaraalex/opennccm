"""API para apps móviles (/api/mobile/…): sesión del teléfono, panel, equipos con puertos, respaldo, versiones, alarmas,
cambios y avisos.

Se entra con la dirección del servidor, usuario y contraseña (locales o de Active Directory) y, si la cuenta lo tiene, el
código de verificación en dos pasos. El teléfono recibe su propio token (un ApiToken con client="android", que aparece y se
revoca en Mi cuenta › Tokens de API); la contraseña no se guarda en el teléfono. Todo usa los mismos roles y grupos que la
web (require / device_scope / check_device) y deja la acción en Auditoría con «[Android]».
"""
import hashlib
import json
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from . import __version__, services
from .auth import ROLES, check_device, device_scope, require
from .crypto import dummy_verify, verify_password
from .db import ApiToken, ChangeRequest, ConfigVersion, Device, Event, SessionLocal, User, now
from .drivers import PROFILE_MAP
from .i18n import _
from .web import audit, ip

router = APIRouter()
_fails: dict[str, list[float]] = {}         # intentos fallidos por dirección


def _issue(user: User, device: str) -> str:
    token = "ncm_" + secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(ApiToken(user_id=user.id, name=("Android · " + (device or "teléfono"))[:64], prefix=token[:12],
                        token_hash=hashlib.sha256(token.encode()).hexdigest(), client="android"))
        db.commit()
    return token


def _user_json(u: User) -> dict:
    return {"username": u.username, "name": u.username, "role": u.role,
            "can_run": ROLES.get(u.role, 0) >= 1, "is_admin": u.role == "admin"}


def act(request: Request, user, action: str, detail: str = ""):
    audit(user.username, action, "[Android] " + detail, ip(request))


def _iso(d):
    return d.isoformat() if d else None


# --------------------------------------------------------------------------- entrar
@router.post("/api/mobile/login")
async def mobile_login(request: Request):
    """Usuario y contraseña (locales o de Active Directory) y, si la cuenta lo tiene, el código de dos pasos."""
    from . import extauth
    from .security import totp_verify
    addr = ip(request)
    tries = [t for t in _fails.get(addr, []) if time.time() - t < 900]
    if len(tries) >= 10:
        return JSONResponse({"error": "too_many", "detail": _("Demasiados intentos desde su dirección. Espere 15 minutos.")}, 429)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    username, password = str(data.get("username") or "").strip(), str(data.get("password") or "")
    code, device = str(data.get("otp") or "").strip(), str(data.get("device") or "")[:40]

    def fail(detail=None, status=401):
        tries.append(time.time())
        _fails[addr] = tries
        audit(username or "?", "login_failed", "[Android]", addr)
        return JSONResponse({"error": "invalid", "detail": detail or _("Usuario o contraseña incorrectos")}, status)

    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == username)).first()
        if user is None and "@" in username:
            user = db.scalars(select(User).where(func.lower(User.username) == username.lower())).first()
        ok = False
        if user is not None and user.locked_until and user.locked_until > now():
            return fail(_("La cuenta está bloqueada temporalmente por intentos fallidos. Inténtelo más tarde o pida a un administrador que la desbloquee."), 423)
        if (user is None or (user.auth_source or "local") == "ldap") and extauth.ldap_enabled():
            try:
                ext = await run_in_threadpool(extauth.ldap_authenticate, username, password)
            except extauth.ExtAuthError:
                return fail(_("No se pudo comprobar la cuenta en el directorio. Avise a un administrador."), 502)
            if ext is not None:
                user, _why = extauth.upsert_user(db, ext, "ldap")
                db.commit()
                ok = user is not None
        elif user is None:
            dummy_verify(password)
        elif (user.auth_source or "local") == "local":
            ok = bool(user.password_hash) and verify_password(password, user.password_hash)
        if not ok or not user.active:
            if user is not None and (user.auth_source or "local") == "local":
                user.failed_logins = (user.failed_logins or 0) + 1
                db.commit()
            return fail()
        if user.totp_enabled:
            if not code:
                return JSONResponse({"error": "otp_required", "detail": _("Escriba el código de verificación en dos pasos.")}, 401)
            if not totp_verify(user, code):
                return fail(_("Código incorrecto o caducado"))
        user.failed_logins = 0
        user.last_login_at, user.last_login_ip = now(), addr
        db.commit()
        db.refresh(user)
        token = _issue(user, device)
        info = _user_json(user)
    _fails.pop(addr, None)
    audit(info["username"], "login", "[Android] " + device, addr)
    return {"token": token, "user": info, "server": {"version": __version__}}


@router.post("/api/mobile/logout")
def mobile_logout(request: Request):
    """Cierra la sesión del teléfono: revoca su propio token."""
    require(request)
    auth = request.headers.get("authorization", "")
    digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
    with SessionLocal() as db:
        tok = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
        if tok:
            db.delete(tok)
            db.commit()
    return {"ok": True}


@router.get("/api/mobile/me")
def mobile_me(request: Request):
    user = require(request)
    return {"user": _user_json(user), "server": {"version": __version__}}


# --------------------------------------------------------------------------- panel, equipos, alarmas y cambios
def _visible(db, user):
    scope = device_scope(user)
    return [d for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]


@router.get("/api/mobile/summary")
def mobile_summary(request: Request):
    user = require(request)
    with SessionLocal() as db:
        devs = _visible(db, user)
        ids = {d.id for d in devs}
        events = [e for e in db.scalars(select(Event).where(Event.acknowledged.is_(False)))
                  if e.device_id is None or e.device_id in ids]
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
        sev = {k: sum(1 for e in events if e.severity == k) for k in ("critical", "major", "warning", "info")}
        down = [{"id": d.id, "name": d.name, "host": d.host, "error": (d.last_error or "")[:200]}
                for d in devs if d.last_status == "error"][:20]
        return {"devices": {"total": len(devs), "ok": sum(d.last_status == "ok" for d in devs),
                            "error": sum(d.last_status == "error" for d in devs),
                            "pending": sum(d.last_status not in ("ok", "error") for d in devs),
                            "drift": sum(bool(d.baseline_drift) for d in devs)},
                "alarms": {"open": len(events), **sev}, "changes_pending": pending, "down": down,
                "user": _user_json(user)}


def _dev_json(d) -> dict:
    prof = PROFILE_MAP.get(d.device_type)
    return {"id": d.id, "name": d.name, "host": d.host, "type": d.device_type, "platform": prof.label if prof else d.device_type,
            "vendor": prof.vendor if prof else "", "model": d.model, "version": d.os_version, "serial": d.serial,
            "group": d.group.name if d.group else None, "location": d.location, "status": d.last_status,
            "last_error": d.last_error, "last_backup": _iso(d.last_backup_at), "drift": bool(d.baseline_drift),
            "web_url": (d.web_url or f"https://{d.host}/") if d.web_enabled is not False else None}


@router.get("/api/mobile/devices")
def mobile_devices(request: Request, q: str = ""):
    user = require(request)
    ql = q.strip().lower()
    with SessionLocal() as db:
        out = []
        for d in _visible(db, user):
            if ql and ql not in f"{d.name} {d.host} {d.model or ''} {d.location or ''}".lower():
                continue
            d.group  # carga la relación antes de cerrar la sesión
            out.append(_dev_json(d))
        return out


@router.get("/api/mobile/devices/{dev_id}")
def mobile_device(request: Request, dev_id: int):
    user = require(request)
    check_device(user, dev_id)
    from . import ports as ports_mod
    from .crypto import try_decrypt
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None:
            return JSONResponse({"error": "not_found"}, 404)
        d.group  # carga la relación antes de cerrar la sesión
        versions = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id)
                              .order_by(ConfigVersion.id.desc()).limit(20)).all()
        last_run = next((v for v in versions if v.kind == "running"), None)
        pv = ports_mod.view(d, try_decrypt(last_run.content_enc, "") if last_run else "")
        total = db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == dev_id)) or 0
        events = db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc()).limit(15)).all()
        out = _dev_json(d)
        out.update({
            "ports": None if pv is None else {"items": pv["ports"], "status": pv["status"], "at": _iso(pv["at"]),
                                               "layout": pv["layout"], "rear": pv["rear"]},
            "versions": [{"id": v.id, "n": total - i, "kind": v.kind, "at": _iso(v.created_at), "trigger": v.trigger,
                          "user": v.username, "added": v.added, "removed": v.removed, "label": v.label}
                         for i, v in enumerate(versions)],
            "events": [{"id": e.id, "severity": e.severity, "category": _(e.category), "message": e.message[:500],
                        "at": _iso(e.created_at), "ack": e.acknowledged} for e in events]})
        return out


@router.post("/api/mobile/devices/{dev_id}/backup")
async def mobile_backup(request: Request, dev_id: int):
    user = require(request, "operator")
    check_device(user, dev_id)
    result = await run_in_threadpool(services.backup_device, dev_id, "manual", user.username)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        err, name = (d.last_error, d.name) if d else (None, "")
    act(request, user, "device_backup", f"{name}: {result}")
    return {"result": result, "error": err if result == "error" else None}


@router.get("/api/mobile/versions/{ver_id}")
def mobile_version(request: Request, ver_id: int):
    user = require(request)
    from .crypto import decrypt
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        if v is None:
            return JSONResponse({"error": "not_found"}, 404)
        check_device(user, v.device_id)
        text = decrypt(v.content_enc)
        name = v.device.name
    act(request, user, "config_view", f"{name} v{ver_id}")
    return {"id": ver_id, "device": name, "text": text}


@router.get("/api/mobile/events")
def mobile_events(request: Request, open: int = 1, limit: int = 200):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        stmt = select(Event).order_by(Event.id.desc()).limit(min(max(limit, 1), 500))
        if open:
            stmt = stmt.where(Event.acknowledged.is_(False))
        return [{"id": e.id, "severity": e.severity, "category": _(e.category), "message": e.message[:800],
                 "at": _iso(e.created_at), "ack": e.acknowledged, "device_id": e.device_id,
                 "device": e.device.name if e.device else None}
                for e in db.scalars(stmt) if scope is None or e.device_id is None or e.device_id in scope]


@router.post("/api/mobile/events/{ev_id}/ack")
def mobile_ack(request: Request, ev_id: int):
    user = require(request, "operator")
    with SessionLocal() as db:
        e = db.get(Event, ev_id)
        if e is None:
            return JSONResponse({"error": "not_found"}, 404)
        if e.device_id:
            check_device(user, e.device_id)
        from . import kb
        if kb.needs_case(e, user):  # una alarma crítica se cierra con su caso, desde el panel web
            return JSONResponse({"error": "needs_case", "url": f"/kb/close/{e.id}"}, 409)
        e.acknowledged = True
        db.commit()
    act(request, user, "events_ack", str(ev_id))
    return {"ok": True}


def _cr_json(cr, names: dict) -> dict:
    ids = json.loads(cr.device_ids or "[]")
    return {"id": cr.id, "title": cr.title, "status": cr.status, "kind": cr.kind, "rollback": bool(cr.is_rollback),
            "requested_by": cr.requested_by, "reviewed_by": cr.reviewed_by, "comment": cr.review_comment,
            "at": _iso(cr.created_at), "executed_at": _iso(cr.executed_at),
            "devices": [names.get(i, f"#{i}") for i in ids]}


@router.get("/api/mobile/changes")
def mobile_changes(request: Request, limit: int = 100):
    require(request)
    with SessionLocal() as db:
        names = dict(db.execute(select(Device.id, Device.name)).all())
        return [_cr_json(cr, names) for cr in db.scalars(select(ChangeRequest).order_by(ChangeRequest.id.desc())
                                                           .limit(min(max(limit, 1), 300)))]


@router.get("/api/mobile/changes/{cr_id}")
def mobile_change(request: Request, cr_id: int):
    require(request)
    with SessionLocal() as db:
        cr = db.get(ChangeRequest, cr_id)
        if cr is None:
            return JSONResponse({"error": "not_found"}, 404)
        names = dict(db.execute(select(Device.id, Device.name)).all())
        out = _cr_json(cr, names)
        out.update({"body": cr.body, "result": (cr.result or "")[-6000:]})
        return out


@router.post("/api/mobile/changes/{cr_id}/review")
async def mobile_review(request: Request, cr_id: int):
    """Aprobar o rechazar (la app pide la huella antes). Mismas reglas que la web."""
    user = require(request, "admin")
    from .main import review_change
    try:
        data = await request.json()
    except ValueError:
        data = {}
    decision = "approve" if data.get("decision") == "approve" else "reject"
    ok, msg = review_change(user, cr_id, decision, str(data.get("comment") or ""))
    if ok:
        act(request, user, "change_approved" if decision == "approve" else "change_rejected", f"#{cr_id}")
    return JSONResponse({"ok": ok, "detail": msg}, 200 if ok else 409)


@router.get("/api/mobile/notifications")
def mobile_notifications(request: Request, since: int = 0):
    """Avisos nuevos desde el último que vio el teléfono: alarmas graves y cambios por aprobar (la app consulta cada
    15 minutos en segundo plano). «since» = 0 devuelve solo el último id, para empezar sin avisos viejos."""
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        last = db.scalar(select(func.max(Event.id))) or 0
        if since <= 0:
            return {"last": last, "items": []}
        items = []
        for e in db.scalars(select(Event).where(Event.id > since, Event.severity.in_(("critical", "major")))
                            .order_by(Event.id).limit(50)):
            if scope is None or e.device_id is None or e.device_id in scope:
                items.append({"id": e.id, "kind": "alarm", "severity": e.severity, "title": _(e.category),
                              "text": (e.device.name + ": " if e.device else "") + e.message.split("\n")[0][:200],
                              "device_id": e.device_id})
        if user.role == "admin":
            for cr in db.scalars(select(ChangeRequest).where(ChangeRequest.status == "pending",
                                                             ChangeRequest.created_at > now() - timedelta(minutes=30))):
                items.append({"id": -cr.id, "kind": "change", "severity": "info", "title": _("Cambio por aprobar"),
                              "text": cr.title[:200], "change_id": cr.id})
        return {"last": last, "items": items}
