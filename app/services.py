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
from .crypto import decrypt, encrypt, try_decrypt
from .db import (ChangeRequest, ComplianceResult, ConfigVersion, Device, Event, Policy, SessionLocal,
                 Vulnerability, get_setting, now)
from .drivers import FASTPATH, IOS, clean_config, compare_text, get_profile, parse_inventory, telnet_supported
from .drivers import HTTP_ONLY as _HTTP_ONLY
from .rollback import fastpath_style, rollback_plan
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


def same_config(prev_text: str, new_clean: str, prof) -> bool:
    """¿La versión guardada es igual a la nueva si se limpia con las reglas actuales? Las versiones guardadas antes de
    ampliar las líneas que se ignoran (fechas, horas, tiempo encendido) aún las contienen: sin esto, el primer respaldo
    tras actualizar OpenNCCM las vería como un cambio."""
    return compare_text(clean_config(prev_text, prof)) == compare_text(new_clean)


def versions_differ(a, b, prof) -> bool:
    """¿Dos versiones guardadas son distintas? Como same_config: una línea base guardada antes de ampliar las líneas
    volátiles (p. ej. «! System Up Time» de Ubiquiti) no debe dejar el equipo «distinto de la base» para siempre."""
    return a.sha256 != b.sha256 and not same_config(decrypt(a.content_enc),
                                                    clean_config(decrypt(b.content_enc), prof), prof)


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


def mikrotik_backup_file(device_id: int, password: str = "") -> tuple[str, bytes]:
    """RouterOS: crea un .backup en el equipo («/system backup save»), lo trae por SFTP y lo borra del equipo. El .backup
    es binario y solo se restaura en el mismo modelo; sin contraseña va sin cifrar («dont-encrypt=yes»)."""
    if password and not re.fullmatch(r"[\w.,:;!?@#%&*+=/-]{1,64}", password):
        raise ValueError("La contraseña del .backup solo admite letras, números y . , : ; ! ? @ # % & * + = / - (hasta 64)")
    lock = device_lock(device_id)
    if not lock.acquire(timeout=60):
        raise RuntimeError("El equipo está ocupado con otra tarea")
    try:
        with SessionLocal() as db:
            dev = db.get(Device, device_id)
            if dev is None or dev.device_type != "mikrotik_routeros":
                raise RuntimeError("Solo los equipos MikroTik RouterOS hacen archivos .backup")
            conn = connect(dev)
        name = f"opennccm-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            out = conn.send_command(f"/system backup save name={name} " + (f"password={password}" if password else "dont-encrypt=yes"),
                                    read_timeout=120)
            if re.search(r"(?i)failure|error|expected end of command|bad command", out):
                raise RuntimeError("RouterOS no pudo crear el .backup: " + out.strip()[:300])
            import paramiko
            sftp = paramiko.SFTPClient.from_transport(conn.remote_conn.get_transport())
            try:
                path = data = None
                for _ in range(30):  # se escribe en unos segundos; en equipos con «flash/» queda en esa carpeta
                    for p in (f"{name}.backup", f"flash/{name}.backup"):
                        try:
                            if sftp.stat(p).st_size:
                                path = p
                        except OSError:
                            pass
                    if path:
                        break
                    time.sleep(1)
                if not path:
                    raise RuntimeError("El equipo no dejó el .backup a la vista por SFTP (¿el usuario tiene permiso «ftp»?)")
                with sftp.open(path, "rb") as f:
                    data = f.read()
                # se borra por SFTP: en RouterOS 6 «/file remove» justo después aún no ve el archivo y lo deja
                try:
                    sftp.remove(path)
                except OSError:
                    conn.send_command(f'/file remove "{path}"', read_timeout=30)
            finally:
                sftp.close()
        finally:
            conn.disconnect()
        return name + ".backup", data
    finally:
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


def _read_neighbors(conn, device_type: str) -> str | None:
    """Vecinos LLDP/CDP para el mapa de la red (se puede desactivar en Ajustes). Nunca hace fallar el respaldo."""
    from . import topology
    cmds = topology.commands(device_type)
    if not cmds or get_setting("topology_enabled", "1") != "1":
        return None
    out = []
    for c in cmds:
        try:
            out.append(topology.MARK + c + "\n" + conn.send_command(c, read_timeout=30))
        except Exception as e:  # noqa: BLE001 - sin LLDP/CDP o la orden no existe: sin vecinos
            log.info("vecinos (%s): %s", c, e)
    return "\n".join(out)


def _read_ports(conn, device_type: str) -> str | None:
    """Estado de los puertos (enlace, velocidad, módulo SFP) para la ficha del equipo. Nunca hace fallar el respaldo."""
    from . import ports
    try:
        return ports.read(conn, device_type)
    except Exception as e:  # noqa: BLE001
        log.info("puertos (%s): %s", device_type, e)
        return None


def _backup_device(device_id: int, trigger: str, username: str | None) -> str:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        if dev is None:
            return "missing"
        prof = get_profile(dev.device_type)
        try:
            if prof.key in HTTP_ONLY:  # SwitchOS: por HTTP, sin SSH
                from . import swos
                if dev.credential is None:
                    raise RuntimeError("El dispositivo no tiene credencial asignada")
                cred = (dev.credential.username, decrypt(dev.credential.password_enc) if dev.credential.password_enc else "")
                running, inventory = swos.backup(dev, cred)
                startup = neighbors_raw = ports_raw = None
            else:
                with connect(dev) as conn:
                    running = conn.send_command(dev.custom_backup_cmd or prof.backup, read_timeout=180)
                    startup = conn.send_command(prof.startup, read_timeout=180) if prof.startup else None
                    inventory = "\n\n".join(conn.send_command(c, read_timeout=60) for c in prof.inventory.split("\n")
                                            if c.strip()) if prof.inventory else None
                    neighbors_raw = _read_neighbors(conn, dev.device_type)
                    ports_raw = _read_ports(conn, dev.device_type)
        except Exception as e:  # noqa: BLE001
            dev.last_status, dev.last_error = "error", (str(e) or repr(e))[:2000]
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
        prev_text = try_decrypt(prev.content_enc) if prev else ""
        if prev_text is None:  # la versión anterior se cifró con otra clave: no se puede comparar
            prev_text = ""
            add_event("warning", "Versión anterior ilegible",
                      f"{dev.name}: la versión anterior se cifró con otra clave (secret.key); se guarda la actual completa.",
                      dev.id)
        changed = prev is None or (prev.sha256 != sha and not same_config(prev_text, running, prof))
        result = "unchanged"
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

        if neighbors_raw is not None:
            from . import topology
            dev.neighbors = json.dumps(topology.parse(neighbors_raw, dev.device_type))

        if prof.key not in HTTP_ONLY:  # puertos y su estado, para la ficha del equipo
            from . import ports as ports_mod
            mdl = (parse_inventory(inventory, prof)["model"] if inventory else None) or dev.model
            plist, has_status = ports_mod.parse(ports_raw, dev.device_type, running, mdl)
            dev.ports = json.dumps({"at": now().isoformat(), "status": has_status, "ports": plist}) if plist else None

        if inventory:
            inv = parse_inventory(inventory, prof)
            dev.os_version = inv["os_version"] or dev.os_version
            dev.model = inv["model"] or dev.model
            dev.serial = inv["serial"] or dev.serial
            dev.inventory_raw_enc = encrypt(inventory)

        new_drift = False
        if dev.baseline_version_id:
            base = db.get(ConfigVersion, dev.baseline_version_id)
            base_text = try_decrypt(base.content_enc) if base is not None else None
            drift = base is not None and base.sha256 != sha and (base_text is None or not same_config(base_text, running, prof))
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
                jobs.submit(_auto_rollback, dev.id, dev.name, dev.device_type, dev.host, running, prev_text)
        if result in ("changed", "first"):  # copia en Git (si está activada en Ajustes)
            from . import gitexport
            who = f" por {username}" if username else ""
            msg = (f"{dev.name}: +{added}/-{removed} líneas [{trigger}{who}]" if result == "changed"
                   else f"{dev.name}: primer respaldo [{trigger}{who}]")
            jobs.submit(gitexport.on_new_version, dev.id, msg)

    run_compliance(device_id)
    return result


def _auto_rollback(dev_id: int, dev_name: str, device_type: str, host: str, current: str, target: str):
    cmds, notes = rollback_plan(current, target, get_profile(device_type), host)
    if cmds is None:
        add_event("critical", "Reversión automática no disponible",
                  f"{dev_name}: hubo un cambio no autorizado, pero {notes[0]}", dev_id)
        return
    log.info("Reversión automática de %s: %d comandos", dev_name, len(cmds))
    if notes:
        add_event("warning", "Reversión automática: revisar a mano", f"{dev_name}:\n" + "\n".join(notes), dev_id)
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
#   MikroTik («bad command name», «expected end of command», «failure: ...», «input does not match», «no such item»,
#   «invalid value for …», «ambiguous value», «already have such …», «value of … out of range»),
#   FortiGate («Command fail. Return code», «Unknown action», «entry not found», «value parse error»),
#   Palo Alto («Invalid syntax», «Server error»), Nokia SR OS («MINOR:/MAJOR:»), Cisco IOS-XR («!!% ...»),
#   VyOS/EdgeRouter («Set failed», «Invalid command»), Check Point («CLINFR0329 Invalid command»),
#   Broadcom FastPath/EdgeSwitch/Netgear/Dell N («Failed to ...», «An invalid ...»), HP ProCurve («Invalid input:»).
DEVICE_ERROR = re.compile(
    r"(?im)^\s*(%+\s*(Invalid|Incomplete|Ambiguous|Unrecognized|Unknown|Error|Bad|Wrong|Failed|Cannot)|!!%|"
    r"syntax error|unknown command|error:|Error\b|Failed to |An invalid |Invalid (input|command|syntax|value)|"
    r"Incomplete command|Unrecognized command|Command not found|Command fail|Unknown action|bad command name|"
    r"expected end of command|failure:|input does not match|entry not found|value parse error|Server error|"
    r"no such item|invalid value|ambiguous value|already have|value of \S+ out of range|missing value|bad argument|"
    r"(MINOR|MAJOR|CRITICAL): |CLINFR\d+|(Set|Delete|Commit) failed|Cannot |Unable to |Incorrect usage)")
# fin de la respuesta a un comando: el prompt del equipo (# > $ ]) o una pregunta de confirmación
PROMPT_END = r"([#>$\]]\s*$|\[confirm\]\s*$|[\[(](y/n|yes/no|Y/N)[\])]\s*[?:]?\s*$)"
CONFIRM = re.compile(r"(\[confirm\]|[\[(](y/n|yes/no|Y/N)[\])])\s*[?:]?\s*$")
# cómo descartar cambios sin confirmar en plataformas con «commit» si algo falla a mitad
DISCARD = {"juniper_junos": "rollback 0", "juniper": "rollback 0", "cisco_xr": "abort", "cisco_viptela": "abort",
           "vyos": "discard", "ubiquiti_edgerouter": "discard", "huawei_vrpv8": "abort", "nokia_srl": "discard now",
           "paloalto_panos": "revert config"}


# Red de seguridad del propio equipo mientras se aplican cambios:
#  * MikroTik: «modo seguro» (Ctrl+X). Si algo falla, OpenNCCM cierra la sesión sin liberarlo y RouterOS deshace solo
#    todo lo aplicado; si la conexión se corta a mitad (p. ej. un cambio deja sin acceso), también.
#  * Junos: «commit confirmed 5». OpenNCCM vuelve a conectar y confirma; si no puede (el cambio cortó el acceso), el
#    equipo vuelve solo a la configuración anterior en 5 minutos, sin reiniciar.
SAFE_MODE = {"mikrotik_routeros"}
HTTP_ONLY = _HTTP_ONLY  # sin SSH: solo respaldo por HTTP
CONFIRMED = {"juniper_junos", "juniper"}
CONFIRM_MINUTES = 5


def _mikrotik_safe_mode(conn, take: bool) -> str:
    """Toma o libera el modo seguro (Ctrl+X). Se comprueba por el prompt, que lleva «<SAFE>» mientras está activo
    (RouterOS 7 responde «Taking Safe Mode session... Success!»; RouterOS 6, «[Safe Mode taken]»)."""
    conn.clear_buffer()
    conn.write_channel("\x18")
    out = ""
    try:
        # al tomarlo se espera el prompt con «<SAFE>»; al liberarlo, el prompt normal «] >»
        want = r"<SAFE>\s*$" if take else r"\]\s*>\s*$"
        out = conn.read_until_pattern(pattern=rf"(?:{want}|(?i:already taken|failed))", read_timeout=20)
    except Exception as e:  # noqa: BLE001
        out += str(e)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    active = "<SAFE" in last
    if active != take:
        what, after = (("activar", "No se aplicó ningún cambio.") if take else
                       ("liberar", "Al cerrar la sesión, RouterOS deshace los cambios aplicados."))
        raise RuntimeError(f"No se pudo {what} el modo seguro de RouterOS (Ctrl+X): {out.strip()[-300:]}\n{after}")
    return out + ("\n[modo seguro activado]" if take else "\n[modo seguro liberado: cambios conservados]")


def _mikrotik_safe_undo(conn) -> None:
    """Sale del modo seguro deshaciendo los cambios (Ctrl+D). RouterOS 7 también los deshace al cortar la sesión;
    RouterOS 6 no siempre, por eso se pide de forma explícita."""
    try:
        conn.write_channel("\x04")
        time.sleep(3)
        conn.read_channel()
    except Exception:  # noqa: BLE001 - la sesión puede haberse cerrado ya: también deshace los cambios
        pass


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
        if prof.key in HTTP_ONLY:
            raise RuntimeError(f"{prof.label} no admite cambios por comandos: se configura desde su interfaz web.")
        if not exec_mode:
            # ventana máxima por si el proceso muere; normalmente se cierra al terminar el respaldo post-cambio
            dev.maintenance_until = now() + timedelta(minutes=5)
            db.commit()
        if exec_mode:
            with connect(dev) as conn:
                out = []
                for c in commands:
                    out.append(f"### {c}\n" + conn.send_command(c, read_timeout=180))
                return "\n".join(out)
        return apply_config(dev, commands)


def apply_config(dev: Device, commands: list[str]) -> str:
    """Aplica comandos de configuración con la red de seguridad del equipo (modo seguro, commit confirmed), se detiene
    en el primero que el equipo rechace y guarda. «dev» puede ser un equipo que no está en OpenNCCM (restaurar una
    copia en otro equipo): solo se usan su dirección, puerto, tipo y credencial."""
    prof = get_profile(dev.device_type)
    confirmed = prof.key in CONFIRMED
    with connect(dev) as conn:
        safe = prof.key in SAFE_MODE
        out = _mikrotik_safe_mode(conn, True) if safe else ""
        try:
            out += _push_stepwise(conn, commands, prof)
        except Exception:
            if safe:  # deshacer todo lo aplicado en modo seguro antes de cerrar la sesión
                _mikrotik_safe_undo(conn)
            raise
        if safe:
            out += _mikrotik_safe_mode(conn, False)
        if confirmed:
            out += "\n" + conn.commit(confirm=True, confirm_delay=CONFIRM_MINUTES)
        elif prof.commit:
            out += "\n" + conn.commit()
        if prof.commit:
            try:
                conn.exit_config_mode()
            except Exception:  # noqa: BLE001
                pass
        if prof.save and not confirmed:
            try:
                out += "\n" + conn.save_config()
            except (NotImplementedError, AttributeError):
                pass
    if confirmed:
        # volver a entrar demuestra que el cambio no cortó el acceso; entonces se confirma
        try:
            with connect(dev) as conn:
                conn.config_mode()
                out += "\n" + conn.commit()
                try:
                    conn.exit_config_mode()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"El cambio se aplicó con «commit confirmed {CONFIRM_MINUTES}», pero OpenNCCM no pudo volver a "
                f"conectar para confirmarlo ({e}). Si el cambio cortó el acceso, el equipo volverá solo a la "
                f"configuración anterior en {CONFIRM_MINUTES} minutos.") from e
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


def rollback_commands(device_id: int, version_id: int) -> tuple[list[str] | None, list[str]]:
    """(comandos, avisos) para volver a la versión; comandos None si la plataforma no se puede revertir por comandos."""
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        target = db.get(ConfigVersion, version_id)
        current = latest_version(db, device_id)
        if not target or not current:
            return [], []
        return rollback_plan(decrypt(current.content_enc), decrypt(target.content_enc), get_profile(dev.device_type),
                             dev.host)


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


KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_kev_cache: tuple[float, frozenset] = (0.0, frozenset())


def _kev_file():
    from .config import DATA_DIR
    return DATA_DIR / "kev.json"


def refresh_kev(max_age: int = 86400) -> None:
    """Descarga (como mucho una vez al día) el catálogo KEV de CISA: CVE explotados activamente."""
    f = _kev_file()
    try:
        if f.exists() and time.time() - f.stat().st_mtime < max_age:
            return
        r = requests.get(KEV_URL, timeout=30)
        r.raise_for_status()
        ids = sorted({v["cveID"] for v in r.json().get("vulnerabilities", []) if v.get("cveID")})
        f.write_text(json.dumps(ids), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — sin conexión: se usa la copia anterior (o ninguna)
        log.info("No se pudo actualizar el catálogo KEV de CISA: %s", e)


def kev_ids() -> frozenset:
    """CVE del catálogo KEV guardado (vacío si nunca se pudo descargar)."""
    global _kev_cache
    f = _kev_file()
    try:
        mt = f.stat().st_mtime
    except OSError:
        return frozenset()
    if mt != _kev_cache[0]:
        try:
            _kev_cache = (mt, frozenset(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            return frozenset()
    return _kev_cache[1]


NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_PAGE = 40      # CVE por petición. Los de equipos muy usados (Cisco IOS…) traen miles de CPE cada uno: una página de 200
                   # pesaba más de 1,5 MB, tardaba casi un minuto en NVD y la conexión solía cortarse a medias
NVD_MIN_PAGE = 5   # si una página se corta, se reintenta con la mitad de CVE hasta este mínimo
NVD_MAX = 2000     # tope de CVE por equipo
NVD_TRIES = 4      # intentos por página ante cortes, tiempos agotados, límite de peticiones (403/429) o errores 5xx
_nvd_sent: list[float] = []  # momentos de las últimas peticiones, para respetar el límite de NVD entre búsquedas
_nvd_lock = threading.Lock()


class NvdError(RuntimeError):
    def __init__(self, tries: int, last: str):
        super().__init__(f"NVD: {tries} intentos ({last})")
        self.tries, self.last = tries, last


_nvd_key_cache: tuple[float, str] = (0.0, "")


def nvd_api_key() -> str:
    """Clave de la API de NVD: la de opennccm.env (NCM_NVD_API_KEY) o, si no hay, la guardada en Ajustes (cifrada)."""
    global _nvd_key_cache
    if NVD_API_KEY:
        return NVD_API_KEY
    if time.time() - _nvd_key_cache[0] > 30:
        try:
            key = (get_setting("nvd_api_key", "") or "").strip()
        except Exception:  # noqa: BLE001 — sin base de datos se consulta igual, sin clave
            key = ""
        _nvd_key_cache = (time.time(), key)
    return _nvd_key_cache[1]


def _nvd_wait(sleep, clock):
    """Límite público de NVD: 5 peticiones cada 30 s sin clave, 50 con clave (ventana móvil)."""
    limit = 50 if nvd_api_key() else 5
    with _nvd_lock:
        now = clock()
        _nvd_sent[:] = [t for t in _nvd_sent if now - t < 30]
        if len(_nvd_sent) >= limit:
            sleep(30.5 - (now - _nvd_sent[0]))
            now = clock()
        _nvd_sent.append(now)


def nvd_query(params: dict, sleep=time.sleep, clock=time.monotonic) -> list[dict]:
    """Todos los CVE de una búsqueda en la API 2.0 de NVD, en páginas pequeñas y con reintentos por página.
    Si una página falla tras todos los intentos se lanza NvdError (y el resultado anterior del equipo se conserva)."""
    headers = {"apiKey": nvd_api_key()} if nvd_api_key() else {}
    out, start, total, size = [], 0, None, NVD_PAGE
    while total is None or start < min(total, NVD_MAX):
        last = ""
        for attempt in range(NVD_TRIES):
            if attempt:
                sleep(2 * attempt)
            _nvd_wait(sleep, clock)
            try:
                r = requests.get(NVD_URL, params={**params, "resultsPerPage": size, "startIndex": start},
                                 headers=headers, timeout=(10, 90))  # NVD a veces envía CVE grandes muy despacio
                if r.status_code in (403, 429) or r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                    ValueError) as e:  # «Response ended prematurely», JSON cortado…
                last = str(e) or type(e).__name__
                size = max(NVD_MIN_PAGE, size // 2)  # respuesta demasiado grande: la próxima, más pequeña
        else:
            raise NvdError(NVD_TRIES, last)
        total = int(data.get("totalResults") or 0)
        page = data.get("vulnerabilities", [])
        out += page
        if not page:
            break
        start += len(page)
    return out


# Rangos reservados para documentación (RFC 5737): nunca son la dirección real de un servidor de la red
EXAMPLE_NET = re.compile(r"(?<![\d.])(192\.0\.2|198\.51\.100|203\.0\.113)\.(25[0-5]|2[0-4]\d|1?\d?\d)"
                         r"(?:/\d{1,2})?(?![\d.])")


# Comandos que apagan el acceso por el que OpenNCCM entra al equipo
TELNET_OFF = re.compile(r"(?im)^\s*(no\s+boot config flags telnetd|no ip telnet server|/ip service set telnet\b.*\bdisabled=yes"
                        r"|delete system services telnet\b|transport input ssh\s*$|undo telnet server)")
SSH_OFF = re.compile(r"(?im)^\s*(no ip ssh server|no ssh\s*$|/ip service set ssh\b.*\bdisabled=yes"
                     r"|delete system services ssh\s*$|undo stelnet server)")
ROOT_SSH_OFF = re.compile(r"(?im)^\s*set system services ssh root-login deny")


def lockout_risks(body: str, devices) -> list[str]:
    """Por qué aplicar estos comandos dejaría a OpenNCCM sin acceso a alguno de los equipos (vacío si no hay riesgo).
    Los equipos deben traer cargada su credencial."""
    from .i18n import _f
    out = []
    for d in devices:
        proto = (d.protocol or "ssh").lower()
        if proto == "telnet" and TELNET_OFF.search(body or ""):
            out.append(_f("OpenNCCM entra a {0} por Telnet: desactivarlo lo dejaría sin acceso. Cambie primero el equipo a SSH (Editar equipo) y vuelva a respaldarlo.", d.name))
        if proto == "ssh" and SSH_OFF.search(body or ""):
            out.append(_f("OpenNCCM entra a {0} por SSH: estos comandos lo desactivan y lo dejarían sin acceso.", d.name))
        user = (d.credential.username if getattr(d, "credential", None) else "") or ""
        if user.strip().lower() == "root" and ROOT_SSH_OFF.search(body or ""):
            out.append(_f("OpenNCCM entra a {0} con el usuario root: bloquear root por SSH lo dejaría sin acceso. Use primero una credencial con otro usuario.", d.name))
    return out


def example_addresses(text: str) -> list[str]:
    """Direcciones de ejemplo (192.0.2.x, 198.51.100.x, 203.0.113.x) que aparecen en unos comandos."""
    return sorted({m.group(0) for m in EXAMPLE_NET.finditer(text or "")})


def scan_cves(device_id: int) -> int:
    refresh_kev()
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        prof = get_profile(dev.device_type)
        if not dev.os_version:
            return -1
        items = []  # se consulta todo antes de borrar nada: si NVD falla se conserva el último resultado
        for params in nvd_searches(prof, dev.os_version):
            items += nvd_query(params)
        db.execute(delete(Vulnerability).where(Vulnerability.device_id == device_id))
        count, seen = 0, set()
        for item in items:
            if item["cve"]["id"] in seen:  # NVD puede repetir un CVE entre páginas si se publica uno durante la consulta
                continue
            seen.add(item["cve"]["id"])
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
    _cve_mark_scanned(device_id)
    return count


def nvd_searches(prof, os_version: str) -> list[dict]:
    """Búsquedas en NVD para un perfil y una versión: una por CPE (algunos productos tienen varios nombres en NVD).
    Sin CPE se busca por texto (fabricante + versión)."""
    cpes = (prof.cpe,) if isinstance(prof.cpe, str) else tuple(prof.cpe or ())
    if not cpes:
        return [{"keywordSearch": f"{prof.vendor} {os_version}"}]
    if prof.cpe_format == "junos" and (jv := junos_cpe_version(os_version)):
        return [{"virtualMatchString": f"{c}:{jv}"} for c in cpes]
    ver = os_version
    if prof.cpe_version and (m := re.search(prof.cpe_version, os_version)):
        ver = m.group(1)
    return [{"virtualMatchString": f"{c}:{_cpe_escape(ver)}"} for c in cpes]


def junos_cpe_version(v: str) -> str | None:
    """Versión de Junos como la registra NVD: versión y actualización en campos separados.
    «21.4R3-S5.4» → «21.4:r3-s5», «22.2R1.9» → «22.2:r1», «15.1X49-D200.3» → «15.1x49:d200»."""
    m = re.match(r"^(\d+\.\d+)(R\d+)(?:-(S\d+))?", v.strip(), re.I)
    if m:
        upd = m.group(2) + (f"-{m.group(3)}" if m.group(3) else "")
        return f"{_cpe_escape(m.group(1))}:{_cpe_escape(upd)}"
    m = re.match(r"^(\d+\.\d+X\d+)-(D\d+)", v.strip(), re.I)
    if m:
        return f"{_cpe_escape(m.group(1))}:{_cpe_escape(m.group(2))}"
    return None


# búsqueda de CVE de un equipo en segundo plano (NVD puede tardar minutos con equipos que tienen muchos CVE)
_cve_running: dict[int, float] = {}
_cve_last: dict[int, dict] = {}
_cve_lock = threading.Lock()


def _cve_mark_scanned(device_id: int):
    from .db import set_setting
    with _cve_lock:
        try:
            seen = json.loads(get_setting("cve_scanned", "{}") or "{}")
        except ValueError:
            seen = {}
        seen[str(device_id)] = now().isoformat()
        set_setting("cve_scanned", json.dumps(seen))


def cve_scanned_at(device_id: int):
    """Cuándo se consultó NVD por última vez para el equipo (también si no se encontró ningún CVE)."""
    from datetime import datetime
    try:
        v = json.loads(get_setting("cve_scanned", "{}") or "{}").get(str(device_id))
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def cve_scan_state(device_id: int) -> dict:
    """{"running": desde (epoch) o None, "last": {"ok", "n", "tries", "error", "at"} o None}."""
    return {"running": _cve_running.get(device_id), "last": _cve_last.get(device_id)}


def start_cve_scan(device_id: int) -> bool:
    """Lanza la búsqueda en segundo plano; False si ya hay una en curso para el equipo."""
    with _cve_lock:
        if device_id in _cve_running:
            return False
        _cve_running[device_id] = time.time()
    jobs.submit(_cve_job, device_id)
    return True


def _cve_job(device_id: int):
    res = {"ok": False, "n": None, "tries": None, "error": None, "at": time.time()}
    try:
        n = scan_cves(device_id)
        res.update(ok=n >= 0, n=n)
    except NvdError as e:
        res.update(tries=e.tries, error=e.last)
    except Exception as e:  # noqa: BLE001
        log.warning("CVE scan falló para %s: %s", device_id, e)
        res["error"] = str(e) or type(e).__name__
    finally:
        res["at"] = time.time()
        with _cve_lock:
            _cve_last[device_id] = res
            _cve_running.pop(device_id, None)


def scan_cves_many(ids: list[int]):
    for i in ids:  # nvd_query ya respeta el límite de peticiones de NVD
        try:
            scan_cves(i)
        except Exception as e:  # noqa: BLE001
            log.warning("CVE scan falló para %s: %s", i, e)


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
