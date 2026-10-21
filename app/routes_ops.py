"""Rutas: firmware, fin de vida (EOL/EOS), análisis y recuperación ante desastres."""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select

from . import __version__, cronutil, eol, firmware, fwupdates, reports, sysbackup, updates
from .config import UPDATE_REPO
from .auth import Forbidden, device_scope, filter_ids, require
from .db import Device, DeviceGroup, EolEntry, FirmwareImage, SessionLocal
from .drivers import get_profile
from .i18n import N_, _, _f
from .web import flash, go, log_action, render

router = APIRouter()


# --------------------------------------------------------------------------- firmware
def _mes(d) -> str:
    from .web import _MESES
    return f"{_(_MESES[d.month - 1])} {d.year}"


def _life(d, e, st) -> dict:
    """Fila de «Tus equipos»: cuánto soporte del fabricante le queda (barra) y el texto del prototipo."""
    from datetime import datetime
    sup, sale = (e.end_of_support if e else None), (e.end_of_sale if e else None)
    pct = 100
    if sup:
        pct = max(0, min(100, round((sup - datetime.now()).days / (5 * 365) * 100)))
    if st == "sin_soporte":
        txt, cls = (_f("Sin soporte desde {0}", _mes(sup)) if sup else _("Sin soporte")), "crit"
    elif st == "fin_mantenimiento":
        txt, cls = (_f("Sin parches de software · soporte hasta {0}", _mes(sup)) if sup else _("Sin parches de software")), "warn"
    elif st in ("fin_venta", "proximo"):
        txt = (_f("Fin de venta {0} · soporte hasta {1}", sale.year, _mes(sup)) if sale and sup
               else _f("Soporte hasta {0}", _mes(sup)) if sup else _("Fin de venta"))
        cls = "warn"
    else:
        txt, cls = (_f("Soporte hasta {0}", sup.year) if sup else _("Vigente")), "ok"
    return {"d": d, "e": e, "st": st, "pct": pct, "txt": txt, "cls": cls}


def _fw_page(request: Request, tab: str, status: str = "", img: int = 0, vend: str = ""):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        alldevs = [d for d in db.scalars(select(Device).order_by(Device.name)) if scope is None or d.id in scope]
        devs = [d for d in alldevs if firmware.supports_upgrade(d.device_type)]
        for d in devs:
            d.group.name if d.group else None  # carga la relación antes de cerrar la sesión
        groups = db.scalars(select(DeviceGroup).order_by(DeviceGroup.name)).all()
        entries = db.scalars(select(EolEntry).order_by(EolEntry.vendor, EolEntry.model_pattern)).all()
    images = firmware.list_images()
    compat = {}
    for i in images:
        try:
            rx = re.compile(i.device_type_filter) if i.device_type_filter else None
        except re.error:
            rx = None
        compat[i.id] = [d.name for d in alldevs if rx is None or rx.search(d.device_type)]
    rows = eol.report(scope)
    counts = {}
    for _a, _b, st in rows:
        counts[st] = counts.get(st, 0) + 1
    if status:
        rows = [r for r in rows if r[2] == status]
    life = [_life(d, e, st) for d, e, st in rows if e is not None]
    life.sort(key=lambda r: r["pct"])
    vendors = sorted({e.vendor for e in entries if e.vendor})
    if vend:
        entries = [e for e in entries if e.vendor == vend]
    from datetime import datetime
    from .db import get_setting
    fw_rows = fwupdates.all_rows(scope)
    return render(request, "firmware.html", user, tab=tab, images=images, devices=devs, groups=groups, compat=compat,
                  fw_rows=fw_rows, fw_n=sum(1 for r in fw_rows if r["status"] == "update"),
                  fw_state=fwupdates.state(), fw_enabled=get_setting("fw_update_check", "1") != "0",
                  fw_checked=get_setting("fw_update_checked_at", ""), fw_labels=FW_SOURCES,
                  life=life, no_data=sum(1 for r in rows if r[1] is None), rows=rows, entries=entries,
                  counts=counts, status=status, labels=eol.STATUS_LABEL, sev=eol.STATUS_SEV, sel_img=img,
                  vendors=vendors, vend=vend, today=datetime.now(),
                  n_entries=len(entries) if not vend else None)


@router.get("/firmware")
def firmware_page(request: Request, img: int = 0):
    return _fw_page(request, "imagenes", img=img)


# --------------------------------------------------------------------------- actualizaciones de firmware disponibles
FW_SOURCES = {"mikrotik": N_("MikroTik"), "ubiquiti": N_("Ubiquiti"), "nvd": N_("NVD (NIST)")}


@router.post("/firmware/updates/check")
async def fw_updates_check(request: Request):
    """«Comprobar ahora»: un equipo (device_id) o todos los que el usuario puede ver, en segundo plano."""
    user = require(request, "operator")
    form = await request.form()
    one = int(form.get("device_id") or 0)
    if one:
        ids = filter_ids(user, [one])
        if not ids:
            raise Forbidden()
    else:
        scope = device_scope(user)
        ids = None if scope is None else sorted(scope)
    started = fwupdates.start_check(ids)
    log_action(request, user, "fw_update_check", f"device={one}" if one else "todos")
    if not started:
        flash(request, _("Ya hay una comprobación en curso; espere a que termine."), "error")
    back = form.get("back") or ("/devices/%d#firmware" % one if one else "/firmware#disponibles")
    return go(back if back.startswith("/") and not back.startswith("//") else "/firmware#disponibles")


@router.get("/firmware/updates/status")
def fw_updates_status(request: Request, device_id: int = 0):
    require(request)
    st = fwupdates.state()
    dl = fwupdates.download_state(device_id) if device_id else None
    return {"running": st["running"], "done": st["done"], "total": st["total"],
            "downloading": bool(dl and dl.get("running"))}


@router.post("/firmware/updates/{device_id}/download")
def fw_updates_download(request: Request, device_id: int):
    user = require(request, "admin")
    if not filter_ids(user, [device_id]):
        raise Forbidden()
    up = fwupdates.for_device(device_id)
    if not up or not up.get("download_url"):
        flash(request, _("Este equipo no tiene una descarga pública de firmware."), "error")
    elif fwupdates.start_download(device_id, user.username):
        log_action(request, user, "fw_download", f"device={device_id} {up.get('target')} {up.get('download_url')}")
    return go(f"/devices/{device_id}#firmware")


@router.post("/firmware/updates/settings")
async def fw_updates_settings(request: Request):
    from .db import set_setting
    user = require(request, "admin")
    form = await request.form()
    on = form.get("enabled") == "on"
    set_setting("fw_update_check", "1" if on else "0")
    log_action(request, user, "fw_update_settings", "on" if on else "off")
    flash(request, _("Se comprobarán las actualizaciones de firmware una vez al día.") if on
          else _("Comprobación diaria de actualizaciones de firmware desactivada."))
    return go("/firmware#disponibles")


@router.get("/firmware/new")
def firmware_new(request: Request):
    """«Cargar imagen» en un panel lateral (prototipo)."""
    user = require(request, "admin")
    return render(request, "fw_upload.html", user)


@router.post("/firmware/upload")
async def firmware_upload(request: Request):
    user = require(request, "admin")
    form = await request.form()
    up = form["file"]
    try:
        img = firmware.save_image(up.filename, up.file, user.username, form.get("version"),
                                  form.get("device_type_filter"))
    except ValueError as e:
        flash(request, str(e), "error")
        return go("/firmware")
    log_action(request, user, "firmware_upload", f"{img.filename} md5={img.md5}")
    flash(request, _f("Imagen {0} cargada ({1} bytes, MD5 {2})", img.filename, format(img.size, ','), img.md5))
    return go("/firmware")


@router.post("/firmware/{image_id}/delete")
def firmware_delete(request: Request, image_id: int):
    user = require(request, "admin")
    firmware.delete_image(image_id)
    log_action(request, user, "firmware_delete", str(image_id))
    return go("/firmware")


@router.post("/firmware/upgrade")
async def firmware_upgrade(request: Request):
    from .main import _create_cr
    user = require(request, "operator")
    form = await request.form()
    requested = [int(x) for x in form.getlist("device_ids")]
    ids = filter_ids(user, requested)
    if len(ids) != len(requested):
        raise Forbidden()
    if not ids or not form.get("image_id"):
        flash(request, _("Seleccione una imagen y al menos un equipo"), "error")
        return go("/firmware")
    with SessionLocal() as db:
        img = db.get(FirmwareImage, int(form["image_id"]))
        bad = [d.name for d in db.scalars(select(Device).where(Device.id.in_(ids)))
               if not firmware.supports_upgrade(d.device_type)
               or (img.device_type_filter and not re.search(img.device_type_filter, d.device_type))]
    if bad:
        flash(request, _f("La imagen no es compatible con: {0}", ', '.join(bad)), "error")
        return go("/firmware")
    reload = form.get("reload") == "on"
    cr_id = _create_cr(user, f"Firmware {img.filename} en {len(ids)} equipo(s){' + reinicio' if reload else ''}",
                       "", {}, ids, kind="firmware", firmware_id=img.id, reload=reload)
    log_action(request, user, "firmware_request", f"#{cr_id} {img.filename} -> {ids}")
    flash(request, _("Solicitud de actualización creada. Un administrador debe aprobarla."))
    return go(f"/changes/{cr_id}")


# --------------------------------------------------------------------------- EOL / EOS
@router.get("/eol")
def eol_page(request: Request, status: str = "", vend: str = ""):
    return _fw_page(request, "eol", status=status, vend=vend)


def _models() -> dict:
    with SessionLocal() as db:
        return {d.name: d.model or "" for d in db.scalars(select(Device).order_by(Device.name))}


@router.get("/eol/new")
def eol_new(request: Request):
    user = require(request, "admin")
    return render(request, "eol_form.html", user, e=None, models=_models())


@router.get("/eol/import")
def eol_import_form(request: Request):
    user = require(request, "admin")
    return render(request, "eol_import.html", user)


@router.get("/eol/entries/{eid}")
def eol_edit(request: Request, eid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        e = db.get(EolEntry, eid)
    if e is None:
        return go("/eol")
    return render(request, "eol_form.html", user, e=e, models=_models())


@router.post("/eol/entries")
async def eol_add(request: Request):
    user = require(request, "admin")
    form = await request.form()
    try:
        re.compile(form["model_pattern"])
        with SessionLocal() as db:
            e = db.get(EolEntry, int(form["id"])) if form.get("id") else None
            if e is None:
                e = EolEntry()
                db.add(e)
            e.vendor, e.model_pattern = form.get("vendor") or None, form["model_pattern"].strip()
            e.end_of_sale = eol._date(form.get("end_of_sale"))
            e.end_of_sw_maintenance = eol._date(form.get("end_of_sw_maintenance"))
            e.end_of_support = eol._date(form.get("end_of_support"))
            e.replacement, e.url = form.get("replacement") or None, form.get("url") or None
            db.commit()
    except (re.error, ValueError) as e:
        flash(request, _f("No se pudo guardar: {0}", e), "error")
        return go("/eol")
    log_action(request, user, "eol_add", form["model_pattern"])
    flash(request, _("Entrada EOL guardada") if form.get("id") else _("Entrada EOL agregada"))
    return go("/eol")


@router.post("/eol/import")
async def eol_import(request: Request):
    user = require(request, "admin")
    form = await request.form()
    created, errors = eol.import_csv((await form["file"].read()).decode("utf-8-sig"))
    log_action(request, user, "eol_import", f"{created} entradas")
    flash(request, _f("{0} entradas EOL importadas", created))
    for e in errors[:10]:
        flash(request, e, "error")
    return go("/eol")


@router.post("/eol/entries/{eid}/delete")
def eol_delete(request: Request, eid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        e = db.get(EolEntry, eid)
        if e:
            db.delete(e)
            db.commit()
    log_action(request, user, "eol_delete", str(eid))
    return go("/eol")


@router.post("/eol/cisco-sync")
def eol_cisco_sync(request: Request):
    user = require(request, "admin")
    try:
        n, errors = eol.cisco_eox_sync()
        flash(request, _f("{0} registros actualizados desde Cisco EoX", n))
        for e in errors[:5]:
            flash(request, e, "error")
    except Exception as e:  # noqa: BLE001
        flash(request, _f("Error consultando Cisco EoX: {0}", e), "error")
    log_action(request, user, "eol_cisco_sync")
    return go("/eol")


# --------------------------------------------------------------------------- análisis
@router.get("/analytics")
def analytics_page(request: Request, days: int = 30):
    """Análisis (prototipo): indicadores, mapa de calor, origen de los cambios y barras horizontales."""
    from datetime import timedelta
    from .db import ChangeRequest, now
    user = require(request)
    days = max(7, min(days, 365))
    scope = device_scope(user)
    data = reports.analytics(scope, days)
    with SessionLocal() as db:
        since = now() - timedelta(days=days)
        reverted = sum(1 for cr in db.scalars(select(ChangeRequest).where(ChangeRequest.created_at >= since,
                                                                           ChangeRequest.is_rollback.is_(True),
                                                                           ChangeRequest.status == "done")))
    prev, cur = data["prev_changes"], data["changes"]
    delta = round((cur - prev) / prev * 100) if prev else None
    heat = data["heat"]
    hmax = max((v for row in heat for v in row), default=0)
    colors = {"scheduled": "var(--brand)", "syslog": "var(--cat-2)", "manual": "var(--cat-3)", "push": "var(--cat-4)"}
    total = sum(n for _t, n in data["by_trigger"]) or 1
    src, acc = [], 0.0
    circ = 2 * 3.14159265 * 46
    for t in TRIGGER_ORDER + [t for t, _n in data["by_trigger"] if t not in TRIGGER_ORDER]:
        n = dict(data["by_trigger"]).get(t, 0)
        if not n:
            continue
        ln = n / total * circ
        src.append({"label": _(TRIGGER_LABELS.get(t, t)), "pct": round(n / total * 100), "n": n,
                    "color": colors.get(t, "var(--muted)"), "dash": f"{max(ln - 2, 0.5):.1f} {circ - ln + 2:.1f}",
                    "off": f"{-acc:.1f}"})
        acc += ln
    life = {}
    for k, n in data["eol_status"]:
        key = {"vigente": N_("Con soporte"), "proximo": N_("Con soporte"), "fin_venta": N_("Fin de venta"),
               "fin_mantenimiento": N_("Sin parches de software"), "sin_soporte": N_("Sin soporte"),
               "desconocido": N_("Sin datos")}.get(k, k)
        life[key] = life.get(key, 0) + n
    life_rows = [(_(k), life[k]) for k in (N_("Con soporte"), N_("Fin de venta"), N_("Sin parches de software"),
                                           N_("Sin soporte"), N_("Sin datos")) if life.get(k)]
    by_user = [(_("en el equipo (syslog)") if u == "—" else u, n) for u, n in data["by_user"]]
    weekdays = [_(name).capitalize()[:3] for _k, name in cronutil.DAYS]
    return render(request, "analytics.html", user, data=data, days=days, delta=delta, reverted=reverted,
                  heat=heat, hmax=hmax, weekdays=weekdays, src=src, life_rows=life_rows, by_user=by_user,
                  spark_pts=data["per_week"], syslog_n=dict(data["by_trigger"]).get("syslog", 0),
                  dev_links=[f"/devices?q={quote(n)}" for n, _v in data["by_device"][:5]],
                  comp_rows=[(n, v) for n, v, _p in data["compliance"]],
                  comp_links=[f"/compliance/policies/{p}" for _n, _v, p in data["compliance"]])


TRIGGER_ORDER = ["manual", "scheduled", "syslog", "push"]
TRIGGER_LABELS = {"manual": N_("Manual"), "scheduled": N_("Programado"), "syslog": N_("Detectado por syslog"),
                  "push": N_("Aplicado por OpenNCCM")}
EOL_ORDER = ["vigente", "proximo", "fin_venta", "fin_mantenimiento", "sin_soporte", "desconocido"]
EOL_CLASS = {"vigente": "st-ok", "proximo": "st-warn", "fin_venta": "st-warn", "fin_mantenimiento": "st-major",
             "sin_soporte": "st-crit", "desconocido": "st-none"}


# --------------------------------------------------------------------------- recuperación ante desastres
@router.get("/system")
def system_page(request: Request):
    user = require(request, "admin")
    from .config import DATABASE_URL, KEY_FILE
    backups = [(p.name, p.stat().st_size, p.stat().st_mtime) for p in sysbackup.list_backups()]
    from . import systime
    from .drivers import PROFILES, unknown_profiles
    zones: dict[str, list[str]] = {}
    for z in systime.timezones():
        zones.setdefault(z.split("/")[0] if "/" in z else _("Otras"), []).append(z)
    from .main import _server_panel
    return render(request, "system.html", user, backups=backups, db_url=DATABASE_URL.split("@")[-1],
                  server=_server_panel(),
                  key_file=str(KEY_FILE), profiles_count=len(PROFILES),
                  vendors_count=len({p.vendor for p in PROFILES}), unknown=unknown_profiles(),
                  clock=systime.info(), zones=zones, upd=updates.status(),
                  changelog=[(v, updates.md_lite(t)) for v, t in updates.changelog_sections(6)],
                  latest_notes=updates.md_lite(updates.status()["notes"]),
                  releases_url=f"https://github.com/{UPDATE_REPO}/releases")


# --------------------------------------------------------------------------- actualizaciones
@router.post("/system/update/check")
def update_check(request: Request):
    user = require(request, "admin")
    st = updates.check_latest(force=True)
    log_action(request, user, "update_check", st.get("latest") or st.get("error"))
    if st["error"]:
        flash(request, st["error"], "error")
    elif st["available"]:
        flash(request, _f("Hay una versión nueva: {0} (instalada {1}).", st['latest'], st['current']))
    else:
        flash(request, _f("OpenNCCM {0} es la versión más reciente.", st['current']))
    return go("/system#actualizaciones")


@router.post("/system/update")
def update_start(request: Request):
    user = require(request, "admin")
    st = updates.status()
    if not st["web_update"]:
        flash(request, _("Esta instalación no tiene el servicio de actualización web (se agrega al actualizar una vez desde la consola): ejecute  sudo opennccm update"), "error")
        return go("/system#actualizaciones")
    if st["running"]:
        return go("/system/updating")
    updates.request_update()
    log_action(request, user, "update_start", f"{st['current']} -> {st['latest'] or '?'}")
    return go("/system/updating")


@router.get("/system/updating")
def update_progress(request: Request):
    user = require(request, "admin")
    return render(request, "updating.html", user, upd=updates.status())


@router.get("/system/update/status")
def update_status_json(request: Request):
    require(request, "admin")
    st = updates.status()
    return JSONResponse({"version": __version__, "running": st["running"], "log": updates.log_tail()},
                        headers={"Cache-Control": "no-store"})


@router.post("/system/update/dismiss")
def update_dismiss(request: Request):
    require(request, "admin")
    request.session["upd_dismissed"] = updates.status()["latest"]
    return Response(status_code=204)


@router.post("/system/time")
async def system_time(request: Request):
    """Zona horaria, sincronización NTP y fecha/hora manual del servidor."""
    from . import systime
    user = require(request, "admin")
    form = await request.form()
    action = form.get("action")
    try:
        if action == "timezone":
            tz = form.get("timezone", "")
            systime.set_timezone(tz)
            log_action(request, user, "system_timezone", tz)
            flash(request, _f("Zona horaria cambiada a {0}. Las tareas programadas ya usan la nueva hora.", tz))
            systime.restart_soon()
            # página de espera: el servicio se reinicia unos segundos para aplicar la zona
            from .main import STARTED
            return render(request, "restarting.html", user, tz=tz, started=STARTED)
        elif action == "ntp":
            on = form.get("ntp") == "on"
            systime.set_ntp(on)
            log_action(request, user, "system_ntp", "activada" if on else "desactivada")
            flash(request, _("Sincronización automática de hora (NTP) activada.") if on
                  else _("Sincronización automática de hora (NTP) desactivada."))
        elif action == "manual":
            systime.set_time(form.get("date", ""), form.get("time", ""))
            log_action(request, user, "system_time", f"{form.get('date')} {form.get('time')}")
            flash(request, _f("Fecha y hora fijadas a {0} {1} (NTP desactivado).", form.get('date'), form.get('time')))
    except systime.TimeError as e:
        flash(request, _f("No se pudo cambiar: {0}", e), "error")
    return go("/system#hora")


@router.post("/system/backup")
async def system_backup(request: Request):
    user = require(request, "admin")
    form = await request.form()
    try:
        include_key = form.get("include_key") == "on"
        if include_key and form.get("passphrase") != form.get("passphrase2"):
            raise ValueError("Las contraseñas de la copia no coinciden.")
        path = sysbackup.create_backup(include_key=include_key, passphrase=form.get("passphrase") or None)
        flash(request, _f("Copia creada: {0}", path.name))
        log_action(request, user, "system_backup", path.name)
    except Exception as e:  # noqa: BLE001
        flash(request, _f("Error creando la copia: {0}", e), "error")
    return go("/system")


@router.get("/system/backups/{name}")
def system_download(request: Request, name: str):
    user = require(request, "admin")
    path = sysbackup.BACKUP_DIR / name
    if not re.fullmatch(sysbackup.NAME_RE, name) or not path.exists():
        return go("/system")
    log_action(request, user, "system_backup_download", name)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.post("/system/backups/{name}/delete")
def system_delete(request: Request, name: str):
    user = require(request, "admin")
    if re.fullmatch(sysbackup.NAME_RE, name):
        (sysbackup.BACKUP_DIR / name).unlink(missing_ok=True)
        log_action(request, user, "system_backup_delete", name)
    return go("/system")


@router.get("/vendors")
def vendors_page(request: Request):
    user = require(request)
    from sqlalchemy import case, func

    from .db import ConfigVersion
    from .drivers import PROFILES
    rows = sorted(PROFILES, key=lambda p: (p.vendor.lower(), p.label))
    # "Comprobado en su red": equipos de la plataforma con al menos un respaldo correcto
    verified: dict[str, dict] = {}
    with SessionLocal() as db:
        q = (select(Device.device_type, func.count(func.distinct(Device.id)), func.min(ConfigVersion.created_at),
                    func.sum(case((Device.custom_backup_cmd.isnot(None), 1), else_=0)))
             .join(ConfigVersion, ConfigVersion.device_id == Device.id)
             .where(ConfigVersion.kind == "running")
             .group_by(Device.device_type))
        for dtype, n, first, custom in db.execute(q):
            verified[dtype] = {"devices": n, "first": first, "custom": bool(custom)}
    vendors = sorted({p.vendor for p in PROFILES}, key=str.lower)
    return render(request, "vendors.html", user, rows=rows, upgrade=firmware.supports_upgrade,
                  get_profile=get_profile, verified=verified, vendors=vendors)
