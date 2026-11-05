"""Equipo › Base de conocimiento: cada caso que se resuelve en la red queda documentado (síntoma, causa, pasos con sus
comandos, comprobación y prevención). Solo para admin y operadores (y solo lectura si el admin lo activa); los permisos
se comprueban en el servidor, y cada lectura y cada edición queda en Auditoría.

Los pendientes («Falta documentar») salen solos: al cerrar una alarma crítica, al revertir un cambio y, si se activa,
al cerrar una sesión de consola de más de 15 minutos. Las contraseñas, claves y comunidades SNMP se ocultan siempre al
guardar."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from .auth import ROLES, Forbidden, require
from .db import (ChangeRequest, Configlet, ConsoleSession, Device, Event, KbCase, KbCaseDevice, KbComment, KbPending,
                 KbProposal, KbVersion, KbVote, SessionLocal, User, audit, get_setting, now, set_setting)
from .drivers import get_profile
from .i18n import N_, _, _f
from .web import flash, go, log_action, render

log = logging.getLogger("opennccm.kb")
router = APIRouter()

CATS = [("respaldos", N_("Respaldos")), ("conectividad", N_("Conectividad")), ("cambios", N_("Cambios y configlets")),
        ("cumplimiento", N_("Cumplimiento")), ("credenciales", N_("Credenciales y acceso")), ("firmware", N_("Firmware"))]
CAT_NAME = dict(CATS)
STATUS = {"draft": N_("Borrador"), "review": N_("En revisión"), "published": N_("Publicado"), "archived": N_("Archivado")}
STATUS_CLS = {"draft": "", "review": "warn", "published": "ok", "archived": ""}
KINDS = {"event": N_("Alarma"), "change": N_("Cambio"), "console": N_("Consola")}
OVERDUE_DAYS = 5
LONG_CONSOLE_MIN = 15
HIDDEN = "••••••"

# reglas de «Acceso y reglas»: clave del ajuste, valor por omisión
RULES = {"kb_rule_critical": "1", "kb_rule_revert": "1", "kb_rule_console": "0", "kb_rule_mail": "1"}


def code(case_id: int) -> str:
    return f"KB-{case_id:04d}"


# --------------------------------------------------------------------------- permisos
def can_see(user) -> bool:
    if user is None:
        return False
    if user.role == "admin":
        return True  # al admin no se le puede quitar
    if user.role == "operator":
        return get_setting("kb_operators", "1") == "1"
    return get_setting("kb_viewers", "0") == "1"


def can_write(user) -> bool:
    return can_see(user) and ROLES.get(user.role, 0) >= ROLES["operator"]


def direct(user) -> bool:
    """Publica sin revisión: el admin siempre; el operador si el admin eligió «Admin y operadores»."""
    return user.role == "admin" or (user.role == "operator" and get_setting("kb_publish", "admin") == "all")


def rule(key: str) -> bool:
    return get_setting(key, RULES[key]) == "1"


def _gate(request: Request, write: bool = False):
    """(usuario, None) o (usuario, página «sin acceso»). Las rutas que escriben responden 403."""
    user = require(request)
    if not can_see(user):
        audit(user.username, "kb_denied", request.url.path)
        return user, render(request, "kb_noaccess.html", user, asked=request.query_params.get("asked") == "1")
    if write and not can_write(user):
        raise Forbidden()
    return user, None


def _write(request: Request):
    user = require(request, "operator")
    if not can_write(user):
        raise Forbidden()
    return user


# --------------------------------------------------------------------------- ocultar secretos
def mask(text: str) -> tuple[str, int]:
    """Oculta contraseñas, claves y comunidades SNMP (las mismas reglas que el asistente y la copia en Git) con ••••••.
    Devuelve el texto y cuántas se ocultaron."""
    from .assistant import redact
    if not text:
        return "", 0
    before = text.count("******")
    out = redact(text)
    # comandos sueltos que las reglas por línea no ven: «password=…», «-p clave», «secret clave» en medio de la línea
    out = re.sub(r"(?i)\b((?:[\w-]*password|passwd|secret|community|psk|passphrase|api[-_]?key|token)\s*[=:]\s*)"
                 r"(?!\*{6}|•{6})(\"[^\"]*\"|\S+)", r"\1******", out)
    n = max(0, out.count("******") - before)
    return out.replace("******", HIDDEN), n


def _clean_steps(raw) -> tuple[list[dict], int]:
    steps, hidden = [], 0
    for s in raw or []:
        t, n1 = mask(str((s or {}).get("t") or "").strip()[:1000])
        c, n2 = mask(str((s or {}).get("c") or "").strip()[:4000])
        if t or c:
            steps.append({"t": t, "c": c, "hid": bool(n2 or (s or {}).get("hid") and HIDDEN in c)})
            hidden += n1 + n2
    return steps[:40], hidden


# --------------------------------------------------------------------------- datos de un caso
def _devices(db, case_id: int) -> list[Device]:
    return list(db.scalars(select(Device).join(KbCaseDevice, KbCaseDevice.device_id == Device.id)
                           .where(KbCaseDevice.case_id == case_id).order_by(Device.name)))


def content(c: KbCase, dev_ids: list[int]) -> dict:
    return {"title": c.title, "category": c.category, "symptom": c.symptom, "cause": c.cause, "steps": json.loads(c.steps or "[]"),
            "verify": c.verify, "prevent": c.prevent, "devices": dev_ids, "vendor": c.vendor, "os_version": c.os_version}


def requirements(data: dict) -> list[tuple[str, bool]]:
    steps = [s for s in data.get("steps") or [] if (s.get("t") or s.get("c"))]
    return [(_("Título y equipo"), bool((data.get("title") or "").strip() and data.get("devices"))),
            (_("Síntoma"), bool((data.get("symptom") or "").strip())),
            (_("Causa"), bool((data.get("cause") or "").strip())),
            (_("Al menos un paso"), bool(steps)),
            (_("Cómo comprobar"), bool((data.get("verify") or "").strip()))]


def _source_label(db, kind: str | None, sid: int | None) -> dict | None:
    if not kind or not sid:
        return None
    if kind == "event":
        e = db.get(Event, sid)
        if e is None:
            return None
        from .web import _event_title
        return {"k": "event", "l": _("Alarma"), "t": f"A-{e.id} · {_event_title(e)}",
                "url": f"/devices/{e.device_id}" if e.device_id else "/events"}
    if kind == "change":
        cr = db.get(ChangeRequest, sid)
        return cr and {"k": "change", "l": _("Cambio"), "t": f"CR-{cr.id:04d} · {cr.title}", "url": f"/changes/{cr.id}"}
    if kind == "console":
        cs = db.get(ConsoleSession, sid)
        return cs and {"k": "console", "l": _("Consola"), "t": _f("Grabación de {0} min · {1}", _minutes(cs), cs.device_name),
                       "url": f"/console?rec={cs.id}#grabaciones"}
    return None


def _minutes(cs: ConsoleSession) -> int:
    return max(1, int(((cs.ended_at or now()) - cs.started_at).total_seconds() // 60))


def _origins(db, c: KbCase) -> list[dict]:
    out = [o for o in (_source_label(db, c.source_kind, c.source_id),) if o]
    if c.console_id and not (c.source_kind == "console" and c.source_id == c.console_id):
        o = _source_label(db, "console", c.console_id)
        if o:
            out.append(o)
    return out


def _row(db, c: KbCase, votes: dict) -> dict:
    devs = _devices(db, c.id)
    return {"id": c.id, "code": code(c.id), "title": c.title or _("(sin título)"), "cat": _(CAT_NAME.get(c.category, "")) if c.category else "",
            "vendor": c.vendor or "", "when": c.published_at or c.updated_at, "devs": ", ".join(d.name for d in devs[:3]) + (" …" if len(devs) > 3 else ""),
            "author": c.author, "status": c.status, "st": _(STATUS[c.status]), "cls": STATUS_CLS[c.status], "votes": votes.get(c.id, 0)}


def _vote_counts(db) -> dict:
    return dict(db.execute(select(KbVote.case_id, func.count()).group_by(KbVote.case_id)).all())


def _visible(user, stmt):
    """Los borradores son de su autor (y del admin); lo archivado, solo del admin."""
    if user.role == "admin":
        return stmt
    return stmt.where(or_(KbCase.status.in_(["published", "review"]), KbCase.author == user.username)) \
        .where(KbCase.status != "archived")


def _get_case(db, user, case_id: int) -> KbCase:
    c = db.get(KbCase, case_id)
    if c is None or (user.role != "admin" and (c.status == "archived" or (c.status == "draft" and c.author != user.username))):
        raise Forbidden()
    return c


def _pending_n(db, user) -> int:
    return db.scalar(select(func.count(KbPending.id)).where(KbPending.state == "open")) or 0


def nav_count(user) -> int | None:
    """Insignia ámbar del menú (None si el usuario no ve la sección)."""
    try:
        if not can_see(user):
            return None
        with SessionLocal() as db:
            return _pending_n(db, user)
    except Exception:  # noqa: BLE001 - un contador nunca impide mostrar la página
        return None


# --------------------------------------------------------------------------- pestaña Casos
def _stats(db) -> list[dict]:
    pub = db.scalar(select(func.count(KbCase.id)).where(KbCase.status == "published")) or 0
    cats = db.scalar(select(func.count(func.distinct(KbCase.category))).where(KbCase.status == "published")) or 0
    open_q = select(KbPending).where(KbPending.state == "open").order_by(KbPending.created_at)
    pend = list(db.scalars(open_q))
    month0 = now().replace(day=1, hour=0, minute=0, second=0)
    since = now() - timedelta(days=30)
    ev_total = db.scalar(select(func.count(KbPending.id)).where(KbPending.kind == "event", KbPending.created_at >= since)) or 0
    ev_done = db.scalar(select(func.count(KbPending.id)).where(KbPending.kind == "event", KbPending.created_at >= since,
                                                              KbPending.state.in_(["documented", "linked"]))) or 0
    solved = db.scalar(select(func.count(KbPending.id)).where(KbPending.decided_at >= month0,
                                                             KbPending.state.in_(["documented", "linked"]))) or 0
    pct = f"{round(ev_done * 100 / ev_total)} %" if ev_total else "—"
    oldest = _f("el más viejo: {0}", _ago(pend[0].created_at)) if pend else _("todo al día")
    return [{"l": _("Casos publicados"), "v": pub, "d": _("en 1 categoría") if cats == 1 else _f("en {0} categorías", cats), "cls": ""},
            {"l": _("Falta documentar"), "v": len(pend), "d": oldest, "cls": "warn" if pend else "ok"},
            {"l": _("Alarmas con caso"), "v": pct, "d": _("meta 95 % · últimos 30 días"), "cls": ""},
            {"l": _("Resueltas con un caso"), "v": solved, "d": _("este mes"), "cls": "ok"}]


def _ago(t) -> str:
    if t is None:
        return ""
    s = (now() - t).total_seconds()
    if s < 3600:
        return _f("hace {0} min", max(1, int(s // 60)))
    if s < 86400:
        return _f("hace {0} h", int(s // 3600))
    d = int(s // 86400)
    return _("ayer") if d == 1 else _f("hace {0} días", d)


def _overdue(t) -> bool:
    return t is not None and (now() - t).days >= OVERDUE_DAYS


def _top_month(db) -> list[dict]:
    """Más consultados este mes: las lecturas registradas en Auditoría."""
    from .db import AuditLog
    month0 = now().replace(day=1, hour=0, minute=0, second=0)
    rows = db.execute(select(AuditLog.detail, func.count()).where(AuditLog.action == "kb_read", AuditLog.created_at >= month0)
                      .group_by(AuditLog.detail)).all()
    views: dict[int, int] = {}
    for det, n in rows:  # «KB-0004» y «KB-0004 v1» (una versión anterior) son el mismo caso
        m = re.match(r"KB-(\d+)", det or "")
        if m:
            views[int(m.group(1))] = views.get(int(m.group(1)), 0) + n
    out = []
    for cid, n in sorted(views.items(), key=lambda x: -x[1]):
        c = db.get(KbCase, cid)
        if c is not None and c.status == "published":
            out.append({"id": c.id, "code": code(c.id), "title": c.title, "n": n})
        if len(out) == 4:
            break
    return out


def _search(stmt, q: str):
    like = f"%{q.strip()}%"
    dev_ids = select(KbCaseDevice.case_id).join(Device, Device.id == KbCaseDevice.device_id) \
        .where(or_(Device.name.ilike(like), Device.host.ilike(like)))
    return stmt.where(or_(KbCase.title.ilike(like), KbCase.symptom.ilike(like), KbCase.cause.ilike(like),
                          KbCase.steps.ilike(like), KbCase.verify.ilike(like), KbCase.id.in_(dev_ids),
                          *([KbCase.id == int(q.strip()[3:])] if re.fullmatch(r"(?i)kb-\d+", q.strip()) else [])))


@router.get("/kb")
def kb_list(request: Request, cat: str = "", st: str = "", q: str = ""):
    user, deny = _gate(request)
    if deny:
        return deny
    with SessionLocal() as db:
        stmt = _visible(user, select(KbCase))
        if cat in CAT_NAME:
            stmt = stmt.where(KbCase.category == cat)
        if st in ("published", "review", "draft") or (st == "archived" and user.role == "admin"):
            stmt = stmt.where(KbCase.status == st)
        elif user.role == "admin":
            stmt = stmt.where(KbCase.status != "archived")
        if q.strip():
            stmt = _search(stmt, q)
        votes = _vote_counts(db)
        cases = [_row(db, c, votes) for c in db.scalars(stmt.order_by(KbCase.updated_at.desc()).limit(300))]
        pend = [_pend_row(db, p, user) for p in db.scalars(select(KbPending).where(KbPending.state == "open")
                                                            .order_by(KbPending.created_at.desc()).limit(3))]
        ctx = _common(db, user)
        stats, top = _stats(db), _top_month(db)
    if q.strip():
        log_action(request, user, "kb_search", q[:200])
    return render(request, "kb.html", user, tab="casos", cases=cases, stats=stats, top=top, pend_top=pend, cats=CATS,
                  cat=cat, st=st, q=q, **ctx)


def _common(db, user) -> dict:
    return {"n_pend": _pending_n(db, user),
            "n_rev": (db.scalar(select(func.count(KbCase.id)).where(KbCase.status == "review")) or 0)
            + (db.scalar(select(func.count(KbProposal.id)).where(KbProposal.status == "review")) or 0),
            "visibility": _("Visible para todo el equipo interno") if get_setting("kb_viewers", "0") == "1" else _("Solo admin y operadores"),
            "can_write": can_write(user), "is_admin": user.role == "admin"}


# --------------------------------------------------------------------------- Falta documentar
def _pend_row(db, p: KbPending, user) -> dict:
    d = db.get(Device, p.device_id) if p.device_id else None
    return {"id": p.id, "k": p.kind, "kl": _(KINDS[p.kind]), "src": {"event": "A-", "change": "CR-", "console": "S-"}[p.kind] + str(p.source_id),
            "title": p.title, "detail": p.detail, "dev": d.name if d else "", "who": p.assignee, "ago": _ago(p.created_at),
            "late": _overdue(p.created_at), "case_id": p.case_id, "mine": p.assignee == user.username}


@router.get("/kb/pending")
def kb_pending(request: Request):
    user, deny = _gate(request)
    if deny:
        return deny
    with SessionLocal() as db:
        rows = [_pend_row(db, p, user) for p in db.scalars(select(KbPending).where(KbPending.state == "open")
                                                            .order_by(KbPending.created_at.desc()).limit(500))]
        by = db.execute(select(KbPending.assignee, func.count()).where(KbPending.state == "open").group_by(KbPending.assignee)
                        .order_by(func.count().desc())).all()
        published = [{"id": c.id, "code": code(c.id), "title": c.title}
                     for c in db.scalars(select(KbCase).where(KbCase.status == "published").order_by(KbCase.title))]
        ctx = _common(db, user)
    return render(request, "kb.html", user, tab="pend", pend=rows, by_person=by, published=published, **ctx)


def _decide(request: Request, pid: int, state: str, case_id: int | None = None):
    user = _write(request)
    with SessionLocal() as db:
        p = db.get(KbPending, pid)
        if p is None or p.state != "open":
            return None, user
        if case_id is not None:
            c = db.get(KbCase, case_id)
            if c is None or c.status != "published":
                raise Forbidden()
        p.state, p.case_id, p.decided_by, p.decided_at = state, case_id, user.username, now()
        db.commit()
        db.expunge(p)
    return p, user


@router.post("/kb/pending/{pid:int}/link")
async def kb_pending_link(request: Request, pid: int):
    form = await request.form()
    cid = int(re.sub(r"\D", "", str(form.get("case_id") or "")) or 0)
    p, user = _decide(request, pid, "linked", cid)
    if p is not None:
        log_action(request, user, "kb_pending_link", f"{p.kind}:{p.source_id} -> {code(cid)}")
        flash(request, _f("{0} enlazado a {1}.", p.title, code(cid)))
    return go("/kb/pending")


@router.post("/kb/pending/{pid:int}/dismiss")
def kb_pending_dismiss(request: Request, pid: int):
    user = require(request, "admin")  # «No hace falta» solo lo decide un admin, y queda quién
    p, _u = _decide(request, pid, "dismissed")
    if p is not None:
        log_action(request, user, "kb_pending_dismiss", f"{p.kind}:{p.source_id} · {p.title}")
        flash(request, _f("«{0}» marcado como «no hace falta». Queda registrado quién lo decidió.", p.title))
    return go("/kb/pending")


# --------------------------------------------------------------------------- En revisión
def _changes(old: dict | None, new: dict, hidden: int = 0) -> list[dict]:
    """Etiquetas de la tarjeta de revisión: qué cambió respecto de lo publicado (o qué trae un caso nuevo)."""
    out = []
    steps = [s for s in new.get("steps") or [] if s.get("t") or s.get("c")]
    if old is None:
        out.append({"t": _f("{0} pasos", len(steps)), "cls": ""})
    else:
        diff = len(steps) - len([s for s in old.get("steps") or [] if s.get("t") or s.get("c")])
        if diff:
            out.append({"t": (f"+ {diff} " if diff > 0 else f"− {-diff} ") + (_("paso") if abs(diff) == 1 else _("pasos")), "cls": "ok"})
        for k, label in (("title", N_("Título cambiado")), ("symptom", N_("Síntoma reescrito")), ("cause", N_("Causa reescrita")),
                         ("verify", N_("Comprobación reescrita")), ("prevent", N_("Prevención reescrita"))):
            if (old.get(k) or "") != (new.get(k) or ""):
                out.append({"t": _(label), "cls": "info"})
        if diff == 0 and json.dumps(old.get("steps")) != json.dumps(new.get("steps")):
            out.append({"t": _("Pasos editados"), "cls": "info"})
    n_hid = hidden or sum(1 for s in steps if s.get("hid")) + sum((new.get(k) or "").count(HIDDEN) for k in ("symptom", "cause", "verify", "prevent"))
    out.append({"t": _f("{0} contraseña ocultada", n_hid) if n_hid == 1 else _f("{0} contraseñas ocultadas", n_hid), "cls": "warn"}
               if n_hid else {"t": _("Sin datos sensibles"), "cls": ""})
    return out


@router.get("/kb/review")
def kb_review(request: Request):
    user, deny = _gate(request)
    if deny:
        return deny
    with SessionLocal() as db:
        cards = []
        stmt = select(KbCase).where(KbCase.status == "review").order_by(KbCase.updated_at)
        if user.role != "admin":
            stmt = stmt.where(KbCase.author == user.username)
        for c in db.scalars(stmt):
            ids = [d.id for d in _devices(db, c.id)]
            tags = _changes(None, content(c, ids))
            tags += [{"t": _f("Enlazado a {0}", o["t"].split(" · ")[0]), "cls": "crit"} for o in _origins(db, c)]
            cards.append({"id": c.id, "code": code(c.id), "title": c.title, "who": c.author, "when": c.updated_at,
                          "kind": _("Caso nuevo"), "tags": tags, "prop": None})
        pstmt = select(KbProposal).where(KbProposal.status == "review").order_by(KbProposal.created_at)
        if user.role != "admin":
            pstmt = pstmt.where(KbProposal.author == user.username)
        for p in db.scalars(pstmt):
            c = db.get(KbCase, p.case_id)
            if c is None:
                continue
            new = json.loads(p.data)
            cards.append({"id": c.id, "code": code(c.id), "title": new.get("title") or c.title, "who": p.author, "when": p.created_at,
                          "kind": _("Cambio en caso publicado"), "tags": _changes(content(c, [d.id for d in _devices(db, c.id)]), new),
                          "prop": p.id})
        ctx = _common(db, user)
    return render(request, "kb.html", user, tab="rev", cards=cards, **ctx)


def _publish(db, c: KbCase, by: str, note: str):
    c.status, c.published_at, c.updated_at, c.reviewed_by, c.review_comment = "published", now(), now(), by, None
    n = (db.scalar(select(func.max(KbVersion.n)).where(KbVersion.case_id == c.id)) or 0) + 1
    ids = [d.id for d in _devices(db, c.id)]
    db.add(KbVersion(case_id=c.id, n=n, data=json.dumps(content(c, ids), ensure_ascii=False), note=note[:255], author=by))
    for p in db.scalars(select(KbPending).where(KbPending.case_id == c.id, KbPending.state == "open")):
        p.state, p.decided_by, p.decided_at = "documented", c.author, now()


def _notify(username: str, text: str, link: str = ""):
    """Aviso a una persona: por correo si tiene uno (la campana de Alarmas es para la red, no para esto)."""
    from . import services
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.username == username))
        email = u.email if u else None
    if email:
        services.pool.submit(services.send_mail, [email], "[OpenNCCM] " + _("Base de conocimiento"), text + ("\n" + link if link else ""))


@router.post("/kb/{case_id:int}/approve")
def kb_approve(request: Request, case_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(KbCase, case_id)
        if c is None or c.status != "review":
            return go("/kb/review")
        _publish(db, c, user.username, _("Aprobado y publicado"))
        db.commit()
        author = c.author
    log_action(request, user, "kb_publish", code(case_id))
    _notify(author, _f("{0} publicado por {1}.", code(case_id), user.username))
    flash(request, _f("{0} publicado. Le avisamos a {1}.", code(case_id), author))
    return go("/kb/review")


@router.post("/kb/{case_id:int}/return")
async def kb_return(request: Request, case_id: int):
    user = require(request, "admin")
    form = await request.form()
    comment, _n = mask(str(form.get("comment") or "").strip()[:2000])
    with SessionLocal() as db:
        c = db.get(KbCase, case_id)
        if c is None or c.status != "review":
            return go("/kb/review")
        c.status, c.review_comment, c.reviewed_by, c.updated_at = "draft", comment or _("Revisa el caso."), user.username, now()
        db.commit()
        author = c.author
    log_action(request, user, "kb_return", f"{code(case_id)}: {comment[:300]}")
    _notify(author, _f("{0} vuelve a borrador: {1}", code(case_id), comment))
    flash(request, _f("{0} vuelve a borrador con tu comentario para {1}.", code(case_id), author))
    return go("/kb/review")


@router.post("/kb/proposals/{pid:int}/approve")
def kb_prop_approve(request: Request, pid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        p = db.get(KbProposal, pid)
        c = db.get(KbCase, p.case_id) if p else None
        if p is None or c is None or p.status != "review":
            return go("/kb/review")
        _apply(db, c, json.loads(p.data))
        p.status = "approved"
        _publish(db, c, user.username, _f("Cambio propuesto por {0}", p.author))
        db.commit()
        author, cid = p.author, c.id
    log_action(request, user, "kb_proposal_approve", f"{code(cid)} · {author}")
    _notify(author, _f("Tu cambio en {0} se publicó.", code(cid)))
    flash(request, _f("Cambio en {0} publicado. Le avisamos a {1}.", code(cid), author))
    return go("/kb/review")


@router.post("/kb/proposals/{pid:int}/return")
async def kb_prop_return(request: Request, pid: int):
    user = require(request, "admin")
    form = await request.form()
    comment, _n = mask(str(form.get("comment") or "").strip()[:2000])
    with SessionLocal() as db:
        p = db.get(KbProposal, pid)
        if p is None or p.status != "review":
            return go("/kb/review")
        p.status, p.review_comment = "returned", comment or _("Revisa el cambio.")
        db.commit()
        author, cid = p.author, p.case_id
    log_action(request, user, "kb_proposal_return", f"{code(cid)}: {comment[:300]}")
    _notify(author, _f("Tu cambio en {0} no se publicó: {1}", code(cid), comment))
    flash(request, _f("Se devolvió el cambio en {0} a {1}.", code(cid), author))
    return go("/kb/review")


# --------------------------------------------------------------------------- Acceso y reglas
@router.get("/kb/access")
def kb_access(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        counts = dict(db.execute(select(User.role, func.count()).where(User.active.is_(True)).group_by(User.role)).all())
        ctx = _common(db, user)
    return render(request, "kb.html", user, tab="acc", counts=counts, ops=get_setting("kb_operators", "1") == "1",
                  viewers=get_setting("kb_viewers", "0") == "1", publish=get_setting("kb_publish", "admin"),
                  rules={k: rule(k) for k in RULES}, **ctx)


@router.post("/kb/access")
async def kb_access_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    vals = {"kb_operators": "1" if form.get("kb_operators") else "0", "kb_viewers": "1" if form.get("kb_viewers") else "0",
            "kb_publish": "all" if form.get("kb_publish") == "all" else "admin"}
    vals.update({k: "1" if form.get(k) else "0" for k in RULES})
    for k, v in vals.items():
        set_setting(k, v)
    log_action(request, user, "kb_settings", json.dumps(vals))
    flash(request, _("Acceso y reglas guardados."))
    return go("/kb/access")


@router.post("/kb/request-access")
def kb_request_access(request: Request):
    from . import services
    user = require(request)
    if can_see(user):
        return go("/kb")
    audit(user.username, "kb_access_request", request.headers.get("referer", "")[:300])
    services.add_event("info", "Base de conocimiento",
                       _f("{0} pide acceso a la base de conocimiento (Equipo › Base de conocimiento › Acceso y reglas).", user.username))
    return go("/kb?asked=1")


# --------------------------------------------------------------------------- ficha del caso
@router.get("/kb/{case_id:int}")
def kb_case(request: Request, case_id: int, v: int = 0):
    user, deny = _gate(request)
    if deny:
        return deny
    with SessionLocal() as db:
        c = _get_case(db, user, case_id)
        devs = _devices(db, c.id)
        data = content(c, [d.id for d in devs])
        shown_v = None
        if v:
            ver = db.scalar(select(KbVersion).where(KbVersion.case_id == c.id, KbVersion.n == v))
            if ver is not None:
                data, shown_v = json.loads(ver.data), v
                devs = list(db.scalars(select(Device).where(Device.id.in_(data.get("devices") or []))))
        versions = list(db.scalars(select(KbVersion).where(KbVersion.case_id == c.id).order_by(KbVersion.n.desc())))
        comments = list(db.scalars(select(KbComment).where(KbComment.case_id == c.id).order_by(KbComment.id)))
        roles = dict(db.execute(select(User.username, User.role)).all())
        votes = db.scalar(select(func.count()).select_from(KbVote).where(KbVote.case_id == c.id)) or 0
        mine = db.get(KbVote, (c.id, user.username)) is not None
        origins = _origins(db, c)
        own_prop = db.scalar(select(KbProposal).where(KbProposal.case_id == c.id, KbProposal.author == user.username)
                             .order_by(KbProposal.id.desc()).limit(1))
        db.expunge_all()
        ctx = _common(db, user)
    audit(user.username, "kb_read", code(case_id) + (f" v{shown_v}" if shown_v else ""), request.headers.get("x-real-ip"))
    edit_label = _("Editar") if user.role == "admin" or c.author == user.username else _("Proponer cambio")
    return render(request, "kb_case.html", user, tab="casos", c=c, code=code(c.id), data=data, devs=devs, versions=versions,
                  comments=comments, roles=roles, votes=votes, mine=mine, origins=origins, shown_v=shown_v,
                  cat_name=_(CAT_NAME.get(data.get("category") or "", "")) if data.get("category") else "",
                  st=_(STATUS[c.status]), st_cls=STATUS_CLS[c.status], edit_label=edit_label, own_prop=own_prop,
                  cur_v=versions[0].n if versions else 0, **ctx)


@router.post("/kb/{case_id:int}/vote")
def kb_vote(request: Request, case_id: int):
    user, deny = _gate(request)
    if deny:
        raise Forbidden()
    with SessionLocal() as db:
        c = _get_case(db, user, case_id)
        v = db.get(KbVote, (c.id, user.username))
        if v is None:
            db.add(KbVote(case_id=c.id, username=user.username))
        else:
            db.delete(v)
        db.commit()
        n = db.scalar(select(func.count()).select_from(KbVote).where(KbVote.case_id == c.id)) or 0
    audit(user.username, "kb_vote", f"{code(case_id)} {'+' if v is None else '-'}")
    return JSONResponse({"ok": True, "n": n, "on": v is None})


@router.post("/kb/{case_id:int}/comment")
async def kb_comment(request: Request, case_id: int):
    user = _write(request)
    form = await request.form()
    text, _n = mask(str(form.get("text") or "").strip()[:4000])
    if text:
        with SessionLocal() as db:
            c = _get_case(db, user, case_id)
            db.add(KbComment(case_id=c.id, username=user.username, text=text))
            db.commit()
            author = c.author
        log_action(request, user, "kb_comment", f"{code(case_id)}: {text[:300]}")
        if author != user.username:
            _notify(author, _f("{0} comentó en {1}: {2}", user.username, code(case_id), text[:500]))
        flash(request, _("Comentario publicado."))
    return go(f"/kb/{case_id}#comentarios")


@router.post("/kb/{case_id:int}/archive")
def kb_archive(request: Request, case_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(KbCase, case_id)
        if c is not None and c.status != "archived":
            c.archived_by, c.status, c.updated_at = f"{user.username}|{c.status}", "archived", now()
            db.commit()
    log_action(request, user, "kb_archive", code(case_id))
    flash(request, _f("{0} archivado. Se puede restaurar desde su ficha.", code(case_id)))
    return go(f"/kb/{case_id}")


@router.post("/kb/{case_id:int}/restore")
def kb_restore(request: Request, case_id: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        c = db.get(KbCase, case_id)
        if c is not None and c.status == "archived":
            prev = (c.archived_by or "|published").split("|")[-1]
            c.status, c.archived_by, c.updated_at = prev if prev in STATUS else "published", None, now()
            db.commit()
    log_action(request, user, "kb_restore", code(case_id))
    flash(request, _f("{0} restaurado.", code(case_id)))
    return go(f"/kb/{case_id}")


@router.post("/kb/{case_id:int}/configlet")
def kb_configlet(request: Request, case_id: int):
    """«Convertir en configlet»: los comandos de los pasos como un configlet nuevo (para revisar antes de usarlo)."""
    user = _write(request)
    with SessionLocal() as db:
        c = _get_case(db, user, case_id)
        cmds = [s["c"] for s in json.loads(c.steps or "[]") if s.get("c") and not s["c"].lower().startswith("sudo opennccm")]
        if not cmds:
            flash(request, _("Este caso no tiene comandos para convertir."), "error")
            return go(f"/kb/{case_id}")
        name, i = f"{code(c.id)} · {c.title}"[:120], 2
        while db.scalar(select(Configlet).where(Configlet.name == name)):
            name, i = f"{code(c.id)} · {c.title}"[:116] + f" ({i})", i + 1
        devs = _devices(db, c.id)
        types = sorted({d.device_type for d in devs})
        hidden = any(HIDDEN in x for x in cmds)
        desc = _f("Creado desde {0} (Base de conocimiento). Revíselo antes de ejecutarlo.", code(c.id)) + \
            (" " + _("Tiene claves ocultas (••••••): escríbalas antes de usarlo.") if hidden else "")
        cf = Configlet(name=name, description=desc, kind="config", body="\n".join(cmds), device_type_filter=",".join(types) or None)
        db.add(cf)
        db.commit()
        cf_id = cf.id
    log_action(request, user, "kb_configlet", f"{code(case_id)} -> configlet {cf_id}")
    flash(request, _f("Se creó el configlet «{0}» con los comandos. Revíselo en Configlets y scripts antes de usarlo.", name))
    return go(f"/configlets/{cf_id}/edit")


# --------------------------------------------------------------------------- editor
def _prefill(db, user, pending: KbPending) -> dict:
    """Lo que ya se sabe al documentar un pendiente: equipo, fecha, alarma o cambio y grabación de consola."""
    data = {"title": "", "category": "", "symptom": "", "cause": "", "steps": [], "verify": "", "prevent": "",
            "devices": [pending.device_id] if pending.device_id else [], "source_kind": pending.kind,
            "source_id": pending.source_id, "console_id": None}
    if pending.kind == "event":
        e = db.get(Event, pending.source_id)
        if e is not None:
            data["title"] = pending.title
            data["symptom"] = mask(e.message or "")[0][:3000]
            data["category"] = "respaldos" if "respaldo" in (e.category or "").lower() else \
                "cumplimiento" if "cumplim" in (e.category or "").lower() else "conectividad"
            # la grabación de consola de quien la resolvió, en ese equipo, desde la alarma
            if e.device_id:
                cs = db.scalar(select(ConsoleSession).where(ConsoleSession.device_id == e.device_id,
                                                            ConsoleSession.username == pending.assignee,
                                                            ConsoleSession.started_at >= e.created_at - timedelta(hours=1),
                                                            ConsoleSession.recorded.is_(True))
                               .order_by(ConsoleSession.id.desc()).limit(1))
                data["console_id"] = cs.id if cs else None
    elif pending.kind == "change":
        cr = db.get(ChangeRequest, pending.source_id)
        if cr is not None:
            data["title"] = pending.title
            data["category"] = "cambios"
            data["devices"] = json.loads(cr.device_ids or "[]")
            data["symptom"] = mask(f"{cr.title}\n{(cr.result or '')[-1500:]}".strip())[0]
            lines = [x.strip() for x in (cr.body or "").splitlines() if x.strip()]
            cmds, n = mask("\n".join(lines[:40]))
            data["steps"] = [{"t": _("Aplicar la reversión"), "c": cmds, "hid": bool(n)}]
            if len(lines) > 40:
                data["steps"].append({"t": _f("… y {0} comandos más (ver {1})", len(lines) - 40, f"CR-{cr.id:04d}"), "c": ""})
    elif pending.kind == "console":
        cs = db.get(ConsoleSession, pending.source_id)
        if cs is not None:
            data["title"] = pending.title
            data["console_id"] = cs.id
    return data


def _editor(request: Request, user, data: dict, case: KbCase | None, mode: str, pending_id: int | None = None):
    from . import assistant
    with SessionLocal() as db:
        devs = {d.id: d.name for d in db.scalars(select(Device).order_by(Device.name))}
        origins = []
        if data.get("source_kind"):
            o = _source_label(db, data["source_kind"], data.get("source_id"))
            if o:
                origins.append(o)
        if data.get("console_id") and data.get("source_kind") != "console":
            o = _source_label(db, "console", data["console_id"])
            if o:
                origins.append(o)
        ctx = _common(db, user)
        rec_min = None
        if data.get("console_id"):
            cs = db.get(ConsoleSession, data["console_id"])
            rec_min = _minutes(cs) if cs else None
    send = _("Publicar") if direct(user) else _("Enviar a revisión")
    if mode == "propose":
        send = _("Publicar cambio") if user.role == "admin" else _("Enviar propuesta")
    title = (_f("Editar {0}", code(case.id)) if mode == "edit" else _f("Proponer cambio en {0}", code(case.id))) if case else \
        (_f("Nuevo caso desde {0}", origins[0]["t"].split(" · ")[0]) if origins else _("Nuevo caso"))
    payload = {"id": case.id if case else None, "mode": mode, "pending": pending_id, "data": data, "devs": devs,
               "direct": direct(user) or (mode == "propose" and user.role == "admin"),
               "ai": bool(assistant.allowed(user)), "rec": data.get("console_id"), "rec_min": rec_min,
               "t": {"hint_direct": _("Se publica ya y queda visible para admin y operadores."),
                     "hint_review": _("Un admin lo revisa antes de publicarlo."),
                     "hint_missing": _("Completa lo que falta para poder enviarlo."),
                     "saved": _("Guardado automático"), "saving": _("Guardando…"), "draft_saved": _("Borrador guardado. Sigue en «Falta documentar» hasta que lo envíes."),
                     "hidden": _("contraseña ocultada"), "missing": _("Falta:"), "proposed": _("Propuesta del asistente: revísala"),
                     "rec_none": _("La grabación no tiene comandos."), "rec_got": _("Se trajeron {0} comandos de la grabación."),
                     "step": _("Escribe el paso…"), "cmd": _("Comando (opcional)"), "remove": _("Quitar"),
                     "ai_busy": _("El asistente está leyendo…"), "ai_done": _("El asistente propuso lo que faltaba. Revísalo antes de enviar."),
                     "req": [label for label, _ok in requirements({})],
                     "no_similar": _("No hay casos parecidos."), "same": _("título igual"),
                     "dup_q": _("¿Enviarlo igual? Si es el mismo problema, mejor añade un comentario a ese caso."), "ago_s": _("hace {0} s"), "err": _("No se pudo guardar")}}
    return render(request, "kb_edit.html", user, tab="casos", title=title, payload=payload, cats=CATS, origins=origins,
                  send=send, case=case, mode=mode, code=code(case.id) if case else "", **ctx)


@router.get("/kb/new")
def kb_new(request: Request, pending: int = 0):
    user, deny = _gate(request, write=True)
    if deny:
        return deny
    data = {"title": "", "category": "", "symptom": "", "cause": "", "steps": [], "verify": "", "prevent": "", "devices": []}
    with SessionLocal() as db:
        p = db.get(KbPending, pending) if pending else None
        if p is not None and p.state == "open":
            if p.case_id and db.get(KbCase, p.case_id):
                return go(f"/kb/{p.case_id}/edit")  # ya hay un borrador para este pendiente: se sigue con ese
            data = _prefill(db, user, p)
        else:
            pending = 0
    return _editor(request, user, data, None, "new", pending or None)


@router.get("/kb/{case_id:int}/edit")
def kb_edit(request: Request, case_id: int):
    user, deny = _gate(request, write=True)
    if deny:
        return deny
    with SessionLocal() as db:
        c = _get_case(db, user, case_id)
        data = content(c, [d.id for d in _devices(db, c.id)])
        data.update({"source_kind": c.source_kind, "source_id": c.source_id, "console_id": c.console_id,
                     "ai_marks": json.loads(c.ai_marks or "[]"), "review_comment": c.review_comment})
        pid = db.scalar(select(KbPending.id).where(KbPending.case_id == c.id, KbPending.state == "open"))
        db.expunge(c)
    # lo publicado no se pisa con el guardado automático: se edita como propuesta (el admin la publica al momento)
    own = c.author == user.username or user.role == "admin"
    mode = "edit" if c.status in ("draft", "review") and own else "propose"
    if c.status == "archived":
        raise Forbidden()
    return _editor(request, user, data, c, mode, pid)


def _apply(db, c: KbCase, data: dict):
    c.title = data.get("title", "")[:255]
    c.category = data.get("category") if data.get("category") in CAT_NAME else ""
    for k in ("symptom", "cause", "verify", "prevent"):
        setattr(c, k, data.get(k, ""))
    c.steps = json.dumps(data.get("steps") or [], ensure_ascii=False)
    ids = [i for i in data.get("devices") or [] if db.get(Device, i)]
    db.query(KbCaseDevice).filter(KbCaseDevice.case_id == c.id).delete()
    for i in ids:
        db.add(KbCaseDevice(case_id=c.id, device_id=i))
    # fabricante y versión: del primer equipo
    d = db.get(Device, ids[0]) if ids else None
    if d is not None:
        c.vendor, c.os_version = get_profile(d.device_type).vendor, d.os_version
    c.updated_at = now()


def _parse(body: dict) -> tuple[dict, int]:
    steps, hidden = _clean_steps(body.get("steps"))
    data = {"steps": steps}
    for k, n in (("title", 255), ("symptom", 6000), ("cause", 6000), ("verify", 4000), ("prevent", 4000)):
        data[k], h = mask(str(body.get(k) or "").strip()[:n])
        hidden += h
    data["category"] = str(body.get("category") or "")
    data["devices"] = [int(i) for i in body.get("devices") or [] if str(i).isdigit()][:50]
    return data, hidden


@router.post("/kb/save")
async def kb_save(request: Request):
    """Guardado del editor (automático, «Guardar borrador» y enviar). Devuelve lo guardado ya con las claves ocultas."""
    user = _write(request)
    body = await request.json()
    action = body.get("action") if body.get("action") in ("auto", "draft", "send") else "auto"
    data, hidden = _parse(body)
    missing = [label for label, ok in requirements(data) if not ok]
    with SessionLocal() as db:
        cid = int(body.get("id") or 0)
        if action == "send" and not missing and not body.get("allow_dup"):
            dup = same_title(db, data["title"], cid)
            if dup is not None:  # se avisa una vez; si la persona sigue, se envía igual
                return JSONResponse({"ok": False, "id": cid or None, "dup": {"id": dup.id, "code": code(dup.id)},
                                     "error": _f("Ya hay un caso con este título: {0}.", code(dup.id))})
        c = db.get(KbCase, cid) if cid else None
        if c is not None:
            if c.status == "archived" or (c.author != user.username and user.role != "admin"):
                raise Forbidden()
            if c.status == "published":  # cambio en un caso publicado: propuesta, o directo si es el admin
                if action != "send":
                    return JSONResponse({"ok": False, "error": _("Los cambios en un caso publicado se guardan al enviarlos.")})
                if missing:
                    return JSONResponse({"ok": False, "missing": missing})
                if user.role == "admin":
                    _apply(db, c, data)
                    _publish(db, c, user.username, str(body.get("note") or _("Editado"))[:200])
                    db.commit()
                    log_action(request, user, "kb_edit", code(c.id))
                    return JSONResponse({"ok": True, "id": c.id, "status": "published", "url": f"/kb/{c.id}", "data": data,
                                         "msg": _f("{0} actualizado.", code(c.id))})
        if c is None and cid:
            raise Forbidden()
        if body.get("mode") == "propose" or (c is not None and c.status == "published"):
            # «Proponer cambio» de un operador (en un caso ajeno o, sin publicar directo, en uno propio ya publicado)
            target = c or db.get(KbCase, cid)
            if target is None or target.status != "published":
                raise Forbidden()
            if missing:
                return JSONResponse({"ok": False, "missing": missing})
            if direct(user) and target.author == user.username:
                _apply(db, target, data)
                _publish(db, target, user.username, _("Editado por su autor"))
                db.commit()
                log_action(request, user, "kb_edit", code(target.id))
                return JSONResponse({"ok": True, "id": target.id, "status": "published", "url": f"/kb/{target.id}", "data": data,
                                     "msg": _f("{0} actualizado.", code(target.id))})
            db.add(KbProposal(case_id=target.id, data=json.dumps(data, ensure_ascii=False), author=user.username))
            db.commit()
            log_action(request, user, "kb_proposal", code(target.id))
            return JSONResponse({"ok": True, "id": target.id, "status": "proposal", "url": f"/kb/{target.id}", "data": data,
                                 "msg": _f("Propuesta enviada. Un admin la revisa antes de cambiar {0}.", code(target.id))})
        if c is None:
            c = KbCase(author=user.username, status="draft")
            sk = body.get("source_kind")
            if sk in KINDS:
                c.source_kind, c.source_id = sk, int(body.get("source_id") or 0) or None
            c.console_id = int(body.get("console_id") or 0) or None
            db.add(c)
            db.flush()
            pid = int(body.get("pending") or 0)
            p = db.get(KbPending, pid) if pid else None
            if p is not None and p.state == "open" and p.case_id is None:
                p.case_id = c.id
        _apply(db, c, data)
        marks = [m for m in body.get("ai_marks") or [] if m in ("cause", "verify")]
        c.ai_marks = json.dumps(marks) if marks else None
        status, msg = c.status, ""
        if action == "send":
            if missing:
                db.commit()
                return JSONResponse({"ok": False, "id": c.id, "missing": missing, "data": data})
            if direct(user):
                _publish(db, c, user.username, _("Creado y publicado") if c.published_at is None else _("Editado"))
                msg = _f("{0} publicado.", code(c.id))
            else:
                c.status, c.review_comment = "review", None
                msg = _f("{0} enviado a revisión. Un admin lo revisa antes de publicarlo.", code(c.id))
            status = c.status
        db.commit()
        cid = c.id
    if action != "auto":
        log_action(request, user, {"draft": "kb_draft", "send": "kb_send"}[action], f"{code(cid)} · {data['title'][:200]}")
    elif not cid or body.get("id") is None:
        log_action(request, user, "kb_create", code(cid))
    if action == "send" and status == "review":
        from . import services
        services.add_event("info", "Base de conocimiento", _f("{0} espera revisión: {1} ({2})", code(cid), data["title"], user.username),
                           notify=False)
    return JSONResponse({"ok": True, "id": cid, "status": status, "hidden": hidden, "data": data, "missing": missing,
                         "url": f"/kb/{cid}" if action == "send" else f"/kb/{cid}/edit", "msg": msg})


def _norm(title: str) -> str:
    return " ".join(re.findall(r"\w+", (title or "").lower()))


def same_title(db, title: str, exclude: int = 0) -> KbCase | None:
    """Otro caso (publicado, en revisión o borrador) con el mismo título, sin contar mayúsculas ni signos."""
    t = _norm(title)
    if not t:
        return None
    for c in db.scalars(select(KbCase).where(KbCase.status != "archived", KbCase.id != exclude)):
        if _norm(c.title) == t:
            return c
    return None


@router.get("/kb/api/similar")
def kb_similar(request: Request, q: str = "", title: str = "", exclude: int = 0):
    user, deny = _gate(request)
    if deny:
        raise Forbidden()
    words = [w for w in re.findall(r"[\wáéíóúñ.\-/]{4,}", (title + " " + q).lower()) if w not in _STOP][:10]
    tw = {w for w in re.findall(r"[\wáéíóúñ.\-/]{4,}", title.lower()) if w not in _STOP}
    if not words:
        return JSONResponse({"items": []})
    with SessionLocal() as db:
        scored = []
        for c in db.scalars(_visible(user, select(KbCase)).where(KbCase.status.in_(["published", "review"]), KbCase.id != exclude)):
            if title.strip() and _norm(c.title) == _norm(title):
                scored.append((100, c.id, c.title))  # mismo título: siempre primero
                continue
            ct = {w for w in re.findall(r"[\wáéíóúñ.\-/]{4,}", c.title.lower()) if w not in _STOP}
            if tw and ct and len(tw & ct) / len(tw | ct) >= 0.6:  # título casi igual
                scored.append((50, c.id, c.title))
                continue
            hay = f"{c.title} {c.symptom} {c.cause}".lower()
            s = sum(1 for w in words if w in hay)
            if s >= min(2, len(words)):
                scored.append((s, c.id, c.title))
    scored.sort(reverse=True)
    return JSONResponse({"items": [{"id": i, "code": code(i), "title": t, "same": s >= 50} for s, i, t in scored[:4]]})


_STOP = {"para", "como", "esta", "este", "desde", "cuando", "porque", "pero", "with", "from", "that", "this", "after"}


@router.get("/kb/api/rec/{sid:int}")
def kb_rec_commands(request: Request, sid: int):
    """«Traer comandos de la grabación»: los comandos escritos en la sesión, sin repetidos y con las claves ocultas."""
    from .consolecenter import _rec
    user = _write(request)
    _r, ev = _rec(user, sid)
    seen, out = set(), []
    for e in ev:
        if len(e) > 2 and e[1] == "i":
            cmd = str(e[2]).strip()
            if cmd and cmd not in seen and not re.fullmatch(r"(?i)(exit|quit|logout|\?|q)", cmd):
                seen.add(cmd)
                m, n = mask(cmd)
                out.append({"c": m, "hid": bool(n)})
    audit(user.username, "kb_rec_commands", f"sesión {sid}: {len(out)} comandos")
    return JSONResponse({"items": out[:30]})


@router.post("/kb/api/ai")
async def kb_ai(request: Request):
    """«Proponer lo que falta»: el asistente lee el caso (y la grabación) y propone causa y comprobación."""
    from . import assistant
    user = _write(request)
    body = await request.json()
    data, _h = _parse(body)
    rec = []
    sid = int(body.get("console_id") or 0)
    if sid:
        try:
            from .consolecenter import _rec
            _r, ev = _rec(user, sid)
            rec = [("$ " + e[2]) if e[1] == "i" else e[2] for e in ev if len(e) > 2 and e[1] in ("i", "o")]
        except Forbidden:
            rec = []
    lang = "en" if (getattr(user, "lang", "") or "").startswith("en") else "es"
    want = [k for k in ("cause", "verify") if not data.get(k)] or ["cause", "verify"]
    extra = ("Caso de la base de conocimiento (borrador):\n" + json.dumps({k: data[k] for k in ("title", "symptom", "cause", "steps", "verify")},
                                                                           ensure_ascii=False)[:8000]
             + ("\n\nGrabación de consola:\n" + "\n".join(rec)[-10000:] if rec else ""))
    question = ("Propón SOLO los campos " + ", ".join({"cause": "«causa» (por qué pasó)", "verify": "«comprobación» (cómo saber que quedó)"}[k]
                                                       for k in want)
                + ". Responde en JSON {\"cause\": \"…\", \"verify\": \"…\"} sin nada más, en "
                + ("inglés" if lang == "en" else "español") + ", 1 a 3 frases cada uno, sin inventar datos que no estén en el caso.")
    try:
        r = assistant.ask(user, question, int(data["devices"][0]) if data["devices"] else None, extra=extra, lang=lang)
    except (assistant.AiError, Forbidden) as e:
        return JSONResponse({"ok": False, "error": str(e) or _("El asistente no está disponible.")})
    text = "".join(s.get("t", "") for s in r.get("segments") or [])
    m = re.search(r"\{.*\}", text, re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        got = {}
    out = {k: mask(str(got.get(k) or "").strip())[0][:2000] for k in want if got.get(k)}
    return JSONResponse({"ok": bool(out), "fields": out, "error": "" if out else _("El asistente no propuso nada útil.")})


# --------------------------------------------------------------------------- cerrar una alarma crítica
# alarmas críticas que son un estado y no un incidente: se resuelven al corregir la causa (los CVE, al actualizar el
# firmware) y su registro ya está en su página (Vulnerabilidades); no piden un caso para reconocerlas
STATE_CATEGORIES = {"Vulnerabilidad de firmware"}


def needs_case(e: Event, user) -> bool:
    """Con la regla activa, una alarma crítica (un incidente) no se cierra sin un caso nuevo o un enlace a uno existente."""
    return (e is not None and e.severity == "critical" and not e.acknowledged and e.category not in STATE_CATEGORIES
            and rule("kb_rule_critical") and can_write(user))


@router.get("/kb/close/{event_id:int}")
def kb_close(request: Request, event_id: int):
    from .auth import check_device
    from .web import _event_title
    user, deny = _gate(request, write=True)
    if deny:
        return deny
    with SessionLocal() as db:
        e = db.get(Event, event_id)
        if e is None or e.acknowledged:
            return go("/events")
        if e.device_id:
            check_device(user, e.device_id)
        info = {"id": e.id, "title": _event_title(e), "msg": e.message, "dev": e.device.name if e.device else "", "when": e.created_at}
        published = [{"id": c.id, "code": code(c.id), "title": c.title}
                     for c in db.scalars(select(KbCase).where(KbCase.status == "published").order_by(KbCase.updated_at.desc()))]
        ctx = _common(db, user)
    return render(request, "kb_close.html", user, tab="pend", ev=info, published=published, **ctx)


@router.post("/kb/close/{event_id:int}")
async def kb_close_do(request: Request, event_id: int):
    from .auth import check_device
    from .web import _event_title
    user = _write(request)
    form = await request.form()
    how = form.get("how")
    with SessionLocal() as db:
        e = db.get(Event, event_id)
        if e is None or e.acknowledged:
            return go("/events")
        if e.device_id:
            check_device(user, e.device_id)
        cid = int(re.sub(r"\D", "", str(form.get("case_id") or "")) or 0) if how == "link" else 0
        if how == "link" and not (cid and (c := db.get(KbCase, cid)) is not None and c.status == "published"):
            flash(request, _("Elige un caso publicado para enlazarla."), "error")
            return go(f"/kb/close/{event_id:int}")
        if how not in ("doc", "link"):
            return go("/events")
        e.acknowledged = True
        p = _pending_for(db, "event", e.id, e.device_id, _event_title(e), _f("Crítica · cerrada por {0}", user.username), user.username)
        if how == "link":
            p.state, p.case_id, p.decided_by, p.decided_at = "linked", cid, user.username, now()
        db.commit()
        pid = p.id
    log_action(request, user, "events_ack", str(event_id))
    if how == "link":
        log_action(request, user, "kb_pending_link", f"event:{event_id} -> {code(cid)}")
        flash(request, _f("Alarma cerrada y enlazada a {0}.", code(cid)))
        return go("/events")
    flash(request, _("Alarma cerrada. Documenta el caso: queda en «Falta documentar» hasta que lo envíes."))
    return go(f"/kb/new?pending={pid}")


def _pending_for(db, kind, source_id, device_id, title, detail, assignee) -> KbPending:
    p = db.scalar(select(KbPending).where(KbPending.kind == kind, KbPending.source_id == source_id))
    if p is None:
        p = KbPending(kind=kind, source_id=source_id, device_id=device_id, title=title[:255], detail=detail[:2000], assignee=assignee)
        db.add(p)
        db.flush()
    return p


# --------------------------------------------------------------------------- pendientes que salen solos
def on_rollback_done(cr_id: int):
    """Al terminar una reversión: pendiente con el diff y los equipos (regla «Al revertir un cambio»)."""
    try:
        if not rule("kb_rule_revert"):
            return
        with SessionLocal() as db:
            cr = db.get(ChangeRequest, cr_id)
            if cr is None or not cr.is_rollback or cr.status not in ("done", "partial"):
                return
            ids = json.loads(cr.device_ids or "[]")
            n = len(ids)
            _pending_for(db, "change", cr.id, ids[0] if ids else None, cr.title,
                         _f("Revertido en {0} equipo", n) if n == 1 else _f("Revertido en {0} equipos", n),
                         cr.requested_by)
            db.commit()
    except Exception:  # noqa: BLE001 - la base de conocimiento nunca rompe un cambio
        log.exception("kb: pendiente de reversión %s", cr_id)


def on_console_closed(sid: int):
    """Sesión de consola de más de 15 minutos (regla apagada por omisión)."""
    try:
        if not rule("kb_rule_console"):
            return
        with SessionLocal() as db:
            cs = db.get(ConsoleSession, sid)
            if cs is None or cs.ended_at is None or (cs.ended_at - cs.started_at).total_seconds() < LONG_CONSOLE_MIN * 60:
                return
            _pending_for(db, "console", cs.id, cs.device_id, _f("Sesión de {0} min en {1}", _minutes(cs), cs.device_name),
                         _("Grabada") if cs.recorded else _("Sin grabación"), cs.username)
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("kb: pendiente de consola %s", sid)


def overdue_check():
    """Diario: un pendiente con más de 5 días sin caso avisa al admin (una vez)."""
    from . import services
    with SessionLocal() as db:
        late = list(db.scalars(select(KbPending).where(KbPending.state == "open", KbPending.overdue_sent.is_(False),
                                                       KbPending.created_at <= now() - timedelta(days=OVERDUE_DAYS))))
        for p in late:
            p.overdue_sent = True
        db.commit()
    for p in late:
        services.add_event("warning", "Base de conocimiento",
                           _f("Falta documentar hace más de {0} días: {1} (asignado a {2}).", OVERDUE_DAYS, p.title, p.assignee),
                           p.device_id)


def weekly_mail():
    """Lunes: a cada persona, lo que le falta documentar (si tiene correo en su cuenta)."""
    from . import services
    if not rule("kb_rule_mail"):
        return
    with SessionLocal() as db:
        rows = list(db.scalars(select(KbPending).where(KbPending.state == "open").order_by(KbPending.created_at)))
        emails = dict(db.execute(select(User.username, User.email).where(User.active.is_(True))).all())
    by: dict[str, list[KbPending]] = {}
    for p in rows:
        by.setdefault(p.assignee, []).append(p)
    for who, items in by.items():
        if emails.get(who):
            body = _("Te falta documentar en la base de conocimiento:") + "\n\n" + \
                "\n".join(f"- {_(KINDS[p.kind])}: {p.title} ({_ago(p.created_at)})" for p in items) + \
                "\n\n" + _("Equipo › Base de conocimiento › Falta documentar")
            services.pool.submit(services.send_mail, [emails[who]], "[OpenNCCM] " + _f("{0} casos por documentar", len(items)), body)


__all__ = ["router", "can_see", "can_write", "needs_case", "nav_count", "on_rollback_done", "on_console_closed",
           "overdue_check", "weekly_mail", "mask"]
