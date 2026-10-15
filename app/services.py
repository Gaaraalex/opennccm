"""Lógica de negocio: respaldos, cambios, cumplimiento, reversión, alertas y CVE."""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import smtplib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.message import EmailMessage

import requests
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import delete, select

from .config import NVD_API_KEY, WORKERS
from .crypto import decrypt, encrypt
from .db import (ChangeRequest, ComplianceResult, ConfigVersion, Device, Event, Policy, SessionLocal,
                 Vulnerability, get_setting, now)
from .drivers import clean_config, get_profile, parse_inventory, telnet_supported
from .rollback import build_rollback

log = logging.getLogger("opennccm")
pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="ncm")  # trabajo por equipo
jobs = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ncm-job")   # orquestación (usa `pool`)
_device_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()

SEV_ORDER = {"info": 0, "warning": 1, "minor": 1, "major": 2, "critical": 3}


def device_lock(device_id: int) -> threading.Lock:
    with _locks_guard:
        return _device_locks.setdefault(device_id, threading.Lock())


# --------------------------------------------------------------------------- conexión
def connect(dev: Device):
    from netmiko import ConnectHandler

    prof = get_profile(dev.device_type)
    cred = dev.credential
    if cred is None:
        raise RuntimeError("El dispositivo no tiene credencial asignada")
    device_type = dev.device_type
    if dev.protocol == "telnet":
        if not telnet_supported(dev.device_type):
            raise RuntimeError(f"Telnet no soportado para {prof.label}; use SSH")
        device_type = f"{dev.device_type}_telnet"
    secret = decrypt(cred.secret_enc)
    conn = ConnectHandler(
        device_type=device_type, host=dev.host, port=dev.port, username=cred.username,
        password=decrypt(cred.password_enc), secret=secret, conn_timeout=20, timeout=60,
        auth_timeout=30, banner_timeout=30, fast_cli=False,
    )
    if secret:
        try:
            if not conn.check_enable_mode():
                conn.enable()
        except Exception:  # noqa: BLE001 - no todos los drivers tienen modo enable
            pass
    return conn


# --------------------------------------------------------------------------- eventos y alertas
def add_event(severity: str, category: str, message: str, device_id: int | None = None, notify=True):
    with SessionLocal() as db:
        db.add(Event(severity=severity, category=category, message=message, device_id=device_id))
        db.commit()
    if notify:
        minimum = get_setting("alert_min_severity", "warning")
        if SEV_ORDER.get(severity, 0) >= SEV_ORDER.get(minimum, 1):
            pool.submit(send_alert, f"[OpenNCCM][{severity.upper()}] {category}", message)


def send_alert(subject: str, body: str, attachments: list[tuple[str, bytes]] | None = None,
               email_only: bool = False) -> list[str]:
    errors = []
    to = [a.strip() for a in get_setting("alert_emails").split(",") if a.strip()]
    host = get_setting("smtp_host")
    if email_only and not (to and host):
        return ["Configure el servidor SMTP y los destinatarios en Ajustes"]
    if to and host:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = get_setting("smtp_from", "opennccm@localhost")
            msg["To"] = ", ".join(to)
            msg.set_content(body)
            for name, data in attachments or []:
                msg.add_attachment(data, maintype="text", subtype="csv", filename=name)
            port = int(get_setting("smtp_port", "587") or 587)
            security = get_setting("smtp_security", "starttls")
            cls = smtplib.SMTP_SSL if security == "ssl" else smtplib.SMTP
            with cls(host, port, timeout=20) as s:
                if security == "starttls":
                    s.starttls()
                user = get_setting("smtp_user")
                if user:
                    s.login(user, get_setting("smtp_password"))
                s.send_message(msg)
        except Exception as e:  # noqa: BLE001
            errors.append(f"SMTP: {e}")
            log.warning("Fallo al enviar correo: %s", e)
    if email_only:
        return errors
    hook = get_setting("webhook_url")
    if hook:
        try:
            # Compatible con Slack, Mattermost, Rocket.Chat, Google Chat y Teams (incoming webhook)
            requests.post(hook, json={"text": f"*{subject}*\n{body}"}, timeout=15)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Webhook: {e}")
    tg_token, tg_chat = get_setting("telegram_token"), get_setting("telegram_chat_id")
    if tg_token and tg_chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                          json={"chat_id": tg_chat, "text": f"{subject}\n{body}"[:4000]}, timeout=15)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Telegram: {e}")
    return errors


# --------------------------------------------------------------------------- diffs
def diff_lines(a: str, b: str, a_name="anterior", b_name="actual", context=3) -> list[str]:
    return list(difflib.unified_diff(a.splitlines(), b.splitlines(), a_name, b_name, lineterm="",
                                     n=context))


def side_by_side(a: str, b: str, context: int = 3) -> list[tuple]:
    """Filas (tipo, nº izq, texto izq, nº der, texto der). tipo: e=igual c=cambio d=borrado a=agregado s=salto."""
    la, lb = a.splitlines(), b.splitlines()
    rows: list[tuple] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if n > 2 * context:
                for k in range(context):
                    rows.append(("e", i1 + k + 1, la[i1 + k], j1 + k + 1, lb[j1 + k]))
                rows.append(("s", None, f"… {n - 2 * context} líneas iguales …", None, ""))
                for k in range(n - context, n):
                    rows.append(("e", i1 + k + 1, la[i1 + k], j1 + k + 1, lb[j1 + k]))
            else:
                for k in range(n):
                    rows.append(("e", i1 + k + 1, la[i1 + k], j1 + k + 1, lb[j1 + k]))
        else:
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                left = i1 + k < i2
                right = j1 + k < j2
                kind = "c" if left and right else ("d" if left else "a")
                rows.append((kind, i1 + k + 1 if left else None, la[i1 + k] if left else "",
                             j1 + k + 1 if right else None, lb[j1 + k] if right else ""))
    return _trim_edges(rows, context)  # recorta los bloques iguales del inicio y del final


def _trim_edges(rows: list[tuple], context: int) -> list[tuple]:
    changed = [i for i, r in enumerate(rows) if r[0] in "cda"]
    if not changed:
        return []
    start, end = max(0, changed[0] - context), min(len(rows), changed[-1] + context + 1)
    out = rows[start:end]
    if start > 0:
        out.insert(0, ("s", None, f"… {start} líneas …", None, ""))
    if end < len(rows):
        out.append(("s", None, f"… {len(rows) - end} líneas …", None, ""))
    return out


def diff_stats(a: str, b: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(a.splitlines(), b.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def latest_version(db, device_id: int, kind="running") -> ConfigVersion | None:
    return db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == device_id,
                                                  ConfigVersion.kind == kind)
                      .order_by(ConfigVersion.id.desc()).limit(1)).first()


def _normalize_for_compare(text: str) -> list[str]:
    return [l.rstrip() for l in text.splitlines()
            if l.strip() and not re.match(r"^\s*(!|#|Building|Current configuration|Using \d+ out of)", l)]


# --------------------------------------------------------------------------- respaldo
def backup_device(device_id: int, trigger="manual", username: str | None = None) -> str:
    lock = device_lock(device_id)
    if not lock.acquire(timeout=300):
        return "busy"
    try:
        return _backup_device(device_id, trigger, username)
    finally:
        if trigger == "push":
            end_maintenance(device_id)
        lock.release()


def end_maintenance(device_id: int):
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        if dev and dev.maintenance_until:
            dev.maintenance_until = None
            db.commit()


def _backup_device(device_id: int, trigger: str, username: str | None) -> str:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        if dev is None:
            return "missing"
        prof = get_profile(dev.device_type)
        try:
            with connect(dev) as conn:
                running = conn.send_command(dev.custom_backup_cmd or prof.backup, read_timeout=180)
                startup = conn.send_command(prof.startup, read_timeout=180) if prof.startup else None
                inventory = conn.send_command(prof.inventory, read_timeout=60) if prof.inventory else None
        except Exception as e:  # noqa: BLE001
            dev.last_status, dev.last_error = "error", str(e)[:2000]
            db.commit()
            add_event("major", "Respaldo fallido", f"{dev.name} ({dev.host}): {e}", dev.id)
            return "error"

        running = clean_config(running, prof)
        if len(running.strip()) < 10 or re.search(r"(?i)^\s*(% ?Invalid|Unknown command|syntax error)",
                                                    running):
            dev.last_status, dev.last_error = "error", "Salida de configuración vacía o inválida"
            db.commit()
            add_event("major", "Respaldo fallido", f"{dev.name}: salida inválida:\n{running[:300]}", dev.id)
            return "error"

        sha = hashlib.sha256(running.encode()).hexdigest()
        prev = latest_version(db, dev.id)
        changed = prev is None or prev.sha256 != sha
        result = "unchanged"
        prev_text = decrypt(prev.content_enc) if prev else ""
        if changed:
            added, removed = diff_stats(prev_text, running) if prev else (len(running.splitlines()), 0)
            db.add(ConfigVersion(device_id=dev.id, kind="running", content_enc=encrypt(running), sha256=sha,
                                 size=len(running), added=added, removed=removed, trigger=trigger,
                                 username=username))
            result = "changed" if prev else "first"

        if startup is not None:
            startup = clean_config(startup, prof)
            ssha = hashlib.sha256(startup.encode()).hexdigest()
            sprev = latest_version(db, dev.id, "startup")
            if sprev is None or sprev.sha256 != ssha:
                db.add(ConfigVersion(device_id=dev.id, kind="startup", content_enc=encrypt(startup),
                                     sha256=ssha, size=len(startup), trigger=trigger, username=username))
            conflict = _normalize_for_compare(startup) != _normalize_for_compare(running)
            new_conflict = conflict and not dev.startup_conflict
            dev.startup_conflict = conflict
        else:
            new_conflict = False

        if inventory:
            inv = parse_inventory(inventory, prof)
            dev.os_version = inv["os_version"] or dev.os_version
            dev.model = inv["model"] or dev.model
            dev.serial = inv["serial"] or dev.serial
            dev.inventory_raw_enc = encrypt(inventory)

        new_drift = False
        if dev.baseline_version_id:
            base = db.get(ConfigVersion, dev.baseline_version_id)
            drift = base is not None and base.sha256 != sha
            new_drift = drift and not dev.baseline_drift
            dev.baseline_drift = drift

        dev.last_status, dev.last_error, dev.last_backup_at = "ok", None, now()
        db.commit()
        if new_drift:
            add_event("warning", "Desviación de línea base",
                      f"{dev.name}: la configuración actual ya no coincide con la línea base.", dev.id)
        # los eventos se registran después del commit para no competir por el lock de SQLite
        if new_conflict:
            add_event("warning", "Conflicto arranque/ejecución",
                      f"{dev.name}: la configuración en ejecución no está guardada en startup.", dev.id)

        if result == "changed":
            diff = "\n".join(diff_lines(prev_text, running)[:200])
            # releer las opciones: pueden haber cambiado mientras se conectaba al equipo
            db.refresh(dev, ["auto_rollback", "maintenance_until"])
            in_maintenance = dev.maintenance_until and dev.maintenance_until > now()
            sev = "info" if trigger == "push" else "warning"
            add_event(sev, "Cambio de configuración",
                      f"{dev.name} ({dev.host}) cambió [{trigger}]. +{added}/-{removed} líneas\n\n{diff}", dev.id)
            if dev.auto_rollback and trigger in ("syslog", "scheduled") and not in_maintenance:
                # se ejecuta en otro hilo: este todavía tiene el lock del dispositivo
                jobs.submit(_auto_rollback, dev.id, dev.name, prof.family, running, prev_text)

    run_compliance(device_id)
    return result


def _auto_rollback(dev_id: int, dev_name: str, family: str, current: str, target: str):
    cmds = build_rollback(current, target, family)
    if not cmds:
        return
    try:
        out = push_commands(dev_id, cmds)
        add_event("critical", "Reversión automática",
                  f"{dev_name}: cambio no autorizado revertido ({len(cmds)} comandos).\n{out[-1500:]}", dev_id)
        backup_device(dev_id, "push", "auto-rollback")
    except Exception as e:  # noqa: BLE001
        add_event("critical", "Reversión automática fallida", f"{dev_name}: {e}", dev_id)


def backup_many(device_ids: list[int], trigger="scheduled", username=None) -> dict:
    results = list(pool.map(lambda i: backup_device(i, trigger, username), device_ids))
    summary: dict[str, int] = {}
    for r in results:
        summary[r] = summary.get(r, 0) + 1
    return summary


def device_ids(group_id: int | None = None, only_enabled=True) -> list[int]:
    with SessionLocal() as db:
        q = select(Device.id)
        if only_enabled:
            q = q.where(Device.enabled.is_(True))
        if group_id:
            q = q.where(Device.group_id == group_id)
        return list(db.scalars(q))


# --------------------------------------------------------------------------- cambios
def push_commands(device_id: int, commands: list[str], exec_mode=False) -> str:
    if exec_mode:
        return _push(device_id, commands, True)
    try:
        return _push(device_id, commands, False)
    except Exception:
        end_maintenance(device_id)  # si falló no habrá respaldo post-cambio que cierre la ventana
        raise


def _push(device_id: int, commands: list[str], exec_mode: bool) -> str:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        prof = get_profile(dev.device_type)
        if not exec_mode:
            # ventana máxima por si el proceso muere; normalmente se cierra al terminar el respaldo post-cambio
            dev.maintenance_until = now() + timedelta(minutes=5)
            db.commit()
        with connect(dev) as conn:
            if exec_mode:
                out = []
                for c in commands:
                    out.append(f"### {c}\n" + conn.send_command(c, read_timeout=180))
                return "\n".join(out)
            out = conn.send_config_set(commands, read_timeout=180, exit_config_mode=not prof.commit,
                                       cmd_verify=False)
            if re.search(r"(?im)^\s*(% ?Invalid input|% ?Incomplete command|syntax error|Error:)", out):
                raise RuntimeError("El equipo rechazó comandos:\n" + out[-3000:])
            if prof.commit:
                out += "\n" + conn.commit()
                try:
                    conn.exit_config_mode()
                except Exception:  # noqa: BLE001
                    pass
            if prof.save:
                try:
                    out += "\n" + conn.save_config()
                except (NotImplementedError, AttributeError):
                    pass
            return out


_jinja = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)


def render_commands(body: str, dev: Device, variables: dict) -> list[str]:
    text = _jinja.from_string(body).render(
        device={"name": dev.name, "host": dev.host, "model": dev.model, "os_version": dev.os_version,
                "serial": dev.serial, "location": dev.location, "device_type": dev.device_type,
                "group": dev.group.name if dev.group else ""},
        **variables)
    return [l for l in text.splitlines() if l.strip()]


def execute_change_request(cr_id: int):
    with SessionLocal() as db:
        cr = db.get(ChangeRequest, cr_id)
        if cr.status not in ("approved",):
            return
        cr.status = "running"
        db.commit()
        if cr.kind == "firmware":
            from .firmware import execute_firmware_request
            return execute_firmware_request(db, cr)
        variables = json.loads(cr.variables or "{}")
        ids = json.loads(cr.device_ids)
        devices = list(db.scalars(select(Device).where(Device.id.in_(ids))))
        jobs = {}
        for dev in devices:
            try:
                cmds = render_commands(cr.body, dev, variables)
            except Exception as e:  # noqa: BLE001
                jobs[dev.name] = e
                continue
            jobs[dev.name] = (dev.id, pool.submit(push_commands, dev.id, cmds))
        report, ok, fail = [], 0, 0
        for name, job in jobs.items():
            if isinstance(job, Exception):
                fail += 1
                report.append(f"===== {name}: ERROR de plantilla: {job}")
                continue
            dev_id, fut = job
            try:
                out = fut.result()
                ok += 1
                report.append(f"===== {name}: OK\n{out}")
                pool.submit(backup_device, dev_id, "push", cr.requested_by)
            except Exception as e:  # noqa: BLE001
                fail += 1
                report.append(f"===== {name}: ERROR\n{e}")
        cr.status = "done" if fail == 0 else ("failed" if ok == 0 else "partial")
        cr.result = "\n\n".join(report)
        cr.executed_at = now()
        db.commit()
        sev = "info" if fail == 0 else "major"
        add_event(sev, "Solicitud de cambio ejecutada",
                  f"#{cr.id} '{cr.title}': {ok} OK, {fail} con error.")


def rollback_commands(device_id: int, version_id: int) -> list[str]:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        target = db.get(ConfigVersion, version_id)
        current = latest_version(db, device_id)
        if not target or not current:
            return []
        return build_rollback(decrypt(current.content_enc), decrypt(target.content_enc),
                              get_profile(dev.device_type).family)


# --------------------------------------------------------------------------- cumplimiento
def _policy_applies(policy: Policy, dev: Device) -> bool:
    if not policy.enabled:
        return False
    if policy.group_id and policy.group_id != dev.group_id:
        return False
    if policy.device_type_filter and not re.search(policy.device_type_filter, dev.device_type):
        return False
    return True


def run_compliance(device_id: int) -> tuple[int, int]:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        ver = latest_version(db, device_id)
        if not dev or not ver:
            return 0, 0
        text = decrypt(ver.content_enc)
        db.execute(delete(ComplianceResult).where(ComplianceResult.device_id == device_id))
        passed = failed = 0
        new_failures = []
        for policy in db.scalars(select(Policy)):
            if not _policy_applies(policy, dev):
                continue
            for rule in policy.rules:
                try:
                    found = re.search(rule.pattern, text, re.MULTILINE) is not None
                except re.error:
                    found = False
                ok = found if rule.kind == "must_contain" else not found
                db.add(ComplianceResult(device_id=dev.id, policy_id=policy.id, rule_id=rule.id, passed=ok))
                if ok:
                    passed += 1
                else:
                    failed += 1
                    new_failures.append(f"[{rule.severity}] {policy.name} / {rule.name}")
        db.commit()
    if new_failures:
        add_event("major" if any("[critical]" in f for f in new_failures) else "warning",
                  "Infracción de cumplimiento", f"{dev.name}:\n" + "\n".join(new_failures), device_id,
                  notify=get_setting("notify_compliance", "1") == "1")
    return passed, failed


# --------------------------------------------------------------------------- vulnerabilidades (NVD)
def _cpe_escape(v: str) -> str:
    return re.sub(r"([()\\!\"#$%&'*+,/:;<=>?@\[\]^`{|}~])", r"\\\1", v.lower())


def scan_cves(device_id: int) -> int:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        prof = get_profile(dev.device_type)
        if not dev.os_version:
            return -1
        params = {"resultsPerPage": 200}
        if prof.cpe:
            params["virtualMatchString"] = f"{prof.cpe}:{_cpe_escape(dev.os_version)}"
        else:
            params["keywordSearch"] = f"{prof.vendor} {dev.os_version}"
        headers = {"apiKey": NVD_API_KEY} if NVD_API_KEY else {}
        r = requests.get("https://services.nvd.nist.gov/rest/json/cves/2.0", params=params,
                         headers=headers, timeout=60)
        r.raise_for_status()
        data = r.json()
        db.execute(delete(Vulnerability).where(Vulnerability.device_id == device_id))
        count = 0
        for item in data.get("vulnerabilities", []):
            cve = item["cve"]
            score = severity = None
            metrics = cve.get("metrics", {})
            for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if metrics.get(key):
                    m = metrics[key][0]
                    score = m["cvssData"].get("baseScore")
                    severity = m["cvssData"].get("baseSeverity") or m.get("baseSeverity")
                    break
            desc = next((d["value"] for d in cve.get("descriptions", []) if d["lang"] == "es"),
                        next((d["value"] for d in cve.get("descriptions", [])), ""))
            db.add(Vulnerability(device_id=device_id, cve_id=cve["id"], score=score, severity=severity,
                                 description=desc[:2000],
                                 url=f"https://nvd.nist.gov/vuln/detail/{cve['id']}"))
            count += 1
        db.commit()
        crit = db.scalars(select(Vulnerability).where(Vulnerability.device_id == device_id,
                                                      Vulnerability.severity == "CRITICAL")).all()
        if crit:
            add_event("critical", "Vulnerabilidad de firmware",
                      f"{dev.name} ({dev.os_version}) tiene {len(crit)} CVE críticos: "
                      + ", ".join(v.cve_id for v in crit[:10]), device_id)
        return count


def scan_cves_many(ids: list[int]):
    delay = 0.7 if NVD_API_KEY else 6.5  # límite de la API pública NVD
    for i in ids:
        try:
            scan_cves(i)
        except Exception as e:  # noqa: BLE001
            log.warning("CVE scan falló para %s: %s", i, e)
        time.sleep(delay)
