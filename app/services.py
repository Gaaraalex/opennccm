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
from .drivers import FASTPATH, IOS, clean_config, compare_text, get_profile, parse_inventory, telnet_supported
from .rollback import build_rollback, fastpath_style
from .i18n import N_

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

    from app import legacy_ssh

    legacy_ssh.enable()  # equipos antiguos que solo ofrecen ssh-rsa / kex SHA-1 (p. ej. Ubiquiti EdgeSwitch)

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
    password = decrypt(cred.password_enc)
    params = dict(device_type=device_type, host=dev.host, port=dev.port, username=cred.username,
                  password=password, conn_timeout=20, timeout=60, auth_timeout=30, banner_timeout=30,
                  fast_cli=False)
    try:
        conn = ConnectHandler(secret=secret, **params)
    except ValueError as e:
        # Algunos drivers (p. ej. Ubiquiti EdgeSwitch) entran a «enable» al conectar y fallan si el equipo
        # pide una contraseña que la credencial no tiene.
        if "enable mode" not in str(e):
            raise
        if secret:
            raise RuntimeError(f"El equipo no aceptó la contraseña de enable de la credencial «{cred.name}». "
                               "Corríjala en Credenciales → Enable / secret.") from e
        try:  # muchos equipos usan la misma contraseña para ingresar y para enable
            conn = ConnectHandler(secret=password, **params)
        except ValueError as e2:
            if "enable mode" not in str(e2):
                raise
            raise RuntimeError(
                "El equipo pide contraseña para el modo privilegiado (enable) y no se pudo entrar ni sin "
                f"contraseña ni con la de ingreso. Escriba la contraseña de enable en la credencial «{cred.name}» "
                "(Credenciales → Enable / secret).") from e2
        log.info("%s: enable aceptó la contraseña de ingreso (la credencial no tiene contraseña de enable)",
                 dev.name)
    if prof.family not in (IOS, FASTPATH):  # Juniper, Huawei, MikroTik, Linux...: no tienen modo «enable»
        return conn
    try:
        privileged = conn.check_enable_mode()
    except Exception:  # noqa: BLE001 - driver sin modo enable
        privileged = True
    if privileged:
        return conn
    # Ingresó en modo usuario («>»): sin modo privilegiado no se puede leer la configuración.
    # Con la credencial sin enable se prueba la contraseña de ingreso (muy habitual).
    conn.secret = secret or password
    try:
        conn.enable()
        privileged = conn.check_enable_mode()
    except Exception:  # noqa: BLE001
        privileged = False
    if not privileged:
        conn.disconnect()
        if secret:
            raise RuntimeError(f"El equipo no aceptó la contraseña de enable de la credencial «{cred.name}». "
                               "Corríjala en Credenciales → Enable / secret.")
        raise RuntimeError(
            "El equipo entró en modo usuario («>») y pide contraseña para el modo privilegiado (enable); no se pudo "
            f"entrar con la contraseña de ingreso. Escriba la contraseña de enable en la credencial «{cred.name}» "
            "(Credenciales → Enable / secret).")
    if not secret:
        log.info("%s: enable aceptó la contraseña de ingreso (la credencial no tiene contraseña de enable)", dev.name)
    return conn


# --------------------------------------------------------------------------- eventos y alertas
# Categorías de eventos: se guardan en español en la base de datos y se traducen al mostrarlas
EVENT_CATEGORIES = [
    N_("Actualización de firmware"), N_("Cambio de configuración"), N_("Conflicto arranque/ejecución"),
    N_("Copia de recuperación"), N_("Copia de recuperación fallida"), N_("Cuenta bloqueada"),
    N_("Desviación de línea base"), N_("Infracción de cumplimiento"), N_("Informe programado no enviado"),
    N_("Respaldo fallido"), N_("Respaldo programado"), N_("Reversión automática"), N_("Reversión automática fallida"),
    N_("Solicitud de cambio ejecutada"), N_("Solicitud de cambio pendiente"), N_("Syslog de cambio"),
    N_("Vulnerabilidad de firmware"),
]


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


# última línea con la que siempre termina «show running-config» en estas plataformas
END_MARK = {"cisco_ios": "end", "cisco_xe": "end", "arista_eos": "end", "cisco_asa": ": end"}


def _normalize_for_compare(text: str) -> list[str]:
    return [l.rstrip() for l in compare_text(text).splitlines()
            if l.strip() and not re.match(r"^\s*(!|#|Building|Current configuration|Using \d+ out of)", l)]


# --------------------------------------------------------------------------- respaldo
def backup_device(device_id: int, trigger="manual", username: str | None = None) -> str:
    lock = device_lock(device_id)
    if not lock.acquire(timeout=300):
        log.warning("Respaldo del equipo %s [%s]: ocupado, se omite", device_id, trigger)
        if trigger == "push":
            end_maintenance(device_id)  # no dejar la ventana de mantenimiento abierta
        return "busy"
    try:
        result = _backup_device(device_id, trigger, username)
        log.info("Respaldo del equipo %s [%s]: %s", device_id, trigger, result)
        return result
    finally:
        if trigger == "push":
            end_maintenance(device_id)
        lock.release()


def refresh_inventory_from_raw() -> int:
    """Relee la última salida de inventario guardada con el lector actual.

    Se ejecuta al iniciar: tras una actualización que mejora el lector, el modelo, la serie y el firmware se
    corrigen sin esperar al próximo respaldo. Devuelve cuántos equipos cambiaron."""
    changed = 0
    with SessionLocal() as db:
        for dev in db.scalars(select(Device).where(Device.inventory_raw_enc.is_not(None))):
            try:
                inv = parse_inventory(decrypt(dev.inventory_raw_enc), get_profile(dev.device_type))
            except Exception:  # noqa: BLE001 - un inventario ilegible no debe impedir el arranque
                continue
            new = {}
            for field, key in (("os_version", "os_version"), ("model", "model"), ("serial", "serial")):
                old = getattr(dev, field)
                if old and not re.search(r"[A-Za-z0-9]", old):  # valores basura previos («.......»)
                    old = None
                new[field] = inv[key] or old
            if any(getattr(dev, f) != v for f, v in new.items()):
                for f, v in new.items():
                    setattr(dev, f, v)
                changed += 1
        db.commit()
    return changed


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
                inventory = "\n\n".join(conn.send_command(c, read_timeout=60) for c in prof.inventory.split("\n")
                                        if c.strip()) if prof.inventory else None
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
        # salida cortada: paginación activa («--More--»), configuración estilo FastPath sin el «exit» final o
        # plataformas que siempre terminan en «end» y esta vez no
        last = running.rstrip().splitlines()[-1].strip()
        incomplete = re.search(r"(?i)--\s*more\s*--|\(q\)uit|press any key", running) or (
            re.search(r"(?m)^configure$", running) and fastpath_style(running) and last != "exit") or (
            dev.device_type in END_MARK and not dev.custom_backup_cmd and last != END_MARK[dev.device_type])
        if incomplete:
            dev.last_status = "error"
            dev.last_error = "La configuración llegó incompleta (cortada o paginada); no se guardó esta versión"
            db.commit()
            add_event("major", "Respaldo fallido", f"{dev.name}: configuración incompleta, no se guardó:\n"
                      f"{running[-300:]}", dev.id)
            return "error"

        sha = hashlib.sha256(compare_text(running).encode()).hexdigest()
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
            ssha = hashlib.sha256(compare_text(startup).encode()).hexdigest()
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
            log.info("Cambio en %s [%s]: +%s/-%s; reversión automática=%s, mantenimiento=%s",
                     dev.name, trigger, added, removed, dev.auto_rollback, bool(in_maintenance))
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
    log.info("Reversión automática de %s: %d comandos", dev_name, len(cmds))
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


# Respuestas con las que los equipos rechazan un comando, por fabricante:
#   Cisco/Arista/Dell/Allied/Extreme («% Invalid input», «%% Invalid»), HP Comware/Mellanox/A10 («% Unrecognized»),
#   Huawei («Error: Unrecognized command»), Juniper («syntax error», «unknown command», «error: ...»),
#   MikroTik («bad command name», «expected end of command», «failure: ...», «input does not match»),
#   FortiGate («Command fail. Return code», «Unknown action», «entry not found», «value parse error»),
#   Palo Alto («Invalid syntax», «Server error»), Nokia SR OS («MINOR:/MAJOR:»), Cisco IOS-XR («!!% ...»),
#   VyOS/EdgeRouter («Set failed», «Invalid command»), Check Point («CLINFR0329 Invalid command»),
#   Broadcom FastPath/EdgeSwitch/Netgear/Dell N («Failed to ...», «An invalid ...»), HP ProCurve («Invalid input:»).
DEVICE_ERROR = re.compile(
    r"(?im)^\s*(%+\s*(Invalid|Incomplete|Ambiguous|Unrecognized|Unknown|Error|Bad|Wrong|Failed|Cannot)|!!%|"
    r"syntax error|unknown command|error:|Error\b|Failed to |An invalid |Invalid (input|command|syntax|value)|"
    r"Incomplete command|Unrecognized command|Command not found|Command fail|Unknown action|bad command name|"
    r"expected end of command|failure:|input does not match|entry not found|value parse error|Server error|"
    r"(MINOR|MAJOR|CRITICAL): |CLINFR\d+|(Set|Delete|Commit) failed|Cannot |Unable to |Incorrect usage)")
# fin de la respuesta a un comando: el prompt del equipo (# > $ ]) o una pregunta de confirmación
PROMPT_END = r"([#>$\]]\s*$|\[confirm\]\s*$|[\[(](y/n|yes/no|Y/N)[\])]\s*[?:]?\s*$)"
CONFIRM = re.compile(r"(\[confirm\]|[\[(](y/n|yes/no|Y/N)[\])])\s*[?:]?\s*$")
# cómo descartar cambios sin confirmar en plataformas con «commit» si algo falla a mitad
DISCARD = {"juniper_junos": "rollback 0", "juniper": "rollback 0", "cisco_xr": "abort", "cisco_viptela": "abort",
           "vyos": "discard", "ubiquiti_edgerouter": "discard", "huawei_vrpv8": "abort", "nokia_srl": "discard now",
           "paloalto_panos": "revert config"}


def _push_stepwise(conn, commands: list[str], prof) -> str:
    """Envía los comandos de uno en uno y se detiene en el primero que el equipo rechace.

    Se lee la respuesta de cada comando hasta el prompt, así se sabe exactamente cuál falló, no se envían los
    siguientes y no se guarda (write memory / commit). En plataformas con commit se descartan los cambios
    pendientes para no dejarlos a medias en la configuración candidata."""
    out = conn.config_mode()
    for i, cmd in enumerate(commands):
        resp = conn.send_command(cmd, expect_string=PROMPT_END, read_timeout=60, strip_prompt=False,
                                 strip_command=False, cmd_verify=False)
        out += f"\n{resp}"
        problem = None
        if DEVICE_ERROR.search(resp):
            problem = "rechazó"
        elif CONFIRM.search(resp):
            problem = "pidió una confirmación para"
        if problem:
            done = "\n".join(commands[:i]) or "(ninguno)"
            note = "NO se guardó la configuración (write memory)."
            if prof.commit:
                discard = DISCARD.get(prof.key)
                try:
                    if discard:
                        conn.send_command(discard, expect_string=PROMPT_END, read_timeout=60, cmd_verify=False)
                    note = ("NO se hizo commit" + (f" y se descartaron los cambios pendientes («{discard}»)."
                                                   if discard else "; revise la configuración candidata del equipo."))
                except Exception:  # noqa: BLE001
                    note = "NO se hizo commit; revise la configuración candidata del equipo."
            raise RuntimeError(
                f"El equipo {problem} el comando «{cmd}» (n.º {i + 1} de {len(commands)}):\n{resp.strip()}\n\n"
                f"Se detuvo ahí y {note} Comandos ya aplicados:\n{done}")
    if not prof.commit:
        try:
            out += conn.exit_config_mode()
        except Exception:  # noqa: BLE001 - algunos comandos dejan el equipo fuera del modo configuración
            pass
    return out


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
            out = _push_stepwise(conn, commands, prof)
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
                # parte de los comandos pudo aplicarse: respaldar para que la versión refleje el estado real
                pool.submit(backup_device, dev_id, "push", cr.requested_by)
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


# --------------------------------------------------------------------------- resumen de un diff en palabras
_SUMMARY_SKIP = re.compile(r"^\s*(!|#|$)")


def _sections(text: str) -> dict[str, str]:
    """Línea -> sección (la línea sin sangría anterior), para decir «en interface Gi0/1: …»."""
    sec, out = "", {}
    for ln in text.replace("\r\n", "\n").split("\n"):
        if _SUMMARY_SKIP.match(ln):
            continue
        if not ln.startswith((" ", "\t")):
            sec = ln.strip()
            out.setdefault(ln.strip(), "")
        else:
            out.setdefault(ln.strip(), sec)
    return out


def diff_summary(old: str, new: str, limit: int = 5) -> list[str]:
    """Frases cortas que describen un cambio de configuración: qué se agregó, quitó o cambió y dónde.
    Reglas simples: dos líneas de la misma sección que empiezan igual (mismas 2 primeras palabras) son un cambio."""
    from .i18n import _f
    so, sn = _sections(old), _sections(new)
    removed = [ln for ln in so if ln not in sn]
    added = [ln for ln in sn if ln not in so]
    key = lambda ln: " ".join(ln.split()[:2]).lower()  # noqa: E731
    phrases, used_add = [], set()
    for r in removed:
        match = next((a for a in added if a not in used_add and key(a) == key(r) and sn[a] == so[r]), None)
        if not match and so[r]:  # dentro de un bloque basta con la primera palabra («description», «shutdown»…)
            match = next((a for a in added if a not in used_add and sn[a] == so[r]
                          and a.split()[:1] == r.split()[:1]), None)
        where = so[r]
        if match:
            used_add.add(match)
            phrases.append(_f("en «{0}»: «{1}» → «{2}»", where, r, match) if where else _f("«{0}» → «{1}»", r, match))
        else:
            phrases.append(_f("en «{0}» se quitó «{1}»", where, r) if where else _f("se quitó «{0}»", r))
    # bloques nuevos: una sola frase por sección nueva
    new_secs = {}
    for a in added:
        if a in used_add:
            continue
        if sn[a] and sn[a] in added:
            new_secs.setdefault(sn[a], []).append(a)
            continue
        if not sn[a] and a in {x for x in sn.values()}:
            new_secs.setdefault(a, [])
            continue
        phrases.append(_f("en «{0}» se agregó «{1}»", sn[a], a) if sn[a] else _f("se agregó «{0}»", a))
    for sec, lines in new_secs.items():
        phrases.append(_f("se agregó «{0}» ({1} líneas)", sec, len(lines) + 1) if lines else _f("se agregó «{0}»", sec))
    more = len(phrases) - limit
    out = phrases[:limit]
    if more > 0:
        out.append(_f("y {0} cambios más", more))
    return out


def test_rule(text: str, pattern: str, kind: str) -> dict:
    """Prueba una regla de cumplimiento contra un texto de configuración (editor de políticas)."""
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error as e:
        return {"error": str(e)}
    lines = []
    for m in rx.finditer(text):  # la línea completa donde coincide, no solo el trozo
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.start())
        line = text[start:end if end >= 0 else len(text)].strip()[:160]
        if line and line not in lines:
            lines.append(line)
        if len(lines) == 5:
            break
    found = rx.search(text) is not None
    return {"found": found, "passed": found if kind == "must_contain" else not found, "lines": lines}
