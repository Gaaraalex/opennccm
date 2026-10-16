"""Tareas programadas (cron) y receptor syslog para detección de cambios en tiempo real."""
from __future__ import annotations

import json
import logging
import re
import socket
import socketserver
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import cronutil
from sqlalchemy import select

from . import services
from .config import SYSLOG_ENABLED, SYSLOG_PORT
from .db import ChangeRequest, Configlet, ConfigVersion, Device, Schedule, SessionLocal, get_setting, now

log = logging.getLogger("opennccm")
scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})


def run_schedule(schedule_id: int):
    with SessionLocal() as db:
        s = db.get(Schedule, schedule_id)
        if not s or not s.enabled:
            return
        s.last_run = now()
        db.commit()
        action, group_id, configlet_id, name = s.action, s.group_id, s.configlet_id, s.name
    ids = services.device_ids(group_id)
    log.info("Ejecutando tarea '%s' (%s) sobre %d equipos", name, action, len(ids))
    if action == "backup":
        summary = services.backup_many(ids, "scheduled", "scheduler")
        services.add_event("info", "Respaldo programado", f"'{name}': {summary}", notify=False)
    elif action == "compliance":
        for i in ids:
            services.run_compliance(i)
    elif action == "cve":
        services.scan_cves_many(ids)
    elif action == "system_backup":
        from .sysbackup import create_backup
        try:
            path = create_backup()
            services.add_event("info", "Copia de recuperación", f"Creada {path.name}", notify=False)
        except Exception as e:  # noqa: BLE001
            services.add_event("critical", "Copia de recuperación fallida", str(e))
    elif action == "report_email":
        from .reports import email_reports
        errors = email_reports(["inventory", "changes", "compliance", "conflicts", "baseline", "eol"])
        if errors:
            services.add_event("major", "Informe programado no enviado", "\n".join(errors), notify=False)
    elif action == "configlet" and configlet_id:
        with SessionLocal() as db:
            c = db.get(Configlet, configlet_id)
            if c is None:
                return
            cr = ChangeRequest(title=f"[Programada] {name}: {c.name}", body=c.body, variables="{}",
                               device_ids=json.dumps(ids), requested_by="scheduler", reviewed_by="scheduler",
                               status="approved")
            if c.kind == "exec":
                for i in ids:
                    services.pool.submit(services.push_commands, i, c.body.splitlines(), True)
                return
            db.add(cr)
            db.commit()
            cr_id = cr.id
        services.execute_change_request(cr_id)


def reload_jobs():
    for job in scheduler.get_jobs():
        if job.id.startswith("sched-"):
            job.remove()
    with SessionLocal() as db:
        for s in db.scalars(select(Schedule).where(Schedule.enabled.is_(True))):
            try:
                scheduler.add_job(run_schedule, cronutil.trigger(s.cron), args=[s.id], id=f"sched-{s.id}",
                                  replace_existing=True)
            except ValueError as e:
                log.error("Cron inválido en tarea %s: %s", s.name, e)


# --------------------------------------------------------------------------- syslog
CHANGE_PATTERNS = re.compile("|".join([
    r"%SYS-\d-CONFIG",            # Cisco IOS/XE
    r"%PARSER-\d-CFGLOG",         # Cisco archive log config
    r"CONFIG_I\b",
    r"VSHD-\d-VSHD_SYSLOG_CONFIG", # Nexus
    r"%ASA-\d-11100[5-9]",        # ASA
    r"COMMIT\b.*(?:succeeded|completed|by user)",
    r"UI_COMMIT",                 # Junos
    r"UI_CFG_AUDIT",
    r"CFG(?:MAN)?/\d+/CFG_CHANGE", r"CFG_CHANGE", r"CFGCHANGE",  # Huawei/H3C
    r"logdesc=\"(?:Object|Attribute) (?:configured|attribute configured)",  # FortiGate
    r"logdesc=\"Configuration changed\"",
    r"type=CONFIG",               # Palo Alto
    r"config(?:uration)? (?:changed|saved|modified|updated)",
    r"system,info.* (?:changed|added|removed) by",  # MikroTik
    r"SHELL_CMD_CONFIG", r"SYSTEM-\d-CONFIG",
    r"Startup configuration (?:saved|changed)",
    r"Running Config Change",     # Aruba/HP
]), re.IGNORECASE)

_last_trigger: dict[int, float] = {}
_ip_cache: dict[str, int] = {}
_ip_cache_at = 0.0


def _device_for_ip(ip: str) -> int | None:
    global _ip_cache, _ip_cache_at
    if time.time() - _ip_cache_at > 300:
        cache = {}
        with SessionLocal() as db:
            for d in db.scalars(select(Device).where(Device.enabled.is_(True))):
                cache[d.host] = d.id
                try:
                    cache[socket.gethostbyname(d.host)] = d.id
                except OSError:
                    pass
        _ip_cache, _ip_cache_at = cache, time.time()
    return _ip_cache.get(ip)


def _delayed_backup(device_id: int):
    with SessionLocal() as db:
        d = db.get(Device, device_id)
        if d and d.maintenance_until and d.maintenance_until > now():
            log.info("Syslog de %s durante mantenimiento (hasta %s): se revisa más tarde", d.name, d.maintenance_until)
            # OpenNCCM está aplicando un cambio aprobado: no se descarta el aviso, se reintenta
            # cuando termine (el respaldo post-cambio ya habrá guardado la parte autorizada)
            # se revisa cada minuto: la ventana se cierra antes si termina el respaldo post-cambio
            wait = min((d.maintenance_until - now()).total_seconds() + 5, 60)
            timer = threading.Timer(wait, lambda: services.pool.submit(_delayed_backup, device_id))
            timer.daemon = True
            timer.start()
            return
    services.backup_device(device_id, "syslog", "syslog")


class SyslogHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request[0].decode("utf-8", errors="replace")
        if not CHANGE_PATTERNS.search(data):
            return
        dev_id = _device_for_ip(self.client_address[0])
        if dev_id is None:
            return
        last = _last_trigger.get(dev_id, 0)
        if time.time() - last < 60:
            return
        _last_trigger[dev_id] = time.time()
        services.add_event("info", "Syslog de cambio", data.strip()[:500], dev_id, notify=False)
        # espera 20 s para que el usuario termine de escribir antes de respaldar
        timer = threading.Timer(20, lambda: services.pool.submit(_delayed_backup, dev_id))
        timer.daemon = True
        timer.start()


def start_syslog():
    if not SYSLOG_ENABLED:
        return
    try:
        server = socketserver.ThreadingUDPServer(("0.0.0.0", SYSLOG_PORT), SyslogHandler)
    except OSError as e:
        log.error("No se pudo abrir syslog UDP/%s: %s", SYSLOG_PORT, e)
        return
    threading.Thread(target=server.serve_forever, daemon=True, name="syslog").start()
    log.info("Receptor syslog escuchando en UDP/%s", SYSLOG_PORT)


def purge_old_versions():
    """Conserva las N versiones más recientes por equipo y tipo (las etiquetadas nunca se borran)."""
    keep = int(get_setting("retention_versions", "0") or 0)
    if keep <= 0:
        return
    with SessionLocal() as db:
        for dev_id in db.scalars(select(Device.id)):
            for kind in ("running", "startup"):
                old = db.scalars(select(ConfigVersion).where(
                    ConfigVersion.device_id == dev_id, ConfigVersion.kind == kind, ConfigVersion.label.is_(None))
                    .order_by(ConfigVersion.id.desc()).offset(keep)).all()
                for v in old:
                    db.delete(v)
        db.commit()


def start():
    scheduler.start()
    reload_jobs()
    scheduler.add_job(purge_old_versions, CronTrigger(hour=3, minute=30), id="purge", replace_existing=True)
    start_syslog()
