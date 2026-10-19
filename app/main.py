"""Aplicación web OpenNCCM (FastAPI)."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
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
                 DeviceGroup, Event, Policy, Rule, SessionLocal, Schedule, User, UserGroup, Vulnerability, audit,
                 get_setting, init_db, now, set_setting)
from .drivers import PROFILE_MAP, get_profile
from .i18n import LANGS, LangMiddleware, N_, _, _f
from .web import csv_response, flash, go, ip, log_action, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("opennccm")


@asynccontextmanager
async def lifespan(_app):
    init_db()
    try:
        n = services.refresh_inventory_from_raw()
        if n:
            log.info("Inventario actualizado desde la última salida guardada en %d equipos", n)
    except Exception:  # noqa: BLE001
        log.exception("No se pudo releer el inventario guardado")
    scheduler.start()
    yield
    scheduler.scheduler.shutdown(wait=False)


app = FastAPI(title="OpenNCCM", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(LangMiddleware)  # va dentro de la sesión: lee el idioma elegido
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="strict",
                   https_only=os.environ.get("NCM_SECURE_COOKIE", "0") == "1", max_age=12 * 3600,
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
    return render(request, "login.html", None, ca_available=TLS_CA.is_file())


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
    username, password = form.get("username", ""), form.get("password", "")
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == username)).first()
        if user is None:
            dummy_verify(password)  # mismo tiempo de respuesta: no revela si el usuario existe
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
    return render(request, "account.html", require(request), themes=THEMES)


@app.post("/account")
async def account_save(request: Request):
    user = require(request)
    form = await request.form()
    with SessionLocal() as db:
        u = db.get(User, user.id)
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


THEMES = {"auto": N_("Automático (según el sistema)"), "claro": N_("Claro"), "oscuro": N_("Oscuro"),
          "noche": N_("Azul noche"), "bosque": N_("Bosque"), "arena": N_("Arena"), "oceano": N_("Océano"),
          "atardecer": N_("Atardecer"), "lavanda": N_("Lavanda"), "grafito": N_("Grafito"),
          "contraste": N_("Alto contraste")}


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
    theme = form.get("theme") if form.get("theme") in THEMES else "auto"
    with SessionLocal() as db:
        db.get(User, user.id).theme = theme
        db.commit()
    flash(request, _f("Tema «{0}» aplicado.", _(THEMES[theme])))
    return go(form.get("next") or "/account")


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
    from . import charts
    data = reports.analytics(scope, 30)
    eol_bad = sum(n for label, n in data["eol"] if label in ("Sin soporte", "Sin parches de software"))
    comp = round(100 * cpass / (cpass + cfail)) if (cpass + cfail) else None
    server = _server_panel() if user.role == "admin" else None
    return render(request, "dashboard.html", user, total=total, ok=ok, err=err, conflicts=conflicts,
                  changes24=changes24, comp=comp, alarms=alarms, vcrit=vcrit, pending=pending, drift=drift,
                  eol_bad=eol_bad, events=events, recent=recent, server=server,
                  chart_changes=charts.columns(data["per_day"], " cambios"))


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
        if status == "conflict":
            stmt = stmt.where(Device.startup_conflict.is_(True))
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
    from .console import console_allowed
    return render(request, "devices.html", user, devices=devs, groups=groups, q=q, group=group, status=status,
                  can_console=console_allowed(user))


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
def device_detail(request: Request, dev_id: int):
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
    from . import eol
    with SessionLocal() as db:
        entry = eol.match_entry(dev, list(db.scalars(select(eol.EolEntry))))
    from .console import console_allowed
    return render(request, "device.html", user, can_console=console_allowed(user, dev.id), device=dev,
                  versions=versions, comp=comp, vulns=vulns,
                  events=events, inventory=inventory, profile=get_profile(dev.device_type), images=images,
                  eol_entry=entry, eol_status=eol.status(entry), eol_labels=eol.STATUS_LABEL,
                  eol_sev=eol.STATUS_SEV)


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
    try:
        n = services.scan_cves(dev_id)
        flash(request, _("Sin versión de firmware en inventario: haga un respaldo primero") if n < 0
              else _f("{0} CVE encontrados en NVD", n), "error" if n < 0 else "ok")
    except Exception as e:  # noqa: BLE001
        flash(request, _f("Error consultando NVD: {0}", e), "error")
    return go(f"/devices/{dev_id}#vulns")


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
    return render(request, "version.html", user, v=v, text=text, prev=prev, is_baseline=is_baseline,
                  small=small, current=current)


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
        dev.baseline_drift = latest is not None and latest.sha256 != v.sha256
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
    return go(f"/diff?a={dev.baseline_version_id}&b={latest.id}&view=side")


@app.get("/diff")
def diff_view(request: Request, a: int = 0, b: int = 0, ctx: int = 3, view: str = "side"):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        va, vb = db.get(ConfigVersion, a), db.get(ConfigVersion, b)
        if not va or not vb:
            flash(request, _("Seleccione dos versiones"), "error")
            return go("/")
        check_device(user, va.device_id)
        check_device(user, vb.device_id)
        (va.device.name, vb.device.name)  # carga la relación antes de cerrar la sesión
        ta, tb = decrypt(va.content_enc), decrypt(vb.content_enc)
        lines = services.diff_lines(ta, tb, f"{va.device.name} #{va.id}", f"{vb.device.name} #{vb.id}", ctx)
        rows = services.side_by_side(ta, tb, ctx) if view == "side" else []
        q = select(ConfigVersion).order_by(ConfigVersion.id.desc()).limit(500)
        if scope is not None:
            q = q.where(ConfigVersion.device_id.in_(scope))
        all_versions = db.scalars(q).all()
        for x in all_versions:
            x.device.name  # carga la relación antes de cerrar la sesión
    added, removed = services.diff_stats(ta, tb)
    return render(request, "diff.html", user, va=va, vb=vb, lines=lines, rows=rows, ctx=ctx, view=view,
                  all_versions=all_versions, added=added, removed=removed)


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
def search(request: Request, q: str = "", regex: int = 0):
    user = require(request)
    results = []
    if q:
        try:
            pat = re.compile(q if regex else re.escape(q), re.IGNORECASE)
        except re.error as e:
            flash(request, _f("Regex inválida: {0}", e), "error")
            return go("/search")
        scope = device_scope(user)
        with SessionLocal() as db:
            for dev in db.scalars(select(Device).order_by(Device.name)):
                if scope is not None and dev.id not in scope:
                    continue
                v = services.latest_version(db, dev.id)
                if not v:
                    continue
                hits = [(n, l) for n, l in enumerate(decrypt(v.content_enc).splitlines(), 1) if pat.search(l)]
                if hits:
                    results.append((dev, v, hits[:50], len(hits)))
    return render(request, "search.html", user, q=q, regex=regex, results=results)


# --------------------------------------------------------------------------- credenciales y grupos
@app.get("/credentials")
def credentials(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        creds = db.scalars(select(Credential).order_by(Credential.name)).all()
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    return render(request, "credentials.html", user, creds=creds, groups=groups)


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
    return go("/credentials#groups")


@app.post("/groups/{gid}/delete")
def group_delete(request: Request, gid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        g = db.get(DeviceGroup, gid)
        if g:
            db.delete(g)
            db.commit()
            log_action(request, user, "group_delete", g.name)
    return go("/credentials#groups")


# --------------------------------------------------------------------------- configlets
@app.get("/configlets")
def configlets(request: Request):
    user = require(request)
    with SessionLocal() as db:
        items = db.scalars(select(Configlet).order_by(Configlet.name)).all()
    return render(request, "configlets.html", user, items=items)


@app.get("/configlets/new")
@app.get("/configlets/{cid}/edit")
def configlet_form(request: Request, cid: int | None = None):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(Configlet, cid) if cid else None
    return render(request, "configlet_form.html", user, c=c)


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
    return render(request, "configlet_run.html", user, c=c, devices=devs, groups=groups, found_vars=found_vars)


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
            return render(request, "exec_result.html", user, c=c, outputs=outputs)
        title, body = c.name, c.body
    cr_id = _create_cr(user, form.get("title") or f"Configlet: {title}", body, variables, ids)
    log_action(request, user, "change_request", f"#{cr_id} {title}")
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- solicitudes de cambio
def _create_cr(user, title, body, variables, ids, is_rollback=False, kind="config", firmware_id=None,
               reload=False) -> int:
    # la actualización de firmware siempre requiere aprobación
    auto = get_setting("require_approval", "1") != "1" and kind == "config"
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
def changes(request: Request, status: str = ""):
    user = require(request)
    with SessionLocal() as db:
        stmt = select(ChangeRequest).order_by(ChangeRequest.id.desc()).limit(300)
        if status:
            stmt = stmt.where(ChangeRequest.status == status)
        scope = device_scope(user)
        items = [cr for cr in db.scalars(stmt)
                 if scope is None or set(json.loads(cr.device_ids)) <= scope]
    return render(request, "changes.html", user, items=items, status=status)


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
    can_review = ROLES[user.role] >= ROLES["admin"] and (
        cr.requested_by != user.username or get_setting("allow_self_approval", "1") == "1")
    return render(request, "change.html", user, cr=cr, devices=devs, previews=previews, can_review=can_review)


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
    from . import charts
    comp = reports.analytics(scope)["compliance"]
    chart = charts.bars([(n, v) for n, v, _z in comp], "%", vmax=100, status=True,
                        link=[f"/compliance/policies/{pid}" for _a, _b, pid in comp])
    return render(request, "compliance.html", user, policies=policies, groups=groups, fails=fails,
                  devices_checked=len(summary), chart=chart,
                  devices_ok=sum(1 for _k, p, t in summary if p == t))


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


@app.get("/compliance/policies/{pid}")
def policy_detail(request: Request, pid: int):
    user = require(request)
    with SessionLocal() as db:
        p = db.get(Policy, pid)
        p.rules  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
    return render(request, "policy.html", user, p=p, groups=groups)


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
                  DAYS=cronutil.DAYS, FREQS=cronutil.FREQS, ACTIONS=SCHEDULE_ACTIONS, sf=sf)


SCHEDULE_ACTIONS = {"backup": N_("Respaldo de configuración"), "compliance": N_("Auditoría de cumplimiento"),
                    "cve": N_("Escaneo de vulnerabilidades"), "configlet": N_("Ejecutar configlet"),
                    "report_email": N_("Informes por correo"), "system_backup": N_("Copia de recuperación")}


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
@app.get("/events")
def events(request: Request, sev: str = "", unack: int = 0, page: int = 1):
    user = require(request)
    with SessionLocal() as db:
        stmt = select(Event).order_by(Event.id.desc())
        if sev:
            stmt = stmt.where(Event.severity == sev)
        if unack:
            stmt = stmt.where(Event.acknowledged.is_(False))
        scope = device_scope(user)
        if scope is not None:  # eventos de sus equipos (los globales solo los ven usuarios sin restricción)
            stmt = stmt.where(Event.device_id.in_(scope))
        items = db.scalars(stmt.offset((page - 1) * 100).limit(100)).all()
        for e in items:
            e.device.name if e.device else None  # carga la relación antes de cerrar la sesión
    return render(request, "events.html", user, items=items, sev=sev, unack=unack, page=page)


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
        for e in db.scalars(stmt):
            e.acknowledged = True
        db.commit()
    log_action(request, user, "events_ack", form.get("id") or "all")
    return go(request.headers.get("referer") or "/events")


@app.get("/vulns")
def vulns(request: Request):
    user = require(request)
    with SessionLocal() as db:
        scope = device_scope(user)
        items = [v for v in db.scalars(select(Vulnerability).order_by(Vulnerability.score.desc().nulls_last())
                                       .limit(5000)) if scope is None or v.device_id in scope][:2000]
        for v in items:
            v.device.name  # carga la relación antes de cerrar la sesión
    return render(request, "vulns.html", user, items=items)


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
                "session_idle_minutes", "require_2fa_admins", "console_access", "console_autologin", "update_check",
                "org_name", "org_contact", "org_address", "default_lang"]
CHECKBOX_KEYS = ("require_approval", "allow_self_approval", "notify_compliance", "require_2fa_admins",
                 "console_autologin", "update_check")
# valores por defecto cuando el ajuste nunca se guardó
SETTING_DEFAULTS = {"allow_self_approval": "1", "session_idle_minutes": "30", "require_2fa_admins": "0",
                    "console_access": "admin", "console_autologin": "0", "update_check": "1"}
SECRET_FORM_KEYS = ["smtp_password", "cisco_api_secret", "dr_remote_password"]


@app.get("/settings")
def settings(request: Request):
    user = require(request, "admin")
    values = {k: get_setting(k, SETTING_DEFAULTS.get(k, "")) for k in SETTING_KEYS}
    for k in SECRET_FORM_KEYS:
        values[f"{k}_set"] = bool(get_setting(k))
    return render(request, "settings.html", user, s=values)


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
        elif k == "session_idle_minutes":
            v = (form.get(k) or "30").strip()
            set_setting(k, str(min(max(int(v), 5), 720)) if v.isdigit() else "30")
        elif k == "default_lang":
            set_setting(k, form.get(k) if form.get(k) in LANGS else "es")
        elif k == "console_access":
            set_setting(k, form.get(k) if form.get(k) in ("off", "admin", "operator") else "admin")
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
                 "last_backup": d.last_backup_at.isoformat() if d.last_backup_at else None}
                for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]


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


@app.get("/healthz")
def healthz():
    from . import __version__
    with SessionLocal() as db:  # también verifica que la base de datos responda
        db.scalar(select(func.count(User.id)))
    return {"status": "ok", "version": __version__}

