"""Aplicación web OpenNCCM (FastAPI)."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import case, delete, func, or_, select
from starlette.middleware.sessions import SessionMiddleware

from . import reports, scheduler, services
from .auth import (ROLES, Forbidden, LoginRequired, TwoFactorSetupRequired, check_device, clear_settings_cache,
                   device_scope, filter_ids, require, start_session, user_group_ids)
from .config import BASE_DIR, TLS_CA
from .crypto import (decrypt, dummy_verify, encrypt, hash_password, needs_rehash, password_problem, session_secret,
                     verify_password)
from .db import (AuditLog, ChangeRequest, ComplianceResult, Configlet, ConfigVersion, Credential, Device,
                 DeviceGroup, Event, FirmwareImage, Policy, Rule, SessionLocal, Schedule, User, UserGroup,
                 Vulnerability, audit,
                 get_setting, init_db, now, set_setting)
from . import extauth
from .drivers import PROFILE_MAP, get_profile
from .i18n import LANGS, LangMiddleware, N_, _, _f
from .web import csv_response, flash, go, ip, log_action, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("opennccm")


@asynccontextmanager
def _note_version():
    """Recuerda la versión instalada. Si cambió respecto a la anterior (una actualización, no una instalación nueva),
    deja pendiente el aviso «Apoya OpenNCCM» para el primer administrador que entre."""
    from datetime import timedelta

    from . import __version__
    prev = get_setting("installed_version", "")
    if prev == __version__:
        return
    with SessionLocal() as db:  # instalaciones anteriores a este ajuste: si hay actividad de hace tiempo, es una actualización
        old = db.scalar(select(func.count(AuditLog.id)).where(AuditLog.created_at < now() - timedelta(hours=1))) or 0
    if (prev or old) and get_setting("donate_shown", "") != __version__:
        set_setting("donate_pending", __version__)
    set_setting("installed_version", __version__)


async def lifespan(_app):
    init_db()
    try:
        _note_version()
    except Exception:  # noqa: BLE001 — un aviso no debe impedir que arranque
        log.exception("No se pudo registrar la versión instalada")
    try:
        n = services.refresh_inventory_from_raw()
        if n:
            log.info("Inventario actualizado desde la última salida guardada en %d equipos", n)
    except Exception:  # noqa: BLE001
        log.exception("No se pudo releer el inventario guardado")
    try:
        from .compliance_packs import seed_new_packs
        with SessionLocal() as db:
            if db.scalar(select(func.count(User.id))):  # solo en instalaciones ya creadas (las nuevas usan «seed»)
                n = seed_new_packs(db)
                if n:
                    log.info("Cargados %d paquetes de cumplimiento nuevos", n)
                from .compliance_packs import drop_example_fixes
                n = drop_example_fixes(db)
                if n:
                    log.info("Quitadas %d correcciones de cumplimiento con direcciones de ejemplo", n)
    except Exception:  # noqa: BLE001
        log.exception("No se pudieron cargar los paquetes de cumplimiento nuevos")
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(title="OpenNCCM", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(LangMiddleware)  # va dentro de la sesión: lee el idioma elegido
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="strict",
                   https_only=os.environ.get("NCM_SECURE_COOKIE", "0") == "1", max_age=30 * 24 * 3600,  # cada sesión lleva su caducidad (auth.start_session)
                   session_cookie="opennccm")

# Cabeceras de seguridad en todas las respuestas (también sin nginx)
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if not request.url.path.startswith("/static/") and resp.headers.get("content-type", "").startswith("text/html"):
        h.setdefault("Cache-Control", "no-store")  # páginas con datos: no quedan en caché tras cerrar sesión
    return resp
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(LoginRequired)
async def _login_required(request, _exc):
    return go("/login")


@app.exception_handler(TwoFactorSetupRequired)
async def _setup_2fa(request, _exc):
    flash(request, _("Por seguridad, los administradores deben activar la verificación en dos pasos antes de continuar."),
          "error")
    return go("/account#dospasos")


@app.exception_handler(Forbidden)
async def _forbidden(request, _exc):
    return HTMLResponse(f"<h2>403 — {_('No tiene permisos para esta acción')}</h2><a href='/'>{_('Volver')}</a>", 403)


# --------------------------------------------------------------------------- login
_failed: dict[str, list[float]] = {}


ACCOUNT_LOCK_AFTER = 5          # intentos fallidos seguidos de una misma cuenta
ACCOUNT_LOCK_MINUTES = 15


@app.get("/login")
def login_form(request: Request):
    if request.session.pop("expired", None):
        flash(request, _("Su sesión se cerró por inactividad. Vuelva a ingresar."), "error")
    from .auth import remember_days
    return render(request, "login.html", None, ca_available=TLS_CA.is_file(), oidc=extauth.oidc_enabled(),
                  remember_days=remember_days(),
                  oidc_label=get_setting("oidc_button", "") or _("Entrar con Microsoft"), ldap=extauth.ldap_enabled())


def _finish_login(request: Request, db, user: User, addr: str):
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now()
    user.last_login_ip = addr
    db.commit()
    start_session(request, user)
    _failed.pop(addr, None)
    audit(user.username, "login", "", addr)


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    addr = ip(request)
    attempts = [t for t in _failed.get(addr, []) if time.time() - t < 900]
    if len(attempts) >= 10:
        flash(request, _("Demasiados intentos desde su dirección. Espere 15 minutos."), "error")
        return go("/login")
    username, password = form.get("username", "").strip(), form.get("password", "")
    request.state.remember = form.get("remember") == "on"  # «Recordarme» (auth.start_session)
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == username)).first()
        if user is None and "@" in username:  # cuentas de Azure / AD guardadas en minúsculas
            user = db.scalars(select(User).where(func.lower(User.username) == username.lower())).first()
        if (user is None or (user.auth_source or "local") == "ldap") and extauth.ldap_enabled() \
                and not (user and user.locked_until and user.locked_until > now()):
            return _login_ldap(request, db, user, username, password, addr, attempts)
        if user is None:
            dummy_verify(password)  # mismo tiempo de respuesta: no revela si el usuario existe
        elif (user.auth_source or "local") != "local":
            pass  # cuenta de un directorio externo: su contraseña local no sirve
        elif user.locked_until and user.locked_until > now():
            audit(username, "login_locked", "", addr)
            flash(request, _("La cuenta está bloqueada temporalmente por intentos fallidos. Inténtelo más tarde o pida a un administrador que la desbloquee."), "error")
            return go("/login")
        elif user.active and verify_password(password, user.password_hash):
            if needs_rehash(user.password_hash):  # contraseñas guardadas con un cifrado anterior
                user.password_hash = hash_password(password)
                db.commit()
            if user.totp_enabled:
                request.session.clear()
                request.session["pending_uid"] = user.id
                request.session["pending_at"] = int(time.time())
                request.session["pending_rem"] = int(bool(request.state.remember))  # se aplica tras el segundo paso
                return go("/login/2fa")
            _finish_login(request, db, user, addr)
            return go("/")
        elif user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= ACCOUNT_LOCK_AFTER:
                user.locked_until = now() + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
                user.failed_logins = 0
                services.add_event("warning", "Cuenta bloqueada",
                                   f"«{user.username}» bloqueada {ACCOUNT_LOCK_MINUTES} min tras {ACCOUNT_LOCK_AFTER} "
                                   f"intentos fallidos (último desde {addr}).")
            db.commit()
    attempts.append(time.time())
    _failed[addr] = attempts
    audit(username or "?", "login_failed", "", addr)
    flash(request, _("Usuario o contraseña incorrectos"), "error")
    return go("/login")


def _login_fail(request: Request, username: str, addr: str, attempts: list, msg: str | None = None):
    attempts.append(time.time())
    _failed[addr] = attempts
    audit(username or "?", "login_failed", "", addr)
    flash(request, msg or _("Usuario o contraseña incorrectos"), "error")
    return go("/login")


def _external_ok(request: Request, db, user: User, addr: str, source: str):
    """Tras validar en el directorio: segundo paso si el usuario lo tiene, o sesión directamente."""
    db.commit()  # primero se guarda la cuenta: la auditoría escribe con otra conexión (SQLite no admite dos a la vez)
    audit(user.username, f"login_{source}", "", addr)
    if user.totp_enabled:
        request.session.clear()
        request.session["pending_uid"] = user.id
        request.session["pending_at"] = int(time.time())
        request.session["pending_rem"] = int(bool(getattr(request.state, "remember", False)))
        return go("/login/2fa")
    _finish_login(request, db, user, addr)
    return go("/")


def _login_ldap(request: Request, db, user: User | None, username: str, password: str, addr: str, attempts: list):
    """Usuario y contraseña de Active Directory (cuentas que no son locales)."""
    try:
        ext = extauth.ldap_authenticate(username, password)
    except extauth.ExtAuthError as e:
        log.warning("Active Directory: %s", e)
        services.add_event("major", "Error de Active Directory", str(e))
        return _login_fail(request, username, addr, attempts,
                           _("No se pudo comprobar la cuenta en el directorio. Avise a un administrador."))
    if ext is None:
        if user is not None:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= ACCOUNT_LOCK_AFTER:
                user.locked_until = now() + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
                user.failed_logins = 0
            db.commit()
        else:
            dummy_verify(password)
        return _login_fail(request, username, addr, attempts)
    found, why = extauth.upsert_user(db, ext, "ldap")
    if found is None:
        db.rollback()
        return _login_fail(request, username, addr, attempts, {
            "norole": _("Su cuenta del directorio no pertenece a ningún grupo con acceso a OpenNCCM."),
            "inactive": _("Su cuenta está desactivada en OpenNCCM."),
            "local": _("Ya existe una cuenta local con ese nombre; ingrese con su contraseña de OpenNCCM.")}.get(why))
    return _external_ok(request, db, found, addr, "ldap")


def _redirect_uri(request: Request) -> str:
    """Dirección de vuelta que se registra en Azure: la de Ajustes o la de esta misma página (también tras nginx)."""
    fixed = get_setting("oidc_redirect", "")
    if fixed:
        return fixed
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}/login/oidc/callback"


@app.get("/login/oidc")
def login_oidc(request: Request):
    """«Entrar con Microsoft»: lleva al proveedor con state, nonce y PKCE."""
    if not extauth.oidc_enabled():
        return go("/login")
    try:
        url, pending = extauth.oidc_begin(extauth.oidc_settings(), _redirect_uri(request))
    except Exception as e:  # noqa: BLE001 — proveedor inaccesible o mal configurado
        log.warning("OpenID Connect: %s", e)
        flash(request, _("No se pudo contactar con el proveedor de identidad. Avise a un administrador."), "error")
        return go("/login")
    request.session["oidc"] = pending
    return RedirectResponse(url, status_code=303)


@app.get("/login/oidc/callback")
def login_oidc_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    addr = ip(request)
    pending = request.session.pop("oidc", None)
    if error:
        audit("?", "login_oidc_failed", error[:60], addr)
        flash(request, _f("El proveedor de identidad no permitió el ingreso: {0}", (error_description or error)[:200]), "error")
        return go("/login")
    if not pending or not code or not secrets.compare_digest(state, pending.get("state", "")) \
            or time.time() - pending.get("at", 0) > 600:
        flash(request, _("El inicio de sesión caducó o no es válido. Inténtelo de nuevo."), "error")
        return go("/login")
    try:
        ext = extauth.oidc_finish(extauth.oidc_settings(), _redirect_uri(request), code, pending)
    except Exception as e:  # noqa: BLE001 — token inválido, proveedor caído, etc.
        log.warning("OpenID Connect: %s", e)
        audit("?", "login_oidc_failed", str(e)[:120], addr)
        flash(request, _("No se pudo validar el inicio de sesión con el proveedor de identidad."), "error")
        return go("/login")
    with SessionLocal() as db:
        found, why = extauth.upsert_user(db, ext, "oidc")
        if found is None:
            db.rollback()
            audit(ext.username, "login_oidc_denied", why, addr)
            flash(request, {"norole": _("Su cuenta no pertenece a ningún grupo con acceso a OpenNCCM."),
                            "inactive": _("Su cuenta está desactivada en OpenNCCM."),
                            "local": _("Ya existe una cuenta local con ese nombre; ingrese con su contraseña de OpenNCCM.")
                            }.get(why, _("No se pudo iniciar sesión.")), "error")
            return go("/login")
        return _external_ok(request, db, found, addr, "oidc")


@app.get("/login/2fa")
def login_2fa_form(request: Request):
    if not request.session.get("pending_uid"):
        return go("/login")
    return render(request, "login_2fa.html", None)


@app.post("/login/2fa")
async def login_2fa(request: Request):
    form = await request.form()
    addr = ip(request)
    uid, started = request.session.get("pending_uid"), request.session.get("pending_at", 0)
    if not uid or time.time() - started > 300:
        request.session.clear()
        flash(request, _("Tiempo agotado. Ingrese de nuevo."), "error")
        return go("/login")
    with SessionLocal() as db:
        user = db.get(User, uid)
        if user and user.active and services_totp_verify(user, form.get("code", "")):
            _finish_login(request, db, user, addr)
            return go("/")
    tries = request.session.get("pending_tries", 0) + 1
    request.session["pending_tries"] = tries
    audit(user.username if user else "?", "login_2fa_failed", "", addr)
    if tries >= 5:
        request.session.clear()
        flash(request, _("Demasiados códigos incorrectos. Ingrese de nuevo."), "error")
        return go("/login")
    flash(request, _("Código incorrecto. Use el código de 6 dígitos que muestra su app de autenticación."), "error")
    return go("/login/2fa")


def services_totp_verify(user: User, code: str) -> bool:
    from .security import totp_verify
    return totp_verify(user, code)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


@app.get("/account")
def account(request: Request):
    return render(request, "account.html", require(request), themes=THEMES, accents=ACCENTS)


@app.post("/account")
async def account_save(request: Request):
    user = require(request)
    form = await request.form()
    with SessionLocal() as db:
        u = db.get(User, user.id)
        if (u.auth_source or "local") != "local":
            flash(request, _("Su contraseña se administra en el directorio de su organización."), "error")
            return go("/account")
        if not verify_password(form.get("current", ""), u.password_hash):
            flash(request, _("La contraseña actual no es correcta."), "error")
            return go("/account")
        problem = password_problem(form.get("new", ""), u.username)
        if problem:
            flash(request, problem, "error")
            return go("/account")
        if form.get("new") != form.get("new2", form.get("new")):
            flash(request, _("La nueva contraseña y su confirmación no coinciden."), "error")
            return go("/account")
        u.password_hash = hash_password(form["new"])
        u.session_version = (u.session_version or 0) + 1  # cierra sus sesiones abiertas en otros equipos
        db.commit()
        start_session(request, u)  # esta sesión sigue abierta
    log_action(request, user, "password_change")
    flash(request, _("Contraseña actualizada. Úsela la próxima vez que ingrese."))
    return go("/account")


THEMES = {"auto": N_("Automático (según el sistema)"), "claro": N_("Claro"), "oscuro": N_("Sala de control (oscuro)"),
          "contraste": N_("Alto contraste")}
ACCENTS = {"cobalto": N_("Cobalto"), "bosque": N_("Bosque"), "arena": N_("Arena"), "lavanda": N_("Lavanda"),
           "oceano": N_("Océano"), "atardecer": N_("Atardecer"), "grafito": N_("Grafito")}


@app.get("/lang/{code}")
def set_language(request: Request, code: str, next: str = "/"):
    """Cambia el idioma de la interfaz (y lo guarda en el usuario si hay sesión)."""
    if code not in LANGS:
        return go("/")
    request.session["lang"] = code
    uid = request.session.get("uid")
    if uid:
        with SessionLocal() as db:
            u = db.get(User, uid)
            if u:
                u.lang = code
                db.commit()
    # solo rutas locales: evita redirecciones a otros sitios
    return go(next if next.startswith("/") and not next.startswith("//") else "/")


@app.post("/account/theme")
async def account_theme(request: Request):
    user = require(request)
    form = await request.form()
    old = form.get("theme")
    from .db import OLD_THEMES
    theme, accent = OLD_THEMES.get(old, (old, form.get("accent")))  # acepta también los nombres antiguos
    with SessionLocal() as db:
        u = db.get(User, user.id)
        if old or "theme" in form:  # el selector de la barra superior puede cambiar solo el acento
            u.theme = theme if theme in THEMES else "auto"
        if accent or "accent" in form:
            u.accent = accent if accent in ACCENTS else None
        if form.get("density") in ("comfy", "compact"):
            u.density = form.get("density")
        db.commit()
        theme = u.theme
    if form.get("quiet"):  # cambio al instante desde la barra superior o Mi cuenta: sin aviso ni redirección
        return Response(status_code=204)
    flash(request, _f("Tema «{0}» aplicado.", _(THEMES[theme])))
    nxt = form.get("next") or "/account"
    return go(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/account")


# --------------------------------------------------------------------------- panel
@app.get("/")
def dashboard(request: Request):
    user = require(request)
    scope = device_scope(user)

    def scoped(stmt, col):
        return stmt if scope is None else stmt.where(col.in_(scope))
    with SessionLocal() as db:
        cnt = lambda *w: db.scalar(scoped(select(func.count(Device.id)).where(*w), Device.id)) or 0  # noqa: E731
        total, ok = cnt(), cnt(Device.last_status == "ok")
        err, conflicts = cnt(Device.last_status == "error"), cnt(Device.startup_conflict.is_(True))
        drift = cnt(Device.baseline_drift.is_(True))
        since = now() - timedelta(hours=24)
        changes24 = db.scalar(scoped(select(func.count(ConfigVersion.id)).where(
            ConfigVersion.created_at >= since, ConfigVersion.kind == "running"), ConfigVersion.device_id)) or 0
        cpass = db.scalar(scoped(select(func.count(ComplianceResult.id)).where(ComplianceResult.passed.is_(True)),
                                 ComplianceResult.device_id)) or 0
        cfail = db.scalar(scoped(select(func.count(ComplianceResult.id)).where(ComplianceResult.passed.is_(False)),
                                 ComplianceResult.device_id)) or 0
        alarms = db.scalar(scoped(select(func.count(Event.id)).where(Event.acknowledged.is_(False),
                                                                     Event.severity != "info"), Event.device_id)) or 0
        vcrit = db.scalar(scoped(select(func.count(func.distinct(Vulnerability.device_id))).where(
            Vulnerability.severity.in_(["CRITICAL", "HIGH"])), Vulnerability.device_id)) or 0
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
        events = db.scalars(scoped(select(Event), Event.device_id).order_by(Event.id.desc()).limit(10)).all()
        recent = db.scalars(scoped(select(ConfigVersion).where(ConfigVersion.kind == "running"),
                                   ConfigVersion.device_id).order_by(ConfigVersion.id.desc()).limit(10)).all()
        for v in recent:
            v.device.name  # carga la relación antes de cerrar la sesión
        for e in events:
            e.device.name if e.device else None  # carga la relación antes de cerrar la sesión
        # indicadores del panel «Señal»: nombres de los equipos afectados y tendencia de equipos (8 semanas)
        devs = db.scalars(scoped(select(Device), Device.id).order_by(Device.name)).all()
        week_ago = now() - timedelta(days=7)
        # fecha de alta de cada equipo = la de su primera versión respaldada
        first = dict(db.execute(select(ConfigVersion.device_id, func.min(ConfigVersion.created_at))
                                .group_by(ConfigVersion.device_id)).all())
        added = [first.get(d.id) for d in devs]
        new_week = sum(1 for a in added if a and a >= week_ago)
        dev_trend = [sum(1 for a in added if a and a <= now() - timedelta(weeks=w)) for w in range(7, -1, -1)]
        backed24 = sum(1 for d in devs if d.last_status == "ok" and d.last_backup_at and d.last_backup_at >= since)
        names = lambda ds: ", ".join(d.name for d in ds[:2]) + (f" +{len(ds) - 2}" if len(ds) > 2 else "")  # noqa: E731
        err_names = names([d for d in devs if d.last_status == "error"])
        drift_names = names([d for d in devs if d.baseline_drift])
        conflict_names = names([d for d in devs if d.startup_conflict])
        oldest = db.scalar(select(ChangeRequest.created_at).where(ChangeRequest.status == "pending")
                           .order_by(ChangeRequest.created_at).limit(1))
        crit_cves = db.scalar(scoped(select(func.count(Vulnerability.id)).where(Vulnerability.severity == "CRITICAL"),
                                     Vulnerability.device_id)) or 0
    from . import eol as eol_mod, health
    data = reports.analytics(scope, 30)
    unsupported = [(d, e) for d, e, st in eol_mod.report(scope) if st in ("sin_soporte", "fin_mantenimiento")]
    comp = round(100 * cpass / (cpass + cfail)) if (cpass + cfail) else None
    server = _server_panel() if user.role == "admin" else None
    days = data["per_day"]
    peak = max(days, key=lambda dv: dv[1]) if days else ("", 0)
    vend = {}
    for v in recent:
        prof = PROFILE_MAP.get(v.device.device_type)
        vend[v.device_id] = (prof.vendor if prof else v.device.device_type)[:3].upper()
    return render(request, "dashboard.html", user, total=total, ok=ok, err=err, conflicts=conflicts,
                  changes24=changes24, comp=comp, alarms=alarms, vcrit=vcrit, pending=pending, drift=drift,
                  events=events, recent=recent, h=health.summary(scope, user.role == "admin", server),
                  new_week=new_week, dev_trend=dev_trend, backed24=backed24, err_names=err_names,
                  drift_names=drift_names, conflict_names=conflict_names, unsupported=unsupported, oldest=oldest,
                  crit_cves=crit_cves, days=days, peak=peak, vend=vend, groups_n=len({d.group_id for d in devs if d.group_id}))


def _server_panel() -> dict:
    """Rendimiento del servidor de OpenNCCM para el panel (solo administradores)."""
    from datetime import datetime

    from . import charts, metrics
    snap = metrics.snapshot()
    buckets: dict[int, list] = {}
    for ts, cpu, mem in snap["history"]:  # promedio cada 5 minutos
        buckets.setdefault(ts // 300, []).append((cpu, mem))
    cpu_pts, mem_pts = [], []
    for key in sorted(buckets):
        label = datetime.fromtimestamp(key * 300).strftime("%H:%M")
        vals = buckets[key]
        cpu_pts.append((label, sum(v[0] for v in vals) / len(vals)))
        mem_pts.append((label, sum(v[1] for v in vals) / len(vals)))
    hb = metrics.human_bytes
    return {
        "snap": snap, "uptime": metrics.human_uptime(snap["uptime"]),
        "load": " / ".join(f"{x:.2f}" for x in snap["load"]) if snap["load"] else "—",
        "cpu_chart": charts.line(cpu_pts, "%", title=_("Uso de CPU")),
        "mem_chart": charts.line(mem_pts, "%", title=_("Uso de memoria RAM")),
        "mem_meter": charts.meter(snap["mem_used"], snap["mem_total"], _("Memoria RAM"),
                                  _f("{0} usados de {1} · {2} libres", hb(snap['mem_used']), hb(snap['mem_total']),
                                     hb(snap['mem_total'] - snap['mem_used']))),
        "disk_meter": charts.meter(snap["disk_used"], snap["disk_total"], _("Disco"),
                                   _f("{0} usados de {1} · {2} libres ({3})", hb(snap['disk_used']),
                                      hb(snap['disk_total']), hb(snap['disk_free']), snap['disk_path'])),
        "swap_meter": charts.meter(snap["swap_used"], snap["swap_total"], _("Memoria de intercambio (swap)"),
                                   _f("{0} usados de {1}", hb(snap['swap_used']), hb(snap['swap_total'])))
        if snap["swap_total"] else "",
        "data_size": hb(snap["data_size"]), "app_rss": hb(snap["app_rss"]),
        "cpu_vals": [v for _l, v in cpu_pts], "mem_vals": [v for _l, v in mem_pts],
        "space": [(_("Disco"), _f("{0} de {1} · {2} libres", hb(snap["disk_used"]), hb(snap["disk_total"]),
                                  hb(snap["disk_free"])), snap["disk_pct"]),
                  (_("Memoria RAM"), _f("{0} de {1}", hb(snap["mem_used"]), hb(snap["mem_total"])), snap["mem_pct"])]
                 + ([(_("Swap"), _f("{0} de {1}", hb(snap["swap_used"]), hb(snap["swap_total"])),
                      100 * snap["swap_used"] / snap["swap_total"])] if snap["swap_total"] else []),
    }


# --------------------------------------------------------------------------- dispositivos
@app.get("/devices")
def devices(request: Request, q: str = "", group: int = 0, status: str = ""):
    user = require(request)
    with SessionLocal() as db:
        stmt = select(Device).order_by(Device.name)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Device.name.ilike(like), Device.host.ilike(like), Device.model.ilike(like),
                                  Device.serial.ilike(like), Device.os_version.ilike(like),
                                  Device.location.ilike(like)))
        if group:
            stmt = stmt.where(Device.group_id == group)
        from . import eol as eol_mod
        scope0 = device_scope(user)
        unsupported = {d.id for d, _e, st in eol_mod.report(scope0) if st in ("sin_soporte", "fin_mantenimiento")}
        if status == "conflict":
            stmt = stmt.where(Device.startup_conflict.is_(True))
        elif status == "eol":
            stmt = stmt.where(Device.id.in_(unsupported or {0}))
        elif status == "drift":
            stmt = stmt.where(Device.baseline_drift.is_(True))
        elif status:
            stmt = stmt.where(Device.last_status == status)
        scope = device_scope(user)
        if scope is not None:
            stmt = stmt.where(Device.id.in_(scope))
        devs = db.scalars(stmt).all()
        for d in devs:
            d.group.name if d.group else None  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        # recuentos de los chips de estado (sobre los equipos visibles, sin el filtro de estado)
        base = select(Device)
        if scope is not None:
            base = base.where(Device.id.in_(scope))
        if group:
            base = base.where(Device.group_id == group)
        every = db.scalars(base).all()
        counts = {"": len(every), "ok": sum(d.last_status == "ok" for d in every),
                  "error": sum(d.last_status == "error" for d in every),
                  "pending": sum(d.last_status == "pending" for d in every),
                  "conflict": sum(bool(d.startup_conflict) for d in every), "drift": sum(bool(d.baseline_drift) for d in every),
                  "eol": sum(d.id in unsupported for d in every)}
        # origen del último respaldo de cada equipo (manual, programado, syslog, push) y último respaldo global
        latest_ids = select(func.max(ConfigVersion.id)).group_by(ConfigVersion.device_id)
        src = dict(db.execute(select(ConfigVersion.device_id, ConfigVersion.trigger)
                              .where(ConfigVersion.id.in_(latest_ids))).all())
        last_global = max((d.last_backup_at for d in every if d.last_backup_at), default=None)
        n_groups = len({d.group_id for d in every if d.group_id})
    from .console import console_allowed
    return render(request, "devices.html", user, devices=devs, groups=groups, q=q, group=group, status=status,
                  counts=counts, can_console=console_allowed(user), src=src, last_global=last_global, n_groups=n_groups)


@app.post("/devices/bulk")
async def devices_bulk(request: Request):
    """Acciones sobre los equipos seleccionados en la lista: respaldar, auditar o mover a un grupo."""
    form = await request.form()
    action = form.get("action", "")
    user = require(request, "admin" if action == "move" else "operator")
    ids = filter_ids(user, [int(i) for i in form.getlist("ids") if str(i).isdigit()])
    back = form.get("next") or "/devices"
    back = back if back.startswith("/") and not back.startswith("//") else "/devices"
    if not ids:
        flash(request, _("Seleccione al menos un dispositivo"), "error")
        return go(back)
    if action == "backup":
        services.jobs.submit(services.backup_many, ids, "manual", user.username)
        log_action(request, user, "backup_selected", f"{ids}")
        flash(request, _f("Respaldo de {0} equipos lanzado en segundo plano", len(ids)))
    elif action == "audit":
        def _audit():
            for i in ids:
                services.run_compliance(i)
        services.jobs.submit(_audit)
        log_action(request, user, "compliance_selected", f"{ids}")
        flash(request, _f("Auditoría de cumplimiento lanzada para {0} equipos", len(ids)))
    elif action == "cve":
        services.jobs.submit(services.scan_cves_many, ids)
        log_action(request, user, "cve_selected", f"{ids}")
        flash(request, _f("Búsqueda de CVE lanzada para {0} equipos", len(ids)))
    elif action == "move":
        gid = int(form.get("group_id") or 0) or None
        with SessionLocal() as db:
            if gid and db.get(DeviceGroup, gid) is None:
                flash(request, _("El grupo no existe"), "error")
                return go(back)
            for d in db.scalars(select(Device).where(Device.id.in_(ids))):
                d.group_id = gid
            db.commit()
        log_action(request, user, "devices_move", f"{ids} -> grupo {gid}")
        flash(request, _f("{0} equipos movidos de grupo", len(ids)))
    else:
        flash(request, _("Acción desconocida"), "error")
    return go(back)


def _device_form_ctx(db):
    return {"groups": db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all(),
            "credentials": db.scalars(select(Credential).order_by(Credential.name)).all()}


@app.get("/devices/new")
def device_new(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        return render(request, "device_form.html", user, device=None, **_device_form_ctx(db))


@app.get("/devices/{dev_id}/edit")
def device_edit(request: Request, dev_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        return render(request, "device_form.html", user, device=db.get(Device, dev_id), **_device_form_ctx(db))


def _apply_device_form(dev: Device, form):
    dev.name = form["name"].strip()
    dev.host = form["host"].strip()
    dev.device_type = form["device_type"]
    dev.protocol = form.get("protocol", "ssh")
    dev.port = int(form.get("port") or (23 if dev.protocol == "telnet" else 22))
    dev.credential_id = int(form["credential_id"]) if form.get("credential_id") else None
    dev.group_id = int(form["group_id"]) if form.get("group_id") else None
    dev.location = form.get("location") or None
    dev.notes = form.get("notes") or None
    dev.enabled = form.get("enabled") == "on"
    dev.auto_rollback = form.get("auto_rollback") == "on"
    dev.custom_backup_cmd = (form.get("custom_backup_cmd") or "").strip() or None
    web = (form.get("web_url") or "").strip()
    if web and not web.lower().startswith(("http://", "https://")):
        web = "https://" + web
    dev.web_url = web[:255] or None
    dev.web_enabled = form.get("web_enabled", "1") != "0"  # sin el campo (API, versiones anteriores): sí


@app.post("/devices/new")
@app.post("/devices/{dev_id}/edit")
async def device_save(request: Request, dev_id: int | None = None):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        dev = db.get(Device, dev_id) if dev_id else Device()
        _apply_device_form(dev, form)
        new_group = (form.get("new_group") or "").strip()[:100]
        if new_group:  # crear el grupo desde el propio formulario (o reutilizar uno con ese nombre)
            g = next((x for x in db.scalars(select(DeviceGroup)) if x.name.lower() == new_group.lower()), None)
            if g is None:
                g = DeviceGroup(name=new_group)
                db.add(g)
                db.flush()
            dev.group_id = g.id
        if not dev_id:
            db.add(dev)
        try:
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            flash(request, _f("No se pudo guardar: {0} (¿nombre duplicado?)", e.__class__.__name__), "error")
            return go("/devices/new" if not dev_id else f"/devices/{dev_id}/edit")
        log_action(request, user, "device_save", f"{dev.name} {dev.host} {dev.device_type}")
        new_id = dev.id
    flash(request, _("Dispositivo guardado"))
    if not dev_id:
        services.pool.submit(services.backup_device, new_id, "manual", user.username)
        flash(request, _("Se lanzó el primer respaldo en segundo plano"))
    return go(f"/devices/{new_id}")


@app.post("/devices/{dev_id}/delete")
def device_delete(request: Request, dev_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev:
            name = dev.name
            db.delete(dev)
            db.commit()
            log_action(request, user, "device_delete", name)
    flash(request, _("Dispositivo eliminado"))
    return go("/devices")


@app.get("/devices/import")
def device_import_form(request: Request):
    """Importación CSV (se abre en un panel lateral desde «Agregar»), con vista previa en el navegador."""
    user = require(request, "admin")
    with SessionLocal() as db:
        creds = [c.name for c in db.scalars(select(Credential).order_by(Credential.name))]
        groups = [g.name for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
    return render(request, "devices_import.html", user, creds=creds, groups=groups, types=sorted(PROFILE_MAP))


@app.post("/devices/import")
async def device_import(request: Request):
    """CSV: name,host,device_type,credential,group,port,protocol,location"""
    user = require(request, "admin")
    form = await request.form()
    raw = (await form["file"].read()).decode("utf-8-sig")
    created, errors = 0, []
    with SessionLocal() as db:
        creds = {c.name: c.id for c in db.scalars(select(Credential))}
        groups = {g.name: g.id for g in db.scalars(select(DeviceGroup))}
        for i, row in enumerate(csv.DictReader(io.StringIO(raw)), start=2):
            try:
                if row["device_type"] not in PROFILE_MAP:
                    raise ValueError(f"device_type desconocido '{row['device_type']}'")
                gname = (row.get("group") or "").strip()
                if gname and gname not in groups:
                    g = DeviceGroup(name=gname)
                    db.add(g)
                    db.flush()
                    groups[gname] = g.id
                proto = (row.get("protocol") or "ssh").strip()
                db.add(Device(name=row["name"].strip(), host=row["host"].strip(), device_type=row["device_type"],
                              credential_id=creds.get((row.get("credential") or "").strip()),
                              group_id=groups.get(gname), protocol=proto,
                              port=int(row.get("port") or (23 if proto == "telnet" else 22)),
                              location=row.get("location") or None))
                db.commit()
                created += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()  # solo descarta esta fila: las anteriores ya están confirmadas
                groups = {g.name: g.id for g in db.scalars(select(DeviceGroup))}
                errors.append(f"línea {i}: {str(e).splitlines()[0][:200]}")
    log_action(request, user, "device_import", f"{created} creados")
    flash(request, _f("{0} dispositivos importados", created))
    for e in errors[:10]:
        flash(request, e, "error")
    return go("/devices")


@app.get("/devices/{dev_id}")
def device_detail(request: Request, dev_id: int, a: int = 0, b: int = 0, mode: str = "sbs", ctx: int = 3):
    user = require(request)
    check_device(user, dev_id)
    from .firmware import list_images, supports_upgrade
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if not dev:
            return go("/devices")
        images = [i for i in list_images() if not i.device_type_filter
                  or re.search(i.device_type_filter, dev.device_type)] if supports_upgrade(dev.device_type) else []
        (dev.group, dev.credential)  # carga la relación antes de cerrar la sesión
        versions = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id)
                              .order_by(ConfigVersion.id.desc()).limit(200)).all()
        comp = db.scalars(select(ComplianceResult).where(ComplianceResult.device_id == dev_id)).all()
        for c in comp:
            (c.rule.name, c.policy.name)  # carga la relación antes de cerrar la sesión
        vulns = db.scalars(select(Vulnerability).where(Vulnerability.device_id == dev_id)
                           .order_by(Vulnerability.score.desc().nulls_last())).all()
        events = db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc()).limit(30)).all()
        inventory = decrypt(dev.inventory_raw_enc) if dev.inventory_raw_enc else ""
        # línea de tiempo «Señal»: número de versión por equipo (v1…vN), origen, tamaño anormal y comparación A/B
        total_v = db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == dev_id)) or 0
        trig = {"manual": _("Manual"), "scheduled": _("Programado"), "syslog": "Syslog", "push": "Push"}
        tl = []
        for i, v in enumerate(versions):
            prev = next((x for x in versions[i + 1:] if x.kind == v.kind), None)
            tl.append({"id": v.id, "n": total_v - i, "kind": v.kind, "src": trig.get(v.trigger, v.trigger),
                       "who": v.username or v.trigger, "when": v.created_at, "label": v.label,
                       "base": v.id == dev.baseline_version_id, "inc": bool(prev and prev.size and v.size < prev.size * 0.5),
                       "added": v.added, "removed": v.removed, "first": i == total_v - 1})
        vnum = {t["id"]: t["n"] for t in tl}
        ids = [v.id for v in versions]
        va_id = a if a in ids else (ids[1] if len(ids) > 1 else (ids[0] if ids else 0))
        vb_id = b if b in ids else (ids[0] if ids else 0)
        if va_id and vb_id and va_id > vb_id:
            va_id, vb_id = vb_id, va_id  # A siempre la más antigua
        cmp = None
        if va_id and vb_id:
            va, vb = db.get(ConfigVersion, va_id), db.get(ConfigVersion, vb_id)
            ta, tb = decrypt(va.content_enc), decrypt(vb.content_enc)
            add_n, del_n = services.diff_stats(ta, tb)
            cmp = {"a": va, "b": vb, "rows": services.side_by_side(ta, tb, ctx if ctx < 9999 else 100000),
                   "summary": services.diff_summary(ta, tb), "n": add_n + del_n}
        drift_n = 0
        if dev.baseline_drift and dev.baseline_version_id and versions:
            base_v = db.get(ConfigVersion, dev.baseline_version_id)
            last_run = next((v for v in versions if v.kind == "running"), None)
            if base_v and last_run:
                drift_n = sum(services.diff_stats(decrypt(base_v.content_enc), decrypt(last_run.content_enc)))
        audit_rows = db.scalars(select(AuditLog).where(or_(AuditLog.detail.like(f"%{dev.name}%"),
                                                           AuditLog.detail.like(f"%device={dev_id}%")))
                                .order_by(AuditLog.id.desc()).limit(20)).all()
    from . import eol
    with SessionLocal() as db:
        entry = eol.match_entry(dev, list(db.scalars(select(eol.EolEntry))))
    # cumplimiento agrupado por política: las infracciones primero (críticas antes) y «Corregir N» por política
    sev_rank = {"critical": 0, "major": 1, "minor": 2}
    groups: dict[int, dict] = {}
    for c in comp:
        g = groups.setdefault(c.policy_id, {"policy": c.policy, "results": [], "fails": 0, "fixable": 0, "crit": 0})
        g["results"].append(c)
        if not c.passed:
            g["fails"] += 1
            g["fixable"] += bool(c.rule.remediation)
            g["crit"] += c.rule.severity == "critical"
    for g in groups.values():
        g["results"].sort(key=lambda c: (c.passed, sev_rank.get(c.rule.severity, 3), c.rule.name))
    comp_groups = sorted(groups.values(), key=lambda g: (-g["crit"], -g["fails"], g["policy"].name))
    from . import fwupdates as _fwu
    from .routes_ops import FW_SOURCES as _FW_SOURCES
    from .console import console_allowed
    return render(request, "device.html", user, can_console=console_allowed(user, dev.id), device=dev,
                  versions=versions, comp=comp, vulns=vulns, comp_groups=comp_groups,
                  comp_fails=sum(g["fails"] for g in comp_groups),
                  vuln_high=sum(1 for v in vulns if (v.severity or "").upper() in ("CRITICAL", "HIGH")),
                  events=events, inventory=inventory, profile=get_profile(dev.device_type), images=images,
                  eol_entry=entry, eol_status=eol.status(entry), eol_labels=eol.STATUS_LABEL,
                  eol_sev=eol.STATUS_SEV, tl=tl, vnum=vnum, total_v=total_v, cmp=cmp, mode=mode, ctx=ctx,
                  crit_cves=sum(1 for v in vulns if (v.severity or "").upper() == "CRITICAL"),
                  open_events=sum(1 for e in events if not e.acknowledged), audit_rows=audit_rows, drift_n=drift_n,
                  audited_at=max((c.checked_at for c in comp if c.checked_at), default=None),
                  cve_at=services.cve_scanned_at(dev.id) or max((v.found_at for v in vulns if v.found_at),
                                                                    default=None),
                  cve_state=services.cve_scan_state(dev.id),
                  fwu=_fwu.for_device(dev.id), fw_state=_fwu.state(), fw_dl=_fwu.download_state(dev.id), fw_sources=_FW_SOURCES,
                  fw_have=_fwu.in_library(dev.device_type, (_fwu.for_device(dev.id) or {}).get("target")),
                  latest_run=next((t["id"] for t in tl if t["kind"] == "running"), None))


@app.post("/devices/{dev_id}/backup")
def device_backup(request: Request, dev_id: int):
    user = require(request, "operator")
    check_device(user, dev_id)
    res = services.backup_device(dev_id, "manual", user.username)
    log_action(request, user, "backup", f"device={dev_id} -> {res}")
    msgs = {"changed": _("Respaldo realizado: se detectaron cambios"), "unchanged": _("Respaldo realizado: sin cambios"),
            "first": _("Primer respaldo guardado"), "error": _("El respaldo falló (ver detalle)"),
            "busy": _("El equipo está ocupado con otra tarea")}
    flash(request, msgs.get(res, res), "error" if res in ("error", "busy") else "ok")
    return go(f"/devices/{dev_id}")


@app.post("/backup/all")
async def backup_all(request: Request):
    user = require(request, "operator")
    form = await request.form()
    gid = int(form.get("group") or 0) or None
    ids = filter_ids(user, services.device_ids(gid))
    services.jobs.submit(services.backup_many, ids, "manual", user.username)
    log_action(request, user, "backup_all", f"{len(ids)} equipos")
    flash(request, _f("Respaldo de {0} equipos lanzado en segundo plano", len(ids)))
    return go("/devices")


@app.post("/devices/{dev_id}/compliance")
def device_compliance(request: Request, dev_id: int):
    user = require(request, "operator")
    check_device(user, dev_id)
    p, f = services.run_compliance(dev_id)
    flash(request, _f("Cumplimiento: {0} reglas OK, {1} infracciones", p, f), "ok" if f == 0 else "error")
    return go(f"/devices/{dev_id}#compliance")


@app.post("/devices/{dev_id}/cve")
def device_cve(request: Request, dev_id: int):
    user = require(request, "operator")
    check_device(user, dev_id)
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        has_version = bool(dev and dev.os_version)
    if not has_version:
        flash(request, _("Sin versión de firmware en inventario: haga un respaldo primero"), "error")
    elif services.start_cve_scan(dev_id):
        log_action(request, user, "cve_scan", f"device={dev_id}")
    return go(f"/devices/{dev_id}#vulns")


@app.get("/devices/{dev_id}/cve/status")
def device_cve_status(request: Request, dev_id: int):
    """Estado de la búsqueda de CVE en segundo plano (la pestaña Vulnerabilidades lo consulta mientras busca)."""
    user = require(request)
    check_device(user, dev_id)
    st = services.cve_scan_state(dev_id)
    return {"running": st["running"] is not None, "last_at": (st["last"] or {}).get("at")}


@app.post("/cve/all")
def cve_all(request: Request):
    user = require(request, "operator")
    ids = filter_ids(user, services.device_ids())
    services.jobs.submit(services.scan_cves_many, ids)
    log_action(request, user, "cve_scan_all")
    flash(request, _f("Escaneo de vulnerabilidades lanzado para {0} equipos (puede tardar)", len(ids)))
    return go("/vulns")


# --------------------------------------------------------------------------- versiones y diff
@app.get("/versions/{ver_id}")
def version_view(request: Request, ver_id: int, raw: int = 0):
    user = require(request)
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        if not v:
            return go("/")
        check_device(user, v.device_id)
        text = decrypt(v.content_enc)
        v.device.name  # carga la relación antes de cerrar la sesión
        is_baseline = v.device.baseline_version_id == v.id
        if raw:
            log_action(request, user, "config_download", f"{v.device.name} v{v.id}")
            fname = f"{v.device.name}_{v.kind}_{v.created_at:%Y%m%d-%H%M}.txt"
            return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{fname}"'})
        prev = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == v.device_id,
                                                      ConfigVersion.kind == v.kind, ConfigVersion.id < v.id)
                          .order_by(ConfigVersion.id.desc()).limit(1)).first()
        current = services.latest_version(db, v.device_id, v.kind)
        small = _suspiciously_small(v, current)
        n = db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == v.device_id,
                                                                  ConfigVersion.id <= v.id)) or 1
    return render(request, "version.html", user, v=v, text=text, prev=prev, is_baseline=is_baseline,
                  small=small, current=current, n=n, lines=text.splitlines())


def _suspiciously_small(v, current) -> bool:
    """Una versión mucho más pequeña que la actual suele ser un respaldo cortado: revertir a ella borraría lo que falta."""
    return bool(current and current.id != v.id and current.size and v.size < current.size * 0.5)


@app.post("/versions/{ver_id}/label")
async def version_label(request: Request, ver_id: int):
    user = require(request, "operator")
    form = await request.form()
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        check_device(user, v.device_id)
        v.label = (form.get("label") or "").strip() or None
        db.commit()
    log_action(request, user, "version_label", f"v{ver_id}={form.get('label')}")
    return go(f"/versions/{ver_id}")


@app.post("/versions/{ver_id}/baseline")
def version_baseline(request: Request, ver_id: int):
    """Marca la versión como línea base del equipo (configuración de referencia)."""
    user = require(request, "operator")
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        check_device(user, v.device_id)
        if v.kind != "running":
            flash(request, _("Solo una configuración en ejecución puede ser línea base"), "error")
            return go(f"/versions/{ver_id}")
        dev = v.device
        dev.baseline_version_id = v.id
        v.label = v.label or "baseline"
        latest = services.latest_version(db, dev.id)
        dev.baseline_drift = latest is not None and services.versions_differ(v, latest, get_profile(dev.device_type))
        db.commit()
        name = dev.name
    log_action(request, user, "baseline_set", f"{name} -> v{ver_id}")
    flash(request, _f("Versión #{0} marcada como línea base de {1}", ver_id, name))
    return go(f"/versions/{ver_id}")


@app.get("/devices/{dev_id}/baseline-diff")
def baseline_diff(request: Request, dev_id: int):
    user = require(request)
    check_device(user, dev_id)
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        latest = services.latest_version(db, dev_id)
        if not dev.baseline_version_id or not latest:
            flash(request, _("El equipo no tiene línea base definida"), "error")
            return go(f"/devices/{dev_id}")
    return go(f"/devices/{dev_id}?a={dev.baseline_version_id}&b={latest.id}#versions")


@app.get("/diff")
def diff_view(request: Request, a: int = 0, b: int = 0, ctx: int = 3, view: str = "side", raw: int = 0):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        if not a or not b:  # «Comparar configuraciones» sin elegir: las dos últimas versiones del primer equipo visible
            q0 = select(ConfigVersion).where(ConfigVersion.kind == "running").order_by(ConfigVersion.id.desc()).limit(1)
            if scope is not None:
                q0 = q0.where(ConfigVersion.device_id.in_(scope))
            last = db.scalars(q0).first()
            if last:
                two = db.scalars(select(ConfigVersion.id).where(ConfigVersion.device_id == last.device_id)
                                 .order_by(ConfigVersion.id.desc()).limit(2)).all()
                a, b = (two[-1], two[0]) if not a and not b else (a or two[-1], b or two[0])
        va, vb = db.get(ConfigVersion, a), db.get(ConfigVersion, b)
        if not va or not vb:
            flash(request, _("Seleccione dos versiones"), "error")
            return go("/")
        check_device(user, va.device_id)
        check_device(user, vb.device_id)
        (va.device.name, vb.device.name)  # carga la relación antes de cerrar la sesión
        ta, tb = decrypt(va.content_enc), decrypt(vb.content_enc)
        lines = services.diff_lines(ta, tb, f"{va.device.name} #{va.id}", f"{vb.device.name} #{vb.id}", ctx)
        if raw:  # «.diff»: descarga en formato diff unificado
            fname = f"{va.device.name}_v{va.id}_{vb.device.name}_v{vb.id}.diff"
            body = "\n".join(lines) + "\n"
            return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{fname}"'})
        rows = services.side_by_side(ta, tb, ctx if ctx < 9999 else 100000)
        # selectores del prototipo: equipo (izquierda / derecha) y sus versiones, numeradas v1…vN por equipo
        devs = [d for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]
        vers: dict[int, list] = {}
        for d in devs:
            vs = db.execute(select(ConfigVersion.id, ConfigVersion.kind, ConfigVersion.created_at)
                            .where(ConfigVersion.device_id == d.id).order_by(ConfigVersion.id)).all()
            vers[d.id] = [{"id": i, "n": k + 1, "kind": kind, "when": when} for k, (i, kind, when) in enumerate(vs)][::-1][:60]
        vn = {x["id"]: x["n"] for lst in vers.values() for x in lst}
        vn.setdefault(va.id, va.id)
        vn.setdefault(vb.id, vb.id)
    added, removed = services.diff_stats(ta, tb)
    cmp = {"a": va, "b": vb, "rows": rows, "summary": services.diff_summary(ta, tb), "n": added + removed}
    return render(request, "diff.html", user, va=va, vb=vb, cmp=cmp, ctx=ctx, view=view, devs=devs, vers=vers, vn=vn,
                  mode="u" if view == "unified" else "sbs", vers_js={str(k): [x["id"] for x in v] for k, v in vers.items()})


@app.post("/versions/{ver_id}/rollback")
async def version_rollback(request: Request, ver_id: int):
    user = require(request, "operator")
    form = await request.form()
    with SessionLocal() as db:
        v = db.get(ConfigVersion, ver_id)
        check_device(user, v.device_id)
        dev_id, name = v.device_id, v.device.name
        small = _suspiciously_small(v, services.latest_version(db, dev_id, v.kind))
    if small and form.get("confirm") != "1":
        flash(request, _f("No se creó la reversión: la versión #{0} es mucho más pequeña que la actual y probablemente es un respaldo incompleto. Revise el aviso de esta página.", ver_id), "error")
        return go(f"/versions/{ver_id}")
    cmds = services.rollback_commands(dev_id, ver_id)
    if not cmds:
        flash(request, _("La versión seleccionada es igual a la actual: nada que revertir"))
        return go(f"/versions/{ver_id}")
    cr_id = _create_cr(user, f"Reversión de {name} a la versión #{ver_id}", "\n".join(cmds), {}, [dev_id],
                       is_rollback=True)
    log_action(request, user, "rollback_request", f"{name} -> v{ver_id}")
    flash(request, _("Se generó la solicitud de reversión. Revise los comandos antes de aprobar."))
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- búsqueda
@app.get("/search")
def search(request: Request, q: str = "", regex: int = 0, cs: int = 0, group: int = 0, latest: int = 0, sent: int = 0):
    """Buscar texto o expresiones regulares en las configuraciones (como el prototipo: grupo, mayúsculas y versiones)."""
    user = require(request)
    if not sent:  # sin enviar el formulario: por omisión, solo la última versión
        latest = 1
    results, err = [], ""
    with SessionLocal() as db:
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        if q:
            try:
                pat = re.compile(q if regex else re.escape(q), 0 if cs else re.IGNORECASE)
            except re.error as e:
                err = _f("La expresión regular no es válida: {0}", e)
                pat = None
            scope = device_scope(user)
            for dev in db.scalars(select(Device).order_by(Device.name)) if pat else []:
                if (scope is not None and dev.id not in scope) or (group and dev.group_id != group):
                    continue
                vs = [services.latest_version(db, dev.id)] if latest else db.scalars(
                    select(ConfigVersion).where(ConfigVersion.device_id == dev.id, ConfigVersion.kind == "running")
                    .order_by(ConfigVersion.id.desc()).limit(20)).all()
                for v in vs:
                    if not v:
                        continue
                    lines = decrypt(v.content_enc).splitlines()
                    hits = [i for i, line in enumerate(lines) if pat.search(line)]
                    if hits:
                        n = db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == dev.id,
                                                                                  ConfigVersion.id <= v.id))
                        prof = PROFILE_MAP.get(dev.device_type)
                        results.append({"dev": dev, "v": v, "n": n, "lines": lines, "hits": hits[:50],
                                        "total": len(hits), "vend": (prof.vendor if prof else dev.device_type)[:3].upper()})
                        break
    return render(request, "search.html", user, q=q, regex=regex, cs=cs, group=group, latest=latest, groups=groups,
                  results=results, err=err, total=sum(r["total"] for r in results))


# --------------------------------------------------------------------------- credenciales y grupos
@app.get("/credentials")
def credentials(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        creds = db.scalars(select(Credential).order_by(Credential.name)).all()
        usage: dict[int, dict] = {}
        for d in db.scalars(select(Device)):
            if d.credential_id:
                u = usage.setdefault(d.credential_id, {"n": 0, "ok": 0, "err": 0})
                u["n"] += 1
                if d.last_status == "ok":
                    u["ok"] += 1
                elif d.last_status == "error":
                    u["err"] += 1
    return render(request, "credentials.html", user, creds=creds, usage=usage)


@app.get("/credentials/new")
def credential_new(request: Request):
    user = require(request, "admin")
    return render(request, "cred_edit.html", user, c=None, used=0)


@app.get("/credentials/{cid}/edit")
def credential_edit(request: Request, cid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(Credential, cid)
        used = db.scalar(select(func.count(Device.id)).where(Device.credential_id == cid)) or 0
    if c is None:
        return go("/credentials")
    return render(request, "cred_edit.html", user, c=c, used=used)


@app.get("/groups")
def groups_page(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        counts = dict(db.execute(select(Device.group_id, func.count(Device.id)).group_by(Device.group_id)).all())
    return render(request, "groups.html", user, groups=groups, counts=counts)


@app.post("/credentials")
async def credential_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        cid = int(form.get("id") or 0)
        c = db.get(Credential, cid) if cid else Credential()
        c.name, c.username = form["name"].strip(), form["username"].strip()
        if form.get("password"):
            c.password_enc = encrypt(form["password"])
        if form.get("secret"):
            c.secret_enc = encrypt(form["secret"])
        if form.get("clear_secret") == "on":
            c.secret_enc = None
        if not cid:
            db.add(c)
        db.commit()
    log_action(request, user, "credential_save", form["name"])
    flash(request, _("Credencial guardada (cifrada con AES-256)"))
    return go("/credentials")


@app.post("/credentials/{cid}/delete")
def credential_delete(request: Request, cid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(Credential, cid)
        if c:
            db.delete(c)
            db.commit()
            log_action(request, user, "credential_delete", c.name)
    return go("/credentials")


@app.post("/groups")
async def group_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        db.add(DeviceGroup(name=form["name"].strip()))
        db.commit()
    log_action(request, user, "group_create", form["name"])
    return go("/groups")


@app.post("/groups/{gid}/delete")
def group_delete(request: Request, gid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        g = db.get(DeviceGroup, gid)
        if g:
            db.delete(g)
            db.commit()
            log_action(request, user, "group_delete", g.name)
    return go("/groups")


# --------------------------------------------------------------------------- configlets
@app.get("/configlets")
def configlets(request: Request):
    user = require(request)
    with SessionLocal() as db:
        items = db.scalars(select(Configlet).order_by(Configlet.name)).all()
        devs = [d for d in db.scalars(select(Device).where(Device.enabled.is_(True)))]
        allowed = set(filter_ids(user, [d.id for d in devs]))
        audit_rows = db.scalars(select(AuditLog.detail).where(AuditLog.action.in_(("configlet_exec", "change_request")))).all()
    for c in items:  # tarjetas del prototipo: equipos compatibles y veces que se ha usado
        try:
            rx = re.compile(c.device_type_filter) if c.device_type_filter else None
        except re.error:
            rx = None
        c.compat = sum(1 for d in devs if d.id in allowed and (rx is None or rx.search(d.device_type)))
        c.uses = sum(1 for det in audit_rows if det and c.name in det)
    return render(request, "configlets.html", user, items=items)


@app.get("/configlets/new")
@app.get("/configlets/{cid}/edit")
def configlet_form(request: Request, cid: int | None = None):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(Configlet, cid) if cid else None
        types = [d.device_type for d in db.scalars(select(Device))]
    return render(request, "configlet_form.html", user, c=c, types=types)


@app.post("/configlets/new")
@app.post("/configlets/{cid}/edit")
async def configlet_save(request: Request, cid: int | None = None):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        c = db.get(Configlet, cid) if cid else Configlet()
        c.name, c.description = form["name"].strip(), form.get("description")
        c.kind, c.body = form.get("kind", "config"), form["body"]
        c.device_type_filter = form.get("device_type_filter") or None
        if not cid:
            db.add(c)
        db.commit()
    log_action(request, user, "configlet_save", form["name"])
    flash(request, _("Configlet guardado"))
    return go("/configlets")


@app.post("/configlets/{cid}/delete")
def configlet_delete(request: Request, cid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(Configlet, cid)
        if c:
            db.delete(c)
            db.commit()
            log_action(request, user, "configlet_delete", c.name)
    return go("/configlets")


def _parse_vars(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip().isidentifier():
                out[k.strip()] = v.strip()
    return out


@app.get("/configlets/{cid}/run")
def configlet_run_form(request: Request, cid: int):
    user = require(request, "operator")
    with SessionLocal() as db:
        c = db.get(Configlet, cid)
        stmt = select(Device).where(Device.enabled.is_(True)).order_by(Device.name)
        allowed = set(filter_ids(user, [d.id for d in db.scalars(stmt)]))
        devs = [d for d in db.scalars(stmt) if d.id in allowed and
                (not c.device_type_filter or re.search(c.device_type_filter, d.device_type))]
        for d in devs:
            d.group.name if d.group else None  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    found_vars = sorted(set(re.findall(r"{{\s*([a-zA-Z_]\w*)", c.body)) - {"device"})
    return render(request, "configlet_run.html", user, c=c, devices=devs, groups=groups, found_vars=found_vars,
                  require_approval=get_setting("require_approval", "1") == "1")


@app.post("/configlets/{cid}/run")
async def configlet_run(request: Request, cid: int):
    user = require(request, "operator")
    form = await request.form()
    requested = [int(x) for x in form.getlist("device_ids")]
    ids = filter_ids(user, requested)
    if len(ids) != len(requested):
        raise Forbidden()
    if not ids:
        flash(request, _("Seleccione al menos un dispositivo"), "error")
        return go(f"/configlets/{cid}/run")
    variables = _parse_vars(form.get("variables", ""))
    for k, v in form.items():  # campos por variable del panel «Ejecutar» (var_nombre)
        if k.startswith("var_") and k[4:].isidentifier() and str(v).strip():
            variables[k[4:]] = str(v).strip()
    with SessionLocal() as db:
        c = db.get(Configlet, cid)
        if c.kind == "exec":
            outputs = []
            devs = db.scalars(select(Device).where(Device.id.in_(ids))).all()
            futs = []
            for d in devs:
                try:
                    cmds = services.render_commands(c.body, d, variables)
                    futs.append((d.name, services.pool.submit(services.push_commands, d.id, cmds, True)))
                except Exception as e:  # noqa: BLE001
                    outputs.append((d.name, f"ERROR de plantilla: {e}"))
            for name, f in futs:
                try:
                    outputs.append((name, f.result(timeout=600)))
                except Exception as e:  # noqa: BLE001
                    outputs.append((name, f"ERROR: {e}"))
            log_action(request, user, "configlet_exec", f"{c.name} en {len(ids)} equipos")
            vend = {d.name: (PROFILE_MAP[d.device_type].vendor if d.device_type in PROFILE_MAP else d.device_type)[:3].upper()
                    for d in devs}
            return render(request, "exec_result.html", user, c=c, outputs=outputs, vend=vend)
        title, body = c.name, c.body
    cr_id = _create_cr(user, form.get("title") or f"Configlet: {title}", body, variables, ids)
    log_action(request, user, "change_request", f"#{cr_id} {title}")
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- solicitudes de cambio
def _create_cr(user, title, body, variables, ids, is_rollback=False, kind="config", firmware_id=None,
               reload=False) -> int:
    # la actualización de firmware siempre requiere aprobación
    auto = get_setting("require_approval", "1") != "1" and kind == "config"
    if services.example_addresses(body + " " + json.dumps(variables)):
        auto = False  # con direcciones de ejemplo nunca se ejecuta sola: alguien tiene que revisarla
    if auto and kind == "config":
        with SessionLocal() as db:
            devs = list(db.scalars(select(Device).where(Device.id.in_(ids))))
            if services.lockout_risks(body, [(d, d.credential)[0] for d in devs]):
                auto = False  # podría dejar a OpenNCCM sin acceso: que lo revise un administrador
    with SessionLocal() as db:
        cr = ChangeRequest(title=title, body=body, variables=json.dumps(variables), device_ids=json.dumps(ids),
                           requested_by=user.username, is_rollback=is_rollback, kind=kind,
                           firmware_id=firmware_id, reload=reload,
                           status="approved" if auto else "pending",
                           reviewed_by="(aprobación desactivada)" if auto else None)
        db.add(cr)
        db.commit()
        cr_id = cr.id
    if auto:
        services.jobs.submit(services.execute_change_request, cr_id)
    else:
        services.add_event("info", "Solicitud de cambio pendiente",
                           f"#{cr_id} '{title}' solicitada por {user.username} ({len(ids)} equipos)")
    return cr_id


@app.get("/changes")
def changes(request: Request, status: str | None = None):
    user = require(request)
    with SessionLocal() as db:
        if status is None:  # como el prototipo: se abre en «Por aprobar» si hay alguna pendiente
            status = "pending" if db.scalar(select(func.count(ChangeRequest.id))
                                            .where(ChangeRequest.status == "pending")) else ""
        stmt = select(ChangeRequest).order_by(ChangeRequest.id.desc()).limit(300)
        if status:
            stmt = stmt.where(ChangeRequest.status == status)
        scope = device_scope(user)
        items = [cr for cr in db.scalars(stmt)
                 if scope is None or set(json.loads(cr.device_ids)) <= scope]
        names = {d.id: d.name for d in db.scalars(select(Device))}
        counts = {st: db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == st)) or 0
                  for st in ("pending", "done", "rejected", "failed")}
    for cr in items:  # tipo y equipos, para la lista del prototipo
        cr.dev_names = [names.get(i, "?") for i in json.loads(cr.device_ids)]
        cr.kind_label = (_("Firmware") if cr.kind == "firmware" else _("Reversión") if cr.is_rollback
                         else _("Corrección de cumplimiento") if cr.title.startswith(("Corrección", "Corregir"))
                         else _("Configlet"))
        cr.kind_icon = ("chip" if cr.kind == "firmware" else "undo" if cr.is_rollback
                        else "wrench" if cr.title.startswith(("Corrección", "Corregir")) else "code")
    return render(request, "changes.html", user, items=items, status=status, counts=counts,
                  flow=get_setting("require_approval", "1") == "1")


@app.get("/changes/{cr_id}")
def change_detail(request: Request, cr_id: int):
    user = require(request)
    with SessionLocal() as db:
        cr = db.get(ChangeRequest, cr_id)
        ids = json.loads(cr.device_ids)
        if len(filter_ids(user, ids)) != len(ids):
            raise Forbidden()
        devs = db.scalars(select(Device).where(Device.id.in_(ids))).all()
        previews = []
        if cr.kind == "firmware":
            from .db import FirmwareImage
            img = db.get(FirmwareImage, cr.firmware_id)
            for d in devs[:5]:
                fw = get_profile(d.device_type).firmware or {}
                steps = [f"# SCP {img.filename if img else '?'} -> {fw.get('fs', '?')} (verificación MD5)"]
                steps += [c.format(file=img.filename if img else "?") for c in fw.get("boot", []) + fw.get("exec", [])]
                if cr.reload:
                    steps.append(fw.get("reload", "reload"))
                previews.append((d.name, "\n".join(steps)))
        for d in devs[:5] if cr.kind != "firmware" else []:
            try:
                previews.append((d.name, "\n".join(services.render_commands(cr.body, d,
                                                                            json.loads(cr.variables or "{}")))))
            except Exception as e:  # noqa: BLE001
                previews.append((d.name, f"ERROR de plantilla: {e}"))
    example = services.example_addresses((cr.body or "") + " " + (cr.variables or ""))
    with SessionLocal() as db:
        rdevs = list(db.scalars(select(Device).where(Device.id.in_(ids))))
        risks = services.lockout_risks(cr.body, [(d, d.credential)[0] for d in rdevs]) if cr.kind != "firmware" else []
    can_review = ROLES[user.role] >= ROLES["admin"] and (
        cr.requested_by != user.username or get_setting("allow_self_approval", "1") == "1")
    kind_label = (_("Firmware") if cr.kind == "firmware" else _("Reversión") if cr.is_rollback
                  else _("Corrección de cumplimiento") if cr.title.startswith(("Corrección", "Corregir")) else _("Configlet"))
    vend = {d.id: (get_profile(d.device_type).vendor if d.device_type in PROFILE_MAP else d.device_type)[:3].upper() for d in devs}
    return render(request, "change.html", user, cr=cr, devices=devs, previews=previews, can_review=can_review,
                  kind_label=kind_label, vend=vend, example=example, risks=risks)


@app.post("/changes/{cr_id}/review")
async def change_review(request: Request, cr_id: int):
    user = require(request, "admin")
    form = await request.form()
    decision = form.get("decision")
    with SessionLocal() as db:
        cr = db.get(ChangeRequest, cr_id)
        if cr.status != "pending":
            flash(request, _("La solicitud ya fue revisada"), "error")
            return go(f"/changes/{cr_id}")
        if cr.requested_by == user.username and get_setting("allow_self_approval", "1") != "1":
            flash(request, _("No puede aprobar sus propias solicitudes (ver Ajustes)"), "error")
            return go(f"/changes/{cr_id}")
        bad = services.example_addresses((cr.body or "") + " " + (cr.variables or ""))
        risks = []
        if decision == "approve" and cr.kind != "firmware":
            devs = list(db.scalars(select(Device).where(Device.id.in_(json.loads(cr.device_ids or "[]")))))
            risks = services.lockout_risks(cr.body, [(d, d.credential)[0] for d in devs])
        if risks:
            flash(request, _("No se puede aprobar:") + " " + " ".join(risks), "error")
            return go(f"/changes/{cr_id}")
        if decision == "approve" and bad:
            msg = _f("No se puede aprobar: los comandos usan direcciones de ejemplo ({0}) que no existen en ninguna red. Recházala y crea una con las direcciones reales.", ", ".join(bad))
            flash(request, msg, "error")
            return go(f"/changes/{cr_id}")
        if decision != "approve" and not (form.get("comment") or "").strip():
            flash(request, _("Escribe por qué se rechaza: así quien la pidió sabe qué cambiar."), "error")
            return go(f"/changes/{cr_id}")
        cr.status = "approved" if decision == "approve" else "rejected"
        cr.reviewed_by, cr.review_comment = user.username, form.get("comment")
        db.commit()
    log_action(request, user, f"change_{cr.status}", f"#{cr_id}")
    if cr.status == "approved":
        services.jobs.submit(services.execute_change_request, cr_id)
        flash(request, _("Solicitud aprobada: ejecutándose en segundo plano"))
    else:
        flash(request, _("Solicitud rechazada"))
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- cumplimiento
@app.get("/compliance")
def compliance(request: Request):
    user = require(request)
    with SessionLocal() as db:
        policies = db.scalars(select(Policy).order_by(Policy.name)).all()
        for p in policies:
            p.rules  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        scope = device_scope(user)
        fails = [f for f in db.scalars(select(ComplianceResult).where(ComplianceResult.passed.is_(False))
                                       .order_by(ComplianceResult.device_id)) if scope is None or f.device_id in scope]
        for f in fails:
            (f.device.name, f.rule.name, f.policy.name)  # carga la relación antes de cerrar la sesión
        summary = [s for s in db.execute(select(ComplianceResult.device_id,
                                                func.sum(case((ComplianceResult.passed.is_(True), 1), else_=0)),
                                                func.count()).group_by(ComplianceResult.device_id)).all()
                   if scope is None or s[0] in scope]
        # «Señal»: mapa equipo × política y barras por gravedad de cada política (sobre los equipos visibles)
        results = [r for r in db.scalars(select(ComplianceResult)) if scope is None or r.device_id in scope]
        sev_of = {r.id: r.severity for p in policies for r in p.rules}
        cell: dict[tuple, list] = {}
        pstat = {p.id: {"ok": 0, "critical": 0, "major": 0, "minor": 0, "fix": 0} for p in policies}
        fixable = {r.id for p in policies for r in p.rules if r.remediation}
        for r in results:
            c = cell.setdefault((r.device_id, r.policy_id), [0, 0])
            c[1] += 1
            st = pstat.setdefault(r.policy_id, {"ok": 0, "critical": 0, "major": 0, "minor": 0, "fix": 0})
            if r.passed:
                c[0] += 1
                st["ok"] += 1
            else:
                st[sev_of.get(r.rule_id, "major") if sev_of.get(r.rule_id) in ("critical", "major") else "minor"] += 1
                st["fix"] += r.rule_id in fixable
        dev_ids = sorted({d for d, _p in cell})
        devs = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(dev_ids or [0])))}
        matrix = [(devs[d], [round(100 * cell[(d, p.id)][0] / cell[(d, p.id)][1]) if (d, p.id) in cell else None
                             for p in policies]) for d in dev_ids if d in devs]
        matrix.sort(key=lambda row: row[0].name)
        vend = {d.id: (PROFILE_MAP[d.device_type].vendor if d.device_type in PROFILE_MAP else d.device_type)[:3].upper()
                for d in devs.values()}
        last_audit = db.scalar(select(func.max(ComplianceResult.checked_at)))
    total_r = len(results)
    pct = round(100 * sum(1 for r in results if r.passed) / total_r) if total_r else None
    return render(request, "compliance.html", user, policies=policies, groups=groups, fails=fails, fail_rows=fails,
                  devices_checked=len(summary), devices_ok=sum(1 for _k, p, t in summary if p == t),
                  matrix=matrix, pstat=pstat, pct=pct, last_audit=last_audit, vend=vend)


@app.post("/compliance/policies")
async def policy_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        pid = int(form.get("id") or 0)
        p = db.get(Policy, pid) if pid else Policy()
        p.name, p.description = form["name"].strip(), form.get("description")
        p.device_type_filter = form.get("device_type_filter") or None
        p.group_id = int(form["group_id"]) if form.get("group_id") else None
        p.enabled = form.get("enabled", "on") == "on"
        if not pid:
            db.add(p)
        db.commit()
        pid = p.id
    log_action(request, user, "policy_save", form["name"])
    return go(f"/compliance/policies/{pid}")


@app.get("/compliance/policies/new")
def policy_new(request: Request):
    """«Nueva política» en un panel lateral (prototipo)."""
    user = require(request, "admin")
    with SessionLocal() as db:
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        devs = [d for d in db.scalars(select(Device).order_by(Device.name)) if services.latest_version(db, d.id)]
        types = [d.device_type for d in db.scalars(select(Device))]
    return render(request, "policy.html", user, p=None, groups=groups, test_devices=devs, types=types)


@app.post("/compliance/policies/save-all")
async def policy_save_all(request: Request):
    """Guarda la política y todas sus reglas de una vez (editor del prototipo): crea, cambia y quita reglas."""
    user = require(request, "admin")
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        flash(request, _("La política necesita un nombre"), "error")
        return go("/compliance")
    with SessionLocal() as db:
        pid = int(form.get("id") or 0)
        p = db.get(Policy, pid) if pid else Policy()
        p.name, p.description = name, form.get("description") or None
        p.device_type_filter = form.get("device_type_filter") or None
        p.group_id = int(form["group_id"]) if form.get("group_id") else None
        if not pid:
            p.enabled = True
            db.add(p)
            db.flush()
        keep = set()
        ids, names, kinds = form.getlist("rule_id"), form.getlist("rule_name"), form.getlist("rule_kind")
        sevs, pats, fixes = form.getlist("rule_sev"), form.getlist("rule_pattern"), form.getlist("rule_fix")
        for i, rname in enumerate(names):
            pat = pats[i] if i < len(pats) else ""
            if not rname.strip() and not pat.strip():
                continue
            try:
                re.compile(pat, re.M)
            except re.error as e:
                db.rollback()
                flash(request, _f("Regla «{0}»: patrón no válido ({1})", rname or i + 1, e), "error")
                return go(f"/compliance/policies/{pid}" if pid else "/compliance")
            rid = int(ids[i]) if i < len(ids) and ids[i].isdigit() else 0
            r = db.get(Rule, rid) if rid else None
            if r is None or r.policy_id != p.id:
                r = Rule(policy_id=p.id)
                db.add(r)
            r.name, r.pattern = rname.strip() or pat[:60], pat
            r.kind = kinds[i] if i < len(kinds) and kinds[i] in ("must_contain", "must_not_contain") else "must_contain"
            r.severity = sevs[i] if i < len(sevs) and sevs[i] in ("critical", "major", "minor") else "major"
            r.remediation = (fixes[i] if i < len(fixes) else "").strip() or None
            db.flush()
            keep.add(r.id)
        for r in list(p.rules):
            if r.id not in keep:
                db.delete(r)
        db.commit()
        pid = p.id
    log_action(request, user, "policy_save", name)
    flash(request, _("Política guardada. Se auditará en la próxima ejecución."))
    return go("/compliance")


@app.get("/compliance/policies/{pid}")
def policy_detail(request: Request, pid: int):
    user = require(request)
    with SessionLocal() as db:
        p = db.get(Policy, pid)
        p.rules  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        scope = device_scope(user)
        devs = [d for d in db.scalars(select(Device).order_by(Device.name))
                if (scope is None or d.id in scope) and (not p.device_type_filter or re.search(p.device_type_filter, d.device_type))]
        types = [d.device_type for d in db.scalars(select(Device))]
    return render(request, "policy.html", user, p=p, groups=groups, test_devices=devs, types=types)


@app.post("/compliance/policies/{pid}/delete")
def policy_delete(request: Request, pid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        p = db.get(Policy, pid)
        db.delete(p)
        db.commit()
    log_action(request, user, "policy_delete", p.name)
    return go("/compliance")


@app.post("/compliance/policies/{pid}/rules")
async def rule_add(request: Request, pid: int):
    user = require(request, "admin")
    form = await request.form()
    try:
        re.compile(form["pattern"], re.MULTILINE)
    except re.error as e:
        flash(request, _f("Regex inválida: {0}", e), "error")
        return go(f"/compliance/policies/{pid}")
    with SessionLocal() as db:
        db.add(Rule(policy_id=pid, name=form["name"], kind=form["kind"], pattern=form["pattern"],
                    severity=form.get("severity", "major"), remediation=form.get("remediation") or None))
        db.commit()
    log_action(request, user, "rule_add", f"policy={pid} {form['name']}")
    return go(f"/compliance/policies/{pid}")


@app.post("/compliance/rules/{rid}/delete")
def rule_delete(request: Request, rid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        r = db.get(Rule, rid)
        pid = r.policy_id
        db.delete(r)
        db.commit()
    log_action(request, user, "rule_delete", str(rid))
    return go(f"/compliance/policies/{pid}")


@app.post("/compliance/run")
def compliance_run(request: Request):
    user = require(request, "operator")

    ids = filter_ids(user, services.device_ids())

    def _all():
        for i in ids:
            services.run_compliance(i)
    services.jobs.submit(_all)
    log_action(request, user, "compliance_run_all")
    flash(request, _("Auditoría de cumplimiento lanzada para todos los equipos"))
    return go("/compliance")


@app.post("/compliance/remediate/{result_id}")
def compliance_remediate(request: Request, result_id: int):
    user = require(request, "operator")
    with SessionLocal() as db:
        r = db.get(ComplianceResult, result_id)
        if not r or not r.rule.remediation:
            flash(request, _("La regla no tiene comandos de corrección"), "error")
            return go("/compliance")
        check_device(user, r.device_id)
        title = f"Corrección '{r.rule.name}' en {r.device.name}"
        body, dev_id = r.rule.remediation, r.device_id
    cr_id = _create_cr(user, title, body, {}, [dev_id])
    return go(f"/changes/{cr_id}")


@app.post("/compliance/remediate-rule")
async def compliance_remediate_rule(request: Request):
    """«Corregir en N equipos…»: una solicitud de cambio por cada equipo marcado que incumple la regla."""
    user = require(request, "operator")
    form = await request.form()
    ids = [int(x) for x in form.getlist("result_id") if str(x).isdigit()]
    scope = device_scope(user)
    fixes = []
    with SessionLocal() as db:
        for rid in ids:
            r = db.get(ComplianceResult, rid)
            if not r or r.passed or not r.rule or not r.rule.remediation or (scope is not None and r.device_id not in scope):
                continue
            fixes.append((f"Corrección '{r.rule.name}' en {r.device.name}", r.rule.remediation, r.device_id))
    if not fixes:
        flash(request, _("Marca al menos un equipo con corrección pendiente"), "error")
        return go("/compliance")
    created = [_create_cr(user, title[:200], body, {}, [dev_id]) for title, body, dev_id in fixes]
    flash(request, _f("Se crearon {0} solicitudes de corrección", len(created)) if len(created) != 1
          else _("Se creó la solicitud de corrección"))
    return go(f"/changes/{created[0]}" if len(created) == 1 else "/changes?status=pending")


@app.post("/compliance/remediate-policy")
async def compliance_remediate_policy(request: Request):
    """«Corregir N»: una solicitud de cambio por equipo con todas las correcciones de sus infracciones de la política."""
    user = require(request, "operator")
    form = await request.form()
    pid = int(form.get("policy_id") or 0)
    only = int(form.get("device_id") or 0)
    back = form.get("next") or "/compliance"
    back = back if back.startswith("/") and not back.startswith("//") else "/compliance"
    scope = device_scope(user)
    per_dev: dict[int, list] = {}
    with SessionLocal() as db:
        for r in db.scalars(select(ComplianceResult).where(ComplianceResult.policy_id == pid,
                                                             ComplianceResult.passed.is_(False))):
            if (only and r.device_id != only) or (scope is not None and r.device_id not in scope):
                continue
            if r.rule and r.rule.remediation:
                per_dev.setdefault(r.device_id, []).append((r.rule.name, r.rule.remediation.strip(), r.device.name,
                                                            r.policy.name))
    if not per_dev:
        flash(request, _("No hay infracciones con comandos de corrección en esta política"), "error")
        return go(back)
    created = []
    for dev_id, fixes in per_dev.items():
        names = ", ".join(n for n, *_x in fixes)
        title = f"Corrección de {len(fixes)} reglas de '{fixes[0][3]}' en {fixes[0][2]}"
        created.append(_create_cr(user, title[:200], "\n".join(body for _n, body, *_x in fixes), {}, [dev_id]))
        log_action(request, user, "remediate_policy", f"politica={pid} equipo={dev_id}: {names}"[:500])
    flash(request, _f("Se crearon {0} solicitudes de corrección", len(created)) if len(created) != 1
          else _("Se creó la solicitud de corrección"))
    return go(f"/changes/{created[0]}" if len(created) == 1 else "/changes?status=pending")


@app.post("/compliance/rules/test")
async def compliance_rule_test(request: Request):
    """Prueba en vivo de una regla contra la última configuración de un equipo (editor de políticas)."""
    user = require(request, "admin")
    form = await request.form()
    dev_id = int(form.get("device_id") or 0)
    check_device(user, dev_id)
    with SessionLocal() as db:
        ver = services.latest_version(db, dev_id)
        if not ver:
            return JSONResponse({"error": _("El equipo no tiene versiones respaldadas")})
        text = decrypt(ver.content_enc)
    return JSONResponse(services.test_rule(text, form.get("pattern", ""), form.get("kind", "must_contain")))


# --------------------------------------------------------------------------- tareas programadas
@app.get("/schedules")
def schedules(request: Request):
    user = require(request)
    with SessionLocal() as db:
        items = db.scalars(select(Schedule).order_by(Schedule.name)).all()
        for s in items:
            s.group  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        configlets = db.scalars(select(Configlet).order_by(Configlet.name)).all()
    jobs = {j.id: j.next_run_time for j in scheduler.scheduler.get_jobs()}
    from . import cronutil
    sf = {"name": "Respaldo nocturno", "action": "backup", "group_id": None, "configlet_id": None,
          **cronutil.to_form("0 2 * * *")}
    return render(request, "schedules.html", user, items=items, groups=groups, configlets=configlets, jobs=jobs,
                  DAYS=cronutil.DAYS, FREQS=cronutil.FREQS, ACTIONS=SCHEDULE_ACTIONS, sf=sf,
                  cfl_names={c.id: c.name for c in configlets})


SCHEDULE_ACTIONS = {"backup": N_("Respaldo de configuración"), "compliance": N_("Auditoría de cumplimiento"),
                    "cve": N_("Escaneo de vulnerabilidades"), "configlet": N_("Ejecutar configlet"),
                    "report_email": N_("Informes por correo"), "system_backup": N_("Copia de recuperación")}
# nombre propuesto en «Nueva tarea» para cada acción (con su horario por omisión: respaldo a diario, el resto semanal)
SCHEDULE_NAMES = {"backup": N_("Respaldo nocturno"), "compliance": N_("Auditoría semanal"),
                  "cve": N_("Búsqueda semanal de CVE"), "configlet": N_("Configlet semanal"),
                  "report_email": N_("Informe semanal por correo"), "system_backup": N_("Copia de recuperación semanal")}


@app.get("/schedules/new")
def schedule_new_form(request: Request, action: str = "backup"):
    """«Nueva tarea» en un panel lateral (prototipo); por omisión, un respaldo nocturno a las 02:00."""
    user = require(request, "admin")
    from . import cronutil
    with SessionLocal() as db:
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        configlets = db.scalars(select(Configlet).order_by(Configlet.name)).all()
    act = action if action in SCHEDULE_ACTIONS else "backup"
    sf = {"name": _(SCHEDULE_NAMES[act]), "action": act, "group_id": None, "configlet_id": None,
          **cronutil.to_form("0 2 * * *" if act == "backup" else "0 8 * * 1")}
    return render(request, "schedule_edit.html", user, sf=sf, sid=None, groups=groups, configlets=configlets,
                  DAYS=cronutil.DAYS, FREQS=cronutil.FREQS, NAMES={k: _(v) for k, v in SCHEDULE_NAMES.items()})


@app.get("/schedules/{sid}/edit")
def schedule_edit_form(request: Request, sid: int):
    user = require(request, "admin")
    from . import cronutil
    with SessionLocal() as db:
        s = db.get(Schedule, sid)
        if s is None:
            return go("/schedules")
        sf = {"name": s.name, "action": s.action, "group_id": s.group_id, "configlet_id": s.configlet_id,
              **cronutil.to_form(s.cron)}
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        configlets = db.scalars(select(Configlet).order_by(Configlet.name)).all()
    return render(request, "schedule_edit.html", user, sf=sf, sid=sid, groups=groups, configlets=configlets,
                  DAYS=cronutil.DAYS, FREQS=cronutil.FREQS)


@app.post("/schedules/{sid}/edit")
async def schedule_edit_save(request: Request, sid: int):
    user = require(request, "admin")
    form = await request.form()
    from . import cronutil
    try:
        cron = cronutil.from_form(form)
    except ValueError as e:
        flash(request, _f("Programación inválida: {0}", e), "error")
        return go(f"/schedules/{sid}/edit")
    with SessionLocal() as db:
        s = db.get(Schedule, sid)
        if s is None:
            return go("/schedules")
        before = f"{s.name} {s.cron}"
        s.name = (form.get("name") or s.name).strip()
        s.action = form.get("action") or s.action
        s.cron = cron
        s.group_id = int(form["group_id"]) if form.get("group_id") else None
        s.configlet_id = int(form["configlet_id"]) if form.get("configlet_id") else None
        name = s.name
        db.commit()
    scheduler.reload_jobs()
    log_action(request, user, "schedule_edit", f"{before} -> {name} {cron}")
    flash(request, _f("Tarea «{0}» actualizada: {1}", name, cronutil.describe(cron)))
    return go("/schedules")


@app.post("/schedules")
async def schedule_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    from . import cronutil
    try:
        cron = cronutil.from_form(form)
    except ValueError as e:
        flash(request, _f("Programación inválida: {0}", e), "error")
        return go("/schedules")
    with SessionLocal() as db:
        db.add(Schedule(name=form["name"], action=form["action"], cron=cron,
                        group_id=int(form["group_id"]) if form.get("group_id") else None,
                        configlet_id=int(form["configlet_id"]) if form.get("configlet_id") else None))
        db.commit()
    scheduler.reload_jobs()
    log_action(request, user, "schedule_create", f"{form['name']} {cron}")
    flash(request, _f("Tarea «{0}» creada: {1}", form['name'], cronutil.describe(cron)))
    return go("/schedules")


@app.post("/schedules/{sid}/{op}")
def schedule_op(request: Request, sid: int, op: str):
    user = require(request, "admin" if op != "run" else "operator")
    with SessionLocal() as db:
        s = db.get(Schedule, sid)
        if op == "delete":
            db.delete(s)
        elif op == "toggle":
            s.enabled = not s.enabled
        db.commit()
    if op == "run":
        services.jobs.submit(scheduler.run_schedule, sid)
        flash(request, _("Tarea lanzada"))
    scheduler.reload_jobs()
    log_action(request, user, f"schedule_{op}", str(sid))
    return go("/schedules")


# --------------------------------------------------------------------------- alarmas, vulns, auditoría
EV_SEV = {"crit": ("critical",), "major": ("major", "error"), "minor": ("warning",), "info": ("info",)}


@app.get("/events")
def events(request: Request, sev: str = "", unack: int | None = None, page: int = 1, per: int = 15):
    """Alarmas y eventos (prototipo): chips por severidad con recuento, «Sin reconocer / Todas» y cola."""
    from .web import _event_title
    user = require(request)
    if unack is None:
        unack = 1  # como el prototipo: se abre en «Sin reconocer»
    with SessionLocal() as db:
        base = select(Event)
        if unack:
            base = base.where(Event.acknowledged.is_(False))
        scope = device_scope(user)
        if scope is not None:  # eventos de sus equipos (los globales solo los ven usuarios sin restricción)
            base = base.where(Event.device_id.in_(scope))
        counts = {k: 0 for k in EV_SEV}
        sub = base.subquery()
        for sv, n in db.execute(select(sub.c.severity, func.count()).group_by(sub.c.severity)).all():
            for k, vals in EV_SEV.items():
                if sv in vals:
                    counts[k] += n
        total = sum(counts.values())
        stmt = base.order_by(Event.id.desc())
        if sev in EV_SEV:
            stmt = stmt.where(Event.severity.in_(EV_SEV[sev]))
        elif sev:  # compatibilidad con enlaces antiguos (?sev=critical)
            stmt = stmt.where(Event.severity == sev)
        per = per if per in (15, 25, 50, 100) else 15  # 15 por omisión; el tamaño va en el enlace, no se guarda
        shown = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        pages = max(1, -(-shown // per))
        page = min(max(page, 1), pages)
        items = db.scalars(stmt.offset((page - 1) * per).limit(per)).all()
        for e in items:
            e.device.name if e.device else None  # carga la relación antes de cerrar la sesión
            e.title = _event_title(e)
        open_n = db.scalar(select(func.count(Event.id)).where(Event.acknowledged.is_(False),
                                                              *([Event.device_id.in_(scope)] if scope is not None else []))) or 0
    return render(request, "events.html", user, items=items, sev=sev, unack=unack, page=page, per=per, shown=shown, pages=pages, counts=counts,
                  total=total, open_n=open_n, min_sev=get_setting("alert_min_severity", "warning"))


@app.post("/events/ack")
async def events_ack(request: Request):
    user = require(request, "operator")
    form = await request.form()
    with SessionLocal() as db:
        stmt = select(Event).where(Event.acknowledged.is_(False))
        if form.get("id"):
            stmt = stmt.where(Event.id == int(form["id"]))
        scope = device_scope(user)
        if scope is not None:
            stmt = stmt.where(Event.device_id.in_(scope))
        ids = []
        for e in db.scalars(stmt):
            e.acknowledged = True
            ids.append(e.id)
        db.commit()
    log_action(request, user, "events_ack", form.get("id") or "all")
    if ids:
        flash(request, _("Alarma reconocida") if len(ids) == 1 else _f("{0} alarmas reconocidas", len(ids)))
        request.session["undo"] = {"url": "/events/unack", "ids": ",".join(map(str, ids[:5000]))}
    return go(request.headers.get("referer") or "/events")


@app.post("/events/unack")
async def events_unack(request: Request):
    """«Deshacer» del aviso flotante: vuelve a dejar sin reconocer las alarmas recién reconocidas."""
    user = require(request, "operator")
    form = await request.form()
    ids = [int(x) for x in (form.get("ids") or "").split(",") if x.strip().isdigit()][:5000]
    n = 0
    with SessionLocal() as db:
        if ids:
            stmt = select(Event).where(Event.id.in_(ids), Event.acknowledged.is_(True))
            scope = device_scope(user)
            if scope is not None:
                stmt = stmt.where(Event.device_id.in_(scope))
            for e in db.scalars(stmt):
                e.acknowledged = False
                n += 1
            db.commit()
    log_action(request, user, "events_unack", str(n))
    flash(request, _("Se deshizo: la alarma vuelve a estar pendiente") if n == 1
          else _f("Se deshizo: {0} alarmas vuelven a estar pendientes", n))
    return go(request.headers.get("referer") or "/events")


@app.get("/vulns")
def vulns(request: Request, f: str = "all"):
    """Vulnerabilidades agrupadas por CVE (prototipo): KPI, filtros y «explotado activamente» (CISA KEV)."""
    user = require(request)
    kev = services.kev_ids()
    with SessionLocal() as db:
        scope = device_scope(user)
        items = [v for v in db.scalars(select(Vulnerability).order_by(Vulnerability.score.desc().nulls_last())
                                       .limit(5000)) if scope is None or v.device_id in scope][:2000]
        devs = {d.id: d for d in db.scalars(select(Device)) if scope is None or d.id in scope}
        images = db.scalars(select(FirmwareImage).order_by(FirmwareImage.uploaded_at.desc())).all()
        last = max((v.found_at for v in items), default=None)

    def fixed_image(d):  # imagen cargada para el tipo del equipo con otra versión
        for img in images:
            try:
                if (not img.device_type_filter or re.search(img.device_type_filter, d.device_type)) \
                        and img.version and img.version != d.os_version:
                    return img
            except re.error:
                continue
        return None

    groups: dict[str, dict] = {}
    for v in items:
        g = groups.setdefault(v.cve_id, {"id": v.cve_id, "score": v.score, "sev": (v.severity or "").lower(),
                                         "desc": v.description or "", "url": v.url, "kev": v.cve_id in kev,
                                         "devs": []})
        if v.device_id in devs and devs[v.device_id] not in g["devs"]:
            g["devs"].append(devs[v.device_id])
    rows = sorted(groups.values(), key=lambda g: (not g["kev"], -(g["score"] or 0), g["id"]))
    for g in rows:
        imgs = {fixed_image(d) for d in g["devs"]} - {None}
        g["img"] = sorted(imgs, key=lambda i: i.filename)[0] if imgs else None
    affected = {d.id: d for g in rows for d in g["devs"]}
    crit = [g for g in rows if g["sev"] == "critical"]
    k = {"crit": len(crit), "crit_devs": len({d.id for g in crit for d in g["devs"]}),
         "kev": sum(1 for g in rows if g["kev"]), "affected": len(affected), "total": len(devs),
         "with_img": sum(1 for d in affected.values() if fixed_image(d))}
    sev_f = {"crit": ("critical",), "major": ("high",), "minor": ("medium", "low")}
    if f == "kev":
        rows = [g for g in rows if g["kev"]]
    elif f in sev_f:
        rows = [g for g in rows if g["sev"] in sev_f[f]]
    return render(request, "vulns.html", user, rows=rows, k=k, f=f, last=last, has_kev=bool(kev))


@app.get("/audit")
def audit_view(request: Request, q: str = ""):
    user = require(request, "admin")
    with SessionLocal() as db:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(1000)
        if q:
            stmt = stmt.where(or_(AuditLog.username.ilike(f"%{q}%"), AuditLog.action.ilike(f"%{q}%"),
                                  AuditLog.detail.ilike(f"%{q}%")))
        items = db.scalars(stmt).all()
    return render(request, "audit.html", user, items=items, q=q)


# --------------------------------------------------------------------------- usuarios
@app.get("/users")
def users(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        items = db.scalars(select(User).order_by(User.username)).all()
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    gname = {g.id: g.name for g in groups}
    user_groups = {u.id: [gname[g] for g in user_group_ids(u.id) if g in gname] for u in items}
    return render(request, "users.html", user, items=items, groups=groups, user_groups=user_groups)


USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")
ROLE_NAMES = {"admin": N_("administrador"), "operator": N_("operador"), "viewer": N_("lector")}


def _active_admins(db, exclude_id: int | None = None) -> int:
    q = select(func.count(User.id)).where(User.role == "admin", User.active.is_(True))
    if exclude_id:
        q = q.where(User.id != exclude_id)
    return db.scalar(q) or 0


def _set_groups(db, uid: int, form):
    db.execute(delete(UserGroup).where(UserGroup.user_id == uid))
    for gid in form.getlist("group_ids"):
        db.add(UserGroup(user_id=uid, group_id=int(gid)))


@app.post("/users")
async def user_create(request: Request):
    """Crear un usuario nuevo (la edición de existentes está en /users/{id}/edit)."""
    user = require(request, "admin")
    form = await request.form()
    username = (form.get("username") or "").strip()
    role = form.get("role") if form.get("role") in ROLES else "viewer"
    password = form.get("password") or ""
    if not USERNAME_RE.match(username):
        flash(request, _("Nombre de usuario no válido: 2 a 64 caracteres (letras, números, punto, guion, @)."), "error")
        return go("/users")
    problem = password_problem(password, username)
    if problem:
        flash(request, problem, "error")
        return go("/users")
    if password != form.get("password2", password):
        flash(request, _("Las contraseñas no coinciden."), "error")
        return go("/users")
    with SessionLocal() as db:
        if db.scalars(select(User).where(func.lower(User.username) == username.lower())).first():
            flash(request, _f("Ya existe un usuario llamado «{0}». Para cambiarlo use «Editar» en su fila.", username), "error")
            return go("/users")
        u = User(username=username, role=role, email=form.get("email") or None, active=True,
                 password_hash=hash_password(password))
        db.add(u)
        db.flush()
        _set_groups(db, u.id, form)
        db.commit()
    log_action(request, user, "user_create", f"{username} rol={role} grupos={form.getlist('group_ids')}")
    flash(request, _f("Usuario «{0}» creado con rol {1}. Ya puede ingresar con su contraseña.", username, ROLE_NAMES[role]))
    return go("/users")


@app.get("/users/new")
def user_new_form(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    return render(request, "user_edit.html", user, u=None, groups=groups, selected=set(), now=now())


@app.get("/users/{uid}/edit")
def user_edit_form(request: Request, uid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        u = db.get(User, uid)
        if not u:
            return go("/users")
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    return render(request, "user_edit.html", user, u=u, groups=groups, selected=set(user_group_ids(uid)), now=now())


@app.post("/users/{uid}/edit")
async def user_edit(request: Request, uid: int):
    user = require(request, "admin")
    form = await request.form()
    with SessionLocal() as db:
        u = db.get(User, uid)
        if not u:
            return go("/users")
        role = form.get("role") if form.get("role") in ROLES else u.role
        active = form.get("active") == "on"
        was_admin = u.role == "admin" and u.active
        if was_admin and (role != "admin" or not active):
            if uid == user.id:
                flash(request, _("No puede quitarse a sí mismo el rol de administrador ni bloquearse."), "error")
                return go(f"/users/{uid}/edit")
            if _active_admins(db, exclude_id=uid) == 0:
                flash(request, _("Debe quedar al menos un administrador activo."), "error")
                return go(f"/users/{uid}/edit")
        changes = []
        password = form.get("password") or ""
        if password:
            problem = password_problem(password, u.username)
            if problem:
                flash(request, problem, "error")
                return go(f"/users/{uid}/edit")
            if password != form.get("password2", password):
                flash(request, _("Las contraseñas no coinciden."), "error")
                return go(f"/users/{uid}/edit")
            u.password_hash = hash_password(password)
            u.session_version = (u.session_version or 0) + 1  # cierra las sesiones abiertas de ese usuario
            u.locked_until, u.failed_logins = None, 0
            changes.append(("contraseña cambiada (sesiones cerradas)", _("contraseña cambiada (sesiones cerradas)")))
        if role != u.role:
            changes.append((f"rol {ROLE_NAMES[role]}", _f("rol {0}", _(ROLE_NAMES[role]))))
        if active != u.active:
            changes.append(("activado", _("activado")) if active else ("bloqueado", _("bloqueado")))
        u.role, u.active, u.email = role, active, form.get("email") or None
        _set_groups(db, uid, form)
        db.commit()
        name = u.username
    log_action(request, user, "user_edit", f"{name}: {', '.join(c[0] for c in changes) or 'sin cambios de rol/clave'} "
                                           f"grupos={form.getlist('group_ids')}")
    flash(request, _f("Usuario «{0}» actualizado ({1}).", name, ", ".join(c[1] for c in changes)) if changes
          else _f("Usuario «{0}» actualizado.", name))
    return go("/users")


@app.post("/users/{uid}/delete")
def user_delete(request: Request, uid: int):
    user = require(request, "admin")
    if uid == user.id:
        flash(request, _("No puede eliminarse a sí mismo."), "error")
        return go("/users")
    with SessionLocal() as db:
        u = db.get(User, uid)
        if u:
            if u.role == "admin" and u.active and _active_admins(db, exclude_id=uid) == 0:
                flash(request, _("No se puede eliminar al último administrador activo."), "error")
                return go("/users")
            name = u.username
            db.delete(u)
            db.commit()
            log_action(request, user, "user_delete", name)
            flash(request, _f("Usuario «{0}» eliminado.", name))
    return go("/users")


# --------------------------------------------------------------------------- ajustes
SETTING_KEYS = ["smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_from", "alert_emails",
                "webhook_url", "telegram_token", "telegram_chat_id", "alert_min_severity", "require_approval",
                "allow_self_approval", "notify_compliance", "retention_versions", "cisco_api_client_id",
                "dr_keep", "dr_remote_host", "dr_remote_port", "dr_remote_user", "dr_remote_path",
                "session_idle_minutes", "remember_days", "require_2fa_admins", "console_access", "console_autologin", "update_check",
                "org_name", "org_contact", "org_address", "default_lang",
                "ldap_enabled", "ldap_url", "ldap_starttls", "ldap_verify_tls", "ldap_bind_dn", "ldap_base_dn",
                "ldap_user_filter", "ldap_group_admin", "ldap_group_operator", "ldap_group_viewer", "ldap_default_role",
                "oidc_enabled", "oidc_tenant", "oidc_client_id", "oidc_discovery", "oidc_button", "oidc_redirect",
                "oidc_group_admin", "oidc_group_operator", "oidc_group_viewer", "oidc_default_role"]
CHECKBOX_KEYS = ("require_approval", "allow_self_approval", "notify_compliance", "require_2fa_admins",
                 "console_autologin", "update_check", "ldap_enabled", "ldap_starttls", "ldap_verify_tls", "oidc_enabled")
# valores por defecto cuando el ajuste nunca se guardó
SETTING_DEFAULTS = {"allow_self_approval": "1", "session_idle_minutes": "30", "remember_days": "14", "require_2fa_admins": "0",
                    "console_access": "admin", "console_autologin": "0", "update_check": "1",
                    "ldap_verify_tls": "1", "ldap_user_filter": "(&(objectClass=user)(sAMAccountName={username}))"}
SECRET_FORM_KEYS = ["smtp_password", "cisco_api_secret", "dr_remote_password", "ldap_bind_password", "oidc_client_secret",
                    "nvd_api_key"]
ROLE_KEYS = ("ldap_default_role", "oidc_default_role")


@app.get("/settings")
def settings(request: Request):
    user = require(request, "admin")
    values = {k: get_setting(k, SETTING_DEFAULTS.get(k, "")) for k in SETTING_KEYS}
    for k in SECRET_FORM_KEYS:
        values[f"{k}_set"] = bool(get_setting(k))
    from .config import NVD_API_KEY
    values["nvd_api_key_env"] = bool(NVD_API_KEY)  # la del archivo opennccm.env tiene prioridad
    return render(request, "settings.html", user, s=values, redirect_uri=_redirect_uri(request))


@app.post("/settings/ldap-test")
async def settings_ldap_test(request: Request):
    """«Probar conexión» con Active Directory, con lo que hay escrito en el formulario (sin guardarlo)."""
    user = require(request, "admin")
    form = await request.form()
    cfg = {k: (form.get(f"ldap_{k}") or "").strip() for k in ("url", "bind_dn", "base_dn", "user_filter",
                                                               "group_admin", "group_operator", "group_viewer", "default_role")}
    cfg["starttls"] = "1" if form.get("ldap_starttls") == "on" else "0"
    cfg["verify_tls"] = "1" if form.get("ldap_verify_tls") == "on" else "0"
    cfg["bind_password"] = form.get("ldap_bind_password") or get_setting("ldap_bind_password", "")
    if not cfg["url"]:
        return JSONResponse({"ok": False, "msg": _("Escriba la dirección del servidor.")})
    try:
        res = extauth.ldap_test(cfg, (form.get("test_user") or "").strip())
    except extauth.ExtAuthError as e:
        return JSONResponse({"ok": False, "msg": str(e)})
    log_action(request, user, "ldap_test", cfg["url"])
    if res == "ok":
        return JSONResponse({"ok": True, "msg": _("Conexión correcta: la cuenta de servicio inició sesión.")})
    if res == "notfound":
        return JSONResponse({"ok": False, "msg": _("Conexión correcta, pero no se encontró ese usuario con el filtro indicado.")})
    _f_, role, n = res.split(":")
    names = {"admin": _("administrador"), "operator": _("operador"), "viewer": _("lector")}
    return JSONResponse({"ok": bool(role), "msg": _f("Usuario encontrado, en {0} grupos. Rol en OpenNCCM: {1}.", n,
                                                      names.get(role, _("sin acceso")))})


@app.post("/settings")
async def settings_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    for k in SETTING_KEYS:
        if k == "require_2fa_admins" and form.get(k) == "on" and not user.totp_enabled:
            # exigirla sin tenerla dejaría a este administrador fuera de todo salvo «Mi cuenta»
            flash(request, _("Para exigir la verificación en dos pasos a los administradores, actívela primero en su propia cuenta (Mi cuenta → Verificación en dos pasos)."), "error")
            continue
        if k in CHECKBOX_KEYS:
            set_setting(k, "1" if form.get(k) == "on" else "0")
        elif k == "remember_days":
            if k not in form:  # formulario sin este campo (otra página o una versión anterior): no se toca
                continue
            v = (form.get(k) or "0").strip()
            set_setting(k, str(min(max(int(v), 0), 30)) if v.isdigit() else "14")
        elif k == "session_idle_minutes":
            v = (form.get(k) or "30").strip()
            set_setting(k, str(min(max(int(v), 5), 720)) if v.isdigit() else "30")
        elif k == "default_lang":
            set_setting(k, form.get(k) if form.get(k) in LANGS else "es")
        elif k == "console_access":
            set_setting(k, form.get(k) if form.get(k) in ("off", "admin", "operator") else "admin")
        elif k in ROLE_KEYS:
            set_setting(k, form.get(k) if form.get(k) in ROLES else "")
        else:
            set_setting(k, (form.get(k) or "").strip())
    clear_settings_cache()
    for k in SECRET_FORM_KEYS:
        if form.get(k):
            set_setting(k, form[k])
    log_action(request, user, "settings_save")
    flash(request, _("Ajustes guardados"))
    return go("/settings")


@app.post("/settings/test-alert")
def settings_test(request: Request):
    require(request, "admin")
    errors = services.send_alert("[OpenNCCM] Prueba de notificación", "Si recibe este mensaje, las alertas funcionan.")
    if errors:
        for e in errors:
            flash(request, e, "error")
    else:
        flash(request, _("Notificación de prueba enviada"))
    return go("/settings")


# --------------------------------------------------------------------------- informes
@app.get("/reports")
def reports_page(request: Request):
    return render(request, "reports.html", require(request), titles=reports.TITLES)


@app.get("/reports/view/{kind}")
def report_view(request: Request, kind: str, days: int = 30):
    """Informe en pantalla (tabla) con enlace de descarga."""
    user = require(request, "admin" if kind == "audit" else "viewer")
    if kind not in reports.TITLES:
        return PlainTextResponse("Informe desconocido", 404)
    header, rows = reports.build(kind, device_scope(user), days)
    return render(request, "report_view.html", user, kind=kind, title=reports.TITLES[kind], header=header,
                  rows=rows, days=days)


@app.get("/reports/{kind}.csv")
def report_csv(request: Request, kind: str, days: int = 30):
    user = require(request, "admin" if kind == "audit" else "viewer")
    if kind not in reports.TITLES:
        return PlainTextResponse("Informe desconocido", 404)
    log_action(request, user, "report", kind)
    header, rows = reports.build(kind, device_scope(user), days)
    return csv_response(kind, reports.to_csv(header, rows))


@app.post("/reports/email")
def report_email(request: Request):
    user = require(request, "admin")
    errors = reports.email_reports(["inventory", "changes", "compliance", "conflicts", "baseline", "eol"])
    for e in errors:
        flash(request, e, "error")
    if not errors:
        flash(request, _("Informes enviados por correo"))
    log_action(request, user, "report_email")
    return go("/reports")


# --------------------------------------------------------------------------- API JSON mínima
@app.get("/api/devices")
def api_devices(request: Request):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        return [{"id": d.id, "name": d.name, "host": d.host, "type": d.device_type, "model": d.model,
                 "serial": d.serial, "version": d.os_version, "status": d.last_status,
                 "platform": PROFILE_MAP[d.device_type].label if d.device_type in PROFILE_MAP else d.device_type,
                 "last_backup": d.last_backup_at.isoformat() if d.last_backup_at else None}
                for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]


@app.get("/api/config-search")
def api_config_search(request: Request, q: str = ""):
    """Paleta Ctrl K: las primeras líneas de configuración que contienen el texto (última versión de cada equipo)."""
    user = require(request)
    q = q.strip()
    if len(q) < 2:
        return []
    ql, scope, hits = q.lower(), device_scope(user), []
    with SessionLocal() as db:
        for dev in db.scalars(select(Device).order_by(Device.name)):
            if scope is not None and dev.id not in scope:
                continue
            v = services.latest_version(db, dev.id)
            if not v:
                continue
            for n, line in enumerate(decrypt(v.content_enc).splitlines(), 1):
                if ql in line.lower():
                    hits.append({"device": dev.name, "device_id": dev.id, "line_no": n, "line": line.strip()[:120]})
                    if len(hits) >= 4:
                        return hits
    return hits


from . import routes_ops  # noqa: E402  (rutas de firmware, EOL, análisis y sistema)

app.include_router(routes_ops.router)
from . import console, security  # noqa: E402  (consola SSH web; 2 pasos, certificado, privacidad)

app.include_router(security.router)
app.include_router(console.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(BASE_DIR / "static" / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


STARTED = int(time.time())  # cuándo arrancó este proceso: la página de espera sabe así que el servicio ya se reinició


@app.get("/healthz")
def healthz():
    from . import __version__
    with SessionLocal() as db:  # también verifica que la base de datos responda
        db.scalar(select(func.count(User.id)))
    return {"status": "ok", "version": __version__, "started": STARTED}

