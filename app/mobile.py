"""App Android de OpenNCCM: su API (/api/mobile/…), vincular teléfonos y descargar la APK.

La app entra de dos formas, y en las dos recibe un token propio del teléfono (un ApiToken con client="android") que el
usuario ve y revoca en Mi cuenta › Teléfonos vinculados; la contraseña nunca se guarda en el teléfono:

* con la dirección del servidor, usuario y contraseña (locales o de Active Directory) y, si la cuenta lo tiene, el código
  de verificación en dos pasos;
* escaneando el QR de Mi cuenta › Teléfonos vinculados (vale 5 minutos y una sola vez); sirve también para las cuentas de
  Microsoft Entra, que no tienen contraseña en OpenNCCM.

Todo lo demás usa los mismos roles y grupos que la web (require / device_scope / check_device) y deja la acción en
Auditoría con «[Android]».
"""
import hashlib
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path

import segno
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from . import __version__, services
from .auth import ROLES, check_device, device_scope, require
from .config import BASE_DIR, TLS_CA
from .crypto import dummy_verify, verify_password
from .db import ApiToken, ChangeRequest, ConfigVersion, Device, Event, SessionLocal, User, now
from .drivers import PROFILE_MAP, get_profile
from .i18n import _
from .web import audit, flash, go, ip, render

router = APIRouter()
APK = BASE_DIR / "static" / "android" / "opennccm.apk"
PAIR_TTL = 300
_pairs: dict[str, tuple[int, float]] = {}   # código -> (usuario, caducidad)
_fails: dict[str, list[float]] = {}         # intentos fallidos por dirección


def base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def ca_fingerprint() -> str:
    """SHA-256 del certificado de la autoridad de OpenNCCM (la app lo fija para confiar en el servidor)."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        cert = x509.load_pem_x509_certificate(Path(TLS_CA).read_bytes())
        return cert.fingerprint(hashes.SHA256()).hex()
    except Exception:  # noqa: BLE001 - sin CA propia (certificado de una autoridad pública o HTTP)
        return ""


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


@router.post("/api/mobile/pair")
async def mobile_pair(request: Request):
    """Canjea el código del QR de Mi cuenta › Teléfonos vinculados por el token del teléfono (una sola vez)."""
    try:
        data = await request.json()
    except ValueError:
        data = {}
    code, device = str(data.get("code") or ""), str(data.get("device") or "")[:40]
    entry = _pairs.pop(code, None)
    if not entry or entry[1] < time.time():
        return JSONResponse({"error": "invalid", "detail": _("El código caducó o ya se usó. Genere otro QR en Mi cuenta.")}, 401)
    with SessionLocal() as db:
        user = db.get(User, entry[0])
        if user is None or not user.active:
            return JSONResponse({"error": "invalid", "detail": _("La cuenta ya no está activa.")}, 401)
        token = _issue(user, device)
        info = _user_json(user)
    audit(info["username"], "phone_paired", "[Android] " + device, ip(request))
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


# --------------------------------------------------------------------------- web: teléfonos, QR y APK
@router.get("/account/phones")
def phones_page(request: Request):
    user = require(request)
    with SessionLocal() as db:
        phones = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.client == "android")
                            .order_by(ApiToken.id.desc())).all()
    return render(request, "phones.html", user, phones=phones, has_apk=APK.exists(), qr=None, url=base_url(request),
                  fp=ca_fingerprint())


@router.post("/account/phones/pair")
def phones_pair(request: Request):
    """QR para vincular un teléfono: dirección del servidor, código de un solo uso (5 minutos) y huella del certificado."""
    user = require(request)
    for k in [k for k, v in _pairs.items() if v[1] < time.time()]:
        _pairs.pop(k, None)
    code = secrets.token_urlsafe(24)
    _pairs[code] = (user.id, time.time() + PAIR_TTL)
    payload = json.dumps({"opennccm": 1, "url": base_url(request), "code": code, "fp": ca_fingerprint()},
                         separators=(",", ":"))
    with SessionLocal() as db:
        phones = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.client == "android")
                            .order_by(ApiToken.id.desc())).all()
    audit(user.username, "phone_pair_qr", "", ip(request))
    return render(request, "phones.html", user, phones=phones, has_apk=APK.exists(), url=base_url(request),
                  fp=ca_fingerprint(), qr=segno.make(payload, error="m").svg_data_uri(scale=6, border=2), code=code)


@router.post("/account/phones/{tid}/revoke")
def phones_revoke(request: Request, tid: int):
    user = require(request)
    with SessionLocal() as db:
        tok = db.get(ApiToken, tid)
        if tok and tok.user_id == user.id:
            db.delete(tok)
            db.commit()
            flash(request, _("Teléfono desvinculado: ya no puede entrar con ese token."))
    audit(user.username, "phone_revoke", str(tid), ip(request))
    return go("/account/phones")


@router.get("/app/android.apk")
def android_apk(request: Request):
    """La app Android que viene con esta versión de OpenNCCM."""
    user = require(request)
    if not APK.exists():
        flash(request, _("Esta instalación no trae la app Android (se compila con cada versión publicada)."), "error")
        return go("/account/phones")
    audit(user.username, "apk_download", "", ip(request))
    return FileResponse(APK, media_type="application/vnd.android.package-archive",
                        filename=f"OpenNCCM-{__version__}.apk")


@router.get("/devices/{dev_id}/label")
def device_label(request: Request, dev_id: int):
    """Etiqueta QR del equipo para pegarla en él: la app la escanea y abre su ficha."""
    user = require(request)
    check_device(user, dev_id)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None:
            return go("/devices")
        payload = json.dumps({"opennccm": 1, "url": base_url(request), "device": d.id, "name": d.name, "host": d.host},
                             separators=(",", ":"))
        info = {"name": d.name, "host": d.host, "model": d.model or "", "platform": get_profile(d.device_type).label}
    return render(request, "device_label.html", user, d=info, dev_id=dev_id,
                  qr=segno.make(payload, error="m").svg_data_uri(scale=5, border=2))
