"""Corrección automática de cumplimiento (opcional, apagada por defecto).

Una política con la corrección automática activada aplica sola, en los equipos que incumplen, las correcciones de las
reglas elegidas. Solo entran reglas:
  * con comandos de corrección y con el sello «Verificada» (app/verified.py: probadas enteras en un equipo real), y
  * cuya corrección sigue siendo la que se aprobó al activarla (Rule.autofix_sig: si alguien cambia el patrón o los
    comandos, la regla sale de la corrección automática hasta que se vuelva a activar).

Cada corrección, por equipo y regla (una que se bloquea o se revierte no arrastra a las demás):
  1. respalda el equipo (la versión de antes, a la que se vuelve si algo sale mal) y comprueba que la regla sigue sin
     cumplirse;
  2. revisa lo mismo que una solicitud de cambio sin aprobación: direcciones de ejemplo, que no deje a OpenNCCM sin
     acceso y el impacto (reinicios o puertos que se apagan). Si hay algo, no se aplica: queda «Bloqueada» para revisar;
  3. aplica los comandos, vuelve a respaldar y audita;
  4. si el equipo rechazó algo o la regla sigue sin cumplirse, revierte a la versión de antes.

Solo dentro de la ventana de la política (días y horas; sin horas, a cualquier hora). Tras un intento que no terminó en
«Aplicada», esa regla no se vuelve a intentar en ese equipo durante 24 h (para no insistir sobre un equipo que la
rechaza), salvo que alguien pulse «Reintentar». Una aplicada tampoco se repite en 24 h: si alguien la deshace a mano,
no se pelea con él. Todo queda en el historial (Cumplimiento › Corrección automática), en Eventos y en Auditoría.
Un usuario con la función «Corrección automática» la activa, la configura o la pausa entera. Cada activación queda a
su nombre (Policy.autofix_by) y solo corrige los equipos que él puede ver; si ya no existe, está desactivado o perdió la
función, la política deja de aplicarse sola. Un equipo con «Permitir corrección automática» apagado (Editar equipo,
Device.autofix_allowed) nunca se corrige solo.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from .db import (AutoFixRun, ComplianceResult, ConfigVersion, Device, Policy, Rule, SessionLocal, audit, get_setting,
                 now, set_setting)
from .i18n import _, _f

log = logging.getLogger("opennccm.autofix")

WHO = "corrección automática"   # usuario con el que se respalda y se audita
COOLDOWN = timedelta(hours=24)
PAUSE_KEY = "autofix_paused"
DAYS = "0123456"                  # lunes = 0 (datetime.weekday)
STATUSES = ("running", "applied", "rolled_back", "rollback_failed", "failed", "blocked")
_lock = threading.Lock()


# --------------------------------------------------------------------------- reglas que entran
def sig(rule) -> str:
    """Huella de lo que se aprobó: tipo, patrón y comandos de corrección."""
    raw = "\0".join([rule.kind or "", rule.pattern or "", (rule.remediation or "").strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def rule_state(rule, verified_map: dict) -> str:
    """«on» (entra), «off» (no elegida), «changed» (cambió desde que se activó), «unverified» o «nofix»."""
    if not (rule.remediation or "").strip():
        return "nofix"
    if not verified_map.get(str(rule.id)):
        return "unverified"
    if not rule.autofix_sig:
        return "off"
    return "on" if rule.autofix_sig == sig(rule) else "changed"


def paused() -> bool:
    return get_setting(PAUSE_KEY, "0") == "1"


def set_paused(on: bool) -> None:
    set_setting(PAUSE_KEY, "1" if on else "0")


# --------------------------------------------------------------------------- ventana
_HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hm(v: str | None) -> str | None:
    """«7:5»/«07:05» → «07:05»; vacío o no válido → None."""
    m = _HM.match((v or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def clean_days(v) -> str:
    return "".join(d for d in DAYS if d in "".join(str(x) for x in (v or "")))


def in_window(policy, at: datetime | None = None) -> bool:
    """¿Está «at» dentro de la ventana de la política? Sin horas: cualquier hora de los días marcados. Una ventana que
    cruza la medianoche (22:00–06:00) pertenece al día en que empieza: el lunes a las 02:00 es la del domingo."""
    at = at or now()
    days = policy.autofix_days if policy.autofix_days is not None else DAYS
    a, b = parse_hm(policy.autofix_from), parse_hm(policy.autofix_to)
    if not a or not b:
        return str(at.weekday()) in days
    t = at.strftime("%H:%M")
    if a < b:
        return a <= t < b and str(at.weekday()) in days
    if a == b:  # de 00:00 a 00:00: el día entero
        return str(at.weekday()) in days
    if t >= a:
        return str(at.weekday()) in days
    if t < b:
        return str((at.weekday() - 1) % 7) in days
    return False


def window_text(policy) -> str:
    names = [_("lun"), _("mar"), _("mié"), _("jue"), _("vie"), _("sáb"), _("dom")]
    days = policy.autofix_days if policy.autofix_days is not None else DAYS
    a, b = parse_hm(policy.autofix_from), parse_hm(policy.autofix_to)
    d = _("todos los días") if days == DAYS else (", ".join(names[int(x)] for x in days) if days else _("ningún día"))
    return f"{d} · {a}–{b}" if a and b else f"{d} · " + _("a cualquier hora")


# --------------------------------------------------------------------------- qué hay que corregir
def owner(db, policy):
    """(usuario, problema) de quien activó la corrección automática de la política. Se comprueba en cada vuelta: si
    ya no existe, está desactivado o perdió la función «Corrección automática», la política deja de aplicarse sola."""
    from .db import User
    if not policy.autofix_by:
        return None, _("No se aplica: vuelva a guardarla para que quede a nombre de quien la activa.")
    u = db.scalar(select(User).where(User.username == policy.autofix_by))
    if u is None or not u.active:
        return None, _f("La activó {0}, que ya no existe o está desactivado: no se aplica.", policy.autofix_by)
    from . import perms
    if not perms.has(u, "autofix"):
        return None, _f("La activó {0}, que ya no tiene la función «Corrección automática»: no se aplica.", policy.autofix_by)
    return u, None


def _eligible(db) -> dict[int, tuple]:
    """{policy_id: (policy, {rule_id: rule}, equipos)} de las políticas activas con corrección automática y en su
    ventana. «equipos»: los que puede ver quien la activó (None = todos); solo esos se corrigen."""
    from . import verified
    from .auth import device_scope
    vmap = verified.all_of("rule")
    out = {}
    for p in db.scalars(select(Policy).where(Policy.enabled.is_(True), Policy.autofix.is_(True))):
        if not in_window(p):
            continue
        u, problem = owner(db, p)
        if problem:
            continue
        rules = {r.id: r for r in p.rules if rule_state(r, vmap) == "on"}
        if rules:
            out[p.id] = (p, rules, device_scope(u))
    return out


def _held(db) -> set[tuple[int, int]]:
    """(equipo, regla) intentados hace menos de 24 h (y no liberados con «Reintentar»)."""
    since = now() - COOLDOWN
    held = set()
    for run in db.scalars(select(AutoFixRun).where(AutoFixRun.started_at >= since, AutoFixRun.hold.is_(True))):
        for rid in json.loads(run.rule_ids or "[]"):
            held.add((run.device_id, int(rid)))
    return held


def pending(db=None) -> list[tuple[int, int, list[int]]]:
    """[(equipo, política, [reglas])] que la corrección automática aplicaría ahora mismo."""
    own = db is None
    db = db or SessionLocal()
    try:
        elig = _eligible(db)
        if not elig:
            return []
        held = _held(db)
        rule_ids = [rid for _p, rules, _s in elig.values() for rid in rules]
        fails = db.execute(select(ComplianceResult.device_id, ComplianceResult.policy_id, ComplianceResult.rule_id)
                           .where(ComplianceResult.passed.is_(False), ComplianceResult.rule_id.in_(rule_ids))).all()
        devs = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_({f.device_id for f in fails} or {0})))}
        at = now()
        groups: dict[tuple[int, int], list[int]] = {}
        for f in fails:
            d = devs.get(f.device_id)
            if d is None or not d.enabled or d.retired_at is not None or not d.autofix_allowed:
                continue  # desactivado, retirado o con «Permitir corrección automática» apagado (Editar equipo)
            if d.maintenance_until and d.maintenance_until > at:
                continue  # se le está aplicando otro cambio: en la próxima vuelta
            if f.policy_id not in elig or (f.device_id, f.rule_id) in held:
                continue
            scope = elig[f.policy_id][2]
            if scope is not None and f.device_id not in scope:
                continue  # fuera de los grupos de quien la activó
            groups.setdefault((f.device_id, f.policy_id), []).append(f.rule_id)
        return [(d, p, sorted(r)) for (d, p), r in sorted(groups.items())]
    finally:
        if own:
            db.close()


# --------------------------------------------------------------------------- ejecución
def sweep() -> int:
    """Aplica lo pendiente (cada 5 minutos y después de cada auditoría). Devuelve cuántas correcciones intentó."""
    if paused() or not _lock.acquire(blocking=False):
        return 0
    try:
        _abort(None, _("Interrumpida: OpenNCCM se reinició mientras se aplicaba."), older=timedelta(hours=1))
        done = 0
        # regla por regla: una que se bloquea o se revierte no arrastra a las demás del mismo equipo
        for dev_id, pid, rids in pending():
            for rid in rids:
                if paused():  # alguien la pausó mientras tanto
                    return done
                try:
                    run_one(dev_id, pid, [rid])
                except Exception as e:  # noqa: BLE001 — un equipo no frena a los demás
                    log.exception("Corrección automática en el equipo %s: %s", dev_id, e)
                    _abort(dev_id, str(e) or repr(e))
                done += 1
        return done
    finally:
        _lock.release()


def kick() -> None:
    """Después de una auditoría: si hay alguna política con corrección automática, revisar en segundo plano."""
    try:
        with SessionLocal() as db:
            on = db.scalar(select(Policy.id).where(Policy.enabled.is_(True), Policy.autofix.is_(True)).limit(1))
        if on is not None and not paused() and not _lock.locked():
            from . import services
            services.jobs.submit(sweep)
    except Exception as e:  # noqa: BLE001 — nunca estropea la auditoría
        log.warning("Corrección automática: %s", e)


def _abort(dev_id: int | None, why: str, older: timedelta | None = None) -> None:
    """Cierra como «Fallida» lo que quedó «En curso»: tras un error inesperado (ese equipo) o un reinicio (más viejo
    que «older»), para que no quede colgado ni bloquee la regla para siempre."""
    with SessionLocal() as db:
        q = select(AutoFixRun.id).where(AutoFixRun.status == "running")
        if dev_id is not None:
            q = q.where(AutoFixRun.device_id == dev_id)
        if older is not None:
            q = q.where(AutoFixRun.started_at < now() - older)
        ids = list(db.scalars(q))
    for rid in ids:
        _finish(rid, "failed", why)


def _failing(db, dev_id: int, rule_ids: list[int]) -> set[int]:
    return set(db.scalars(select(ComplianceResult.rule_id).where(ComplianceResult.device_id == dev_id,
                                                                 ComplianceResult.rule_id.in_(rule_ids),
                                                                 ComplianceResult.passed.is_(False))))


def _latest(db, dev_id: int):
    from .services import latest_version
    return latest_version(db, dev_id)


def _finish(run_id: int, status: str, message: str, output: str = "", after_id=None, hold=True) -> None:
    with SessionLocal() as db:
        run = db.get(AutoFixRun, run_id)
        run.status, run.message, run.finished_at, run.hold = status, message[:4000], now(), hold
        run.output = (output or "")[-20000:] or None
        if after_id:
            run.version_after_id = after_id
        db.commit()
        dev_id, title = run.device_id, f"{run.device_name} · {run.policy_name}"
        rules = ", ".join(json.loads(run.rule_names or "[]"))
    from .services import add_event
    sev, cat = {"applied": ("info", "Corrección automática aplicada"),
                "rolled_back": ("major", "Corrección automática revertida"),
                "rollback_failed": ("critical", "Corrección automática: falló la reversión"),
                "failed": ("major", "Corrección automática fallida"),
                "blocked": ("warning", "Corrección automática bloqueada")}[status]
    add_event(sev, cat, f"{title}: {rules}\n{message}", dev_id, notify=status != "applied")
    audit(WHO, f"autofix_{status}", f"{title}: {rules} · {message}"[:4000])


def run_one(dev_id: int, policy_id: int, rule_ids: list[int]) -> str | None:
    """Corrige un equipo con las reglas de una política. Devuelve el estado final, o None si no hizo falta."""
    from . import services
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        pol = db.get(Policy, policy_id)
        rules = [r for r in db.scalars(select(Rule).where(Rule.id.in_(rule_ids))) if r.policy_id == policy_id]
        if dev is None or pol is None or not rules:
            return None
        run = AutoFixRun(device_id=dev.id, device_name=dev.name, policy_id=pol.id, policy_name=pol.name,
                         rule_ids=json.dumps([r.id for r in rules]), rule_names=json.dumps([r.name for r in rules]),
                         status="running", started_at=now(), hold=True)
        db.add(run)
        db.commit()
        run_id = run.id

    # 1) respaldo de antes: la versión a la que se vuelve
    res = services.backup_device(dev_id, "manual", WHO)
    if res in ("busy", "disabled", "missing"):
        with SessionLocal() as db:  # nada que registrar: se intentará en la próxima vuelta
            db.delete(db.get(AutoFixRun, run_id))
            db.commit()
        return None
    with SessionLocal() as db:
        before = _latest(db, dev_id)
        if res == "error" or before is None:
            err = (db.get(Device, dev_id).last_error or "") if res == "error" else ""
            _finish(run_id, "failed", _("No se pudo respaldar el equipo antes de corregir: no se tocó nada.")
                    + (f"\n{err}" if err else ""))
            return "failed"
        still = _failing(db, dev_id, rule_ids)
        run = db.get(AutoFixRun, run_id)
        run.version_before_id = before.id
        if not still:  # ya cumple (alguien lo corrigió): no hace falta
            db.delete(run)
            db.commit()
            return None
        rules = [r for r in db.scalars(select(Rule).where(Rule.id.in_(still)))]
        run.rule_ids, run.rule_names = json.dumps([r.id for r in rules]), json.dumps([r.name for r in rules])
        db.commit()
        body = "\n".join(r.remediation.strip() for r in rules)
        dev = db.scalars(select(Device).options(joinedload(Device.credential), joinedload(Device.group))
                         .where(Device.id == dev_id)).first()
        before_id = before.id

        # 2) lo mismo que revisa una solicitud de cambio antes de ejecutarse sola
        why = []
        ex = services.example_addresses(body)
        if ex:
            why.append(_f("Los comandos tienen direcciones de ejemplo ({0}).", ", ".join(ex)))
        why += services.lockout_risks(body, [dev])
        try:
            from . import impact
            imp = impact.for_change(SimpleNamespace(device_ids=json.dumps([dev_id]), variables="{}", kind="config",
                                                    body=body, reload=False))
        except Exception:  # noqa: BLE001
            imp = None
        if imp and imp.get("level") in ("high", "medium", "nodata"):
            why.append(_f("{0}: reinicia el equipo o apaga puertos.", imp.get("label") or _("Impacto")))
        if why:
            _finish(run_id, "blocked", _("No se aplicó: necesita que alguien la revise.") + "\n" + "\n".join(why))
            return "blocked"
        try:
            cmds = services.render_commands(body, dev, {})
        except Exception as e:  # noqa: BLE001
            _finish(run_id, "failed", _f("Error en los comandos de corrección: {0}", e))
            return "failed"

    # 3) aplicar, respaldar y auditar
    out, err = "", None
    try:
        out = services.push_commands(dev_id, cmds)
    except Exception as e:  # noqa: BLE001
        err = str(e) or repr(e)
    services.backup_device(dev_id, "push", WHO)
    with SessionLocal() as db:
        after = _latest(db, dev_id)
        left = _failing(db, dev_id, [r.id for r in rules])
        changed = after is not None and not _same(after, db.get(ConfigVersion, before_id))
        after_id = after.id if after is not None else None
    if err is None and not left:
        _finish(run_id, "applied", (_f("Aplicada: {0} comandos; la regla ya se cumple.", len(cmds)) if len(cmds) != 1 else _("Aplicada: 1 comando; la regla ya se cumple.")), out, after_id)
        return "applied"

    # 4) algo salió mal: volver a la versión de antes
    why = (_f("El equipo rechazó un comando: {0}", err) if err
           else _("Se aplicaron los comandos, pero la regla sigue sin cumplirse."))
    if not changed:
        _finish(run_id, "failed", why + "\n" + _("La configuración quedó igual que antes: no hizo falta revertir."), out, after_id)
        return "failed"
    cmds_rb, notes = services.rollback_commands(dev_id, before_id)
    if cmds_rb is None:
        _finish(run_id, "rollback_failed", why + "\n" + _f("No se puede revertir por comandos: {0}", notes[0] if notes else ""),
                out, after_id)
        return "rollback_failed"
    try:
        out_rb = services.push_commands(dev_id, cmds_rb) if cmds_rb else ""
    except Exception as e:  # noqa: BLE001
        services.backup_device(dev_id, "push", WHO)
        _finish(run_id, "rollback_failed", why + "\n" + _f("La reversión falló: {0}", e), out, after_id)
        return "rollback_failed"
    services.backup_device(dev_id, "push", WHO)
    with SessionLocal() as db:
        back = _same(_latest(db, dev_id), db.get(ConfigVersion, before_id))
    msg = why + "\n" + (_("Se volvió a la configuración de antes.") if back
                        else _("Se revirtió, pero la configuración no quedó idéntica a la de antes: revísela."))
    if notes:
        msg += "\n" + "\n".join(notes)
    _finish(run_id, "rolled_back", msg, out + ("\n\n--- reversión ---\n" + out_rb if out_rb else ""), after_id)
    return "rolled_back"


def _same(a, b) -> bool:
    """¿Misma configuración? Igual huella, o las mismas líneas aunque el equipo las ordene distinto al revertir."""
    if a is None or b is None:
        return False
    if a.id == b.id or a.sha256 == b.sha256:
        return True
    from .crypto import decrypt
    from .drivers import compare_text

    def lines(v):
        return sorted(x.strip() for x in compare_text(decrypt(v.content_enc)).splitlines() if x.strip())
    return lines(a) == lines(b)


def retry(run_id: int) -> bool:
    """«Reintentar»: libera esas reglas en ese equipo para la próxima vuelta."""
    with SessionLocal() as db:
        run = db.get(AutoFixRun, run_id)
        if run is None or run.status == "running":
            return False
        run.hold = False
        db.commit()
    return True
