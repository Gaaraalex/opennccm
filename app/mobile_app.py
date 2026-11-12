"""La app Android con servidor: cada pantalla de la app lee aquí lo mismo que su página web (/api/mobile/…), y las
acciones van a las mismas rutas de la web a través del puente /api/mobile/act/…

El puente (MobileBridge) toma la petición del teléfono con su token («Authorization: Bearer ncm_…» de una sesión de la
app), la pasa a la ruta web de la lista ACT con ese usuario (mismos roles, grupos y Auditoría que en la web) y devuelve
el resultado en JSON: los avisos que la web mostraría («flash») y si salió bien. Así una acción del teléfono hace
exactamente lo mismo que el botón de la web. Solo pasan las rutas de la lista: el resto de la web no se abre con tokens.
"""
import json
import re
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from . import __version__, services
from .auth import ROLES, check_device, device_scope, require
from .crypto import decrypt, try_decrypt
from .db import (ApiToken, AuditLog, ChangeRequest, ComplianceResult, ConfigVersion, ConsoleAccessRequest, Configlet,
                 Credential, Device, DeviceGroup, Event, FirmwareImage, NetNode, Policy, Schedule, SessionLocal, User,
                 Vulnerability, get_setting, now)
from .drivers import PROFILE_MAP, get_profile
from .i18n import _

router = APIRouter()


def _iso(d):
    return d.isoformat() if d else None


def _visible(db, user):
    scope = device_scope(user)
    return [d for d in db.scalars(select(Device).where(Device.retired_at.is_(None)).order_by(Device.name))
            if scope is None or d.id in scope]


def _ago_names(devs) -> str:
    return ", ".join(d.name for d in devs[:2]) + (f" +{len(devs) - 2}" if len(devs) > 2 else "")


# =========================================================================== puente para las acciones
# (método, expresión de la ruta web) que el teléfono puede usar con su token
ACT = [(m, re.compile(p)) for m, p in (
    ("POST", r"/devices/\d+/(backup|compliance|cve|mikrotik-backup)"),
    ("POST", r"/devices/bulk"), ("POST", r"/devices/new"),
    ("POST", r"/versions/\d+/(baseline|rollback|label)"),
    ("POST", r"/config-reference/set"),
    ("POST", r"/events/(ack|unack)"),
    ("POST", r"/backup/all"), ("POST", r"/cve/all"), ("POST", r"/compliance/run"),
    ("POST", r"/compliance/remediate(/\d+|-policy|-rule)"),
    ("POST", r"/firmware/updates/check"), ("POST", r"/firmware/upgrade"), ("POST", r"/firmware/updates/\d+/download"),
    ("POST", r"/configlets/\d+/run"),
    ("POST", r"/console/(multi|tools|tools/stop|change-request|recipes/used)"),
    ("POST", r"/console/access/\d+/(approve|reject|revoke)"), ("POST", r"/console/sessions/\d+/share"),
    ("POST", r"/schedules"), ("POST", r"/schedules/\d+/\w+"),
    ("POST", r"/reports/email"), ("POST", r"/reports/netdoc"),
    ("POST", r"/neighbors/\d+/(known|unknown|forget)"),
    ("POST", r"/users"), ("POST", r"/users/\d+/(edit|delete|2fa-reset)"),
    ("POST", r"/account"), ("POST", r"/account/tokens/\d+/delete"),
    ("POST", r"/kb/save"),
    ("GET", r"/console/rec/\d+"), ("GET", r"/reports/[a-z_]+\.csv"), ("GET", r"/ipam\.csv"),
    ("GET", r"/reports/mapinv/(pdf|csv)"), ("GET", r"/reports/unused/csv"),
    ("GET", r"/firmware/updates/status"), ("GET", r"/devices/\d+/cve/status"),
)]
PREFIX = "/api/mobile/act"


def _act_allowed(method: str, path: str) -> bool:
    return any(m == method and rx.fullmatch(path) for m, rx in ACT)


class MobileBridge:
    """ASGI: /api/mobile/act/<ruta web> con el token de la app → la ruta web, con ese usuario; respuesta en JSON."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(PREFIX + "/"):
            return await self.app(scope, receive, send)
        path = scope["path"][len(PREFIX):]
        if not _act_allowed(scope["method"], path):
            return await _json(send, {"error": "not_allowed", "detail": "Esta acción no está disponible en la app."}, 404)
        uid = _bearer_uid(dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1"))
        if uid is None:
            return await _json(send, {"error": "unauthorized", "detail": "Falta un token de la app válido."}, 401)
        scope = dict(scope, path=path, raw_path=path.encode())
        scope["ncm_mobile_uid"] = uid
        scope.setdefault("state", {})["api_token"] = True
        session = scope.get("session")
        if isinstance(session, dict):
            session.pop("flash", None)
        held: dict = {}

        async def wrap(msg):
            if msg["type"] == "http.response.start":
                hdrs = {k.lower(): v for k, v in msg.get("headers", [])}
                ctype = hdrs.get(b"content-type", b"").decode()
                if msg["status"] in (301, 302, 303, 307) or ctype.startswith("text/html"):
                    held["start"] = msg
                    held["loc"] = hdrs.get(b"location", b"").decode()
                    return
                return await send(msg)
            if msg["type"] == "http.response.body" and "start" in held:
                if msg.get("more_body"):
                    return
                st = held["start"]["status"]
                flashes = (session.pop("flash", None) if isinstance(session, dict) else None) or []
                msgs = [{"kind": k, "text": t} for k, t in flashes]
                if held["loc"].startswith("/login"):
                    return await _json(send, {"error": "unauthorized", "detail": "La sesión de la app caducó."}, 401)
                if st >= 400:
                    return await _json(send, {"ok": False, "status": st, "messages": msgs}, st)
                ok = not any(m["kind"] == "error" for m in msgs)
                return await _json(send, {"ok": ok, "messages": msgs, "location": held["loc"] or None})
            return await send(msg)

        return await self.app(scope, receive, wrap)


async def _json(send, data, status=200):
    body = json.dumps(data, default=str).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _bearer_uid(auth: str) -> int | None:
    import hashlib
    if not auth.lower().startswith("bearer "):
        return None
    digest = hashlib.sha256(auth[7:].strip().encode()).hexdigest()
    with SessionLocal() as db:
        tok = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
        if tok is None or tok.client != "android" or (tok.expires_at and tok.expires_at < now()):
            return None
        user = db.get(User, tok.user_id)
        return user.id if user is not None and user.active else None


# =========================================================================== panel
@router.get("/api/mobile/panel")
def panel(request: Request):
    user = require(request)
    with SessionLocal() as db:
        devs = _visible(db, user)
        ids = {d.id for d in devs}
        since = now() - timedelta(hours=24)
        week = now() - timedelta(days=7)
        first = dict(db.execute(select(ConfigVersion.device_id, func.min(ConfigVersion.created_at))
                                .group_by(ConfigVersion.device_id)).all())
        err = [d for d in devs if d.last_status == "error"]
        drift = [d for d in devs if d.baseline_drift]
        backed = sum(1 for d in devs if d.last_status == "ok" and d.last_backup_at and d.last_backup_at >= since)
        res = [r for r in db.scalars(select(ComplianceResult)) if r.device_id in ids]
        comp = round(100 * sum(r.passed for r in res) / len(res)) if res else None
        crit = [v for v in db.scalars(select(Vulnerability).where(Vulnerability.severity == "CRITICAL")) if v.device_id in ids]
        crit_devs: dict[int, list] = {}
        for v in crit:
            crit_devs.setdefault(v.device_id, []).append(v.cve_id)
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
        alarms = sum(1 for e in db.scalars(select(Event).where(Event.acknowledged.is_(False), Event.severity != "info"))
                     if e.device_id is None or e.device_id in ids)
        by_id = {d.id: d for d in devs}
        attention = [{"kind": "backup", "device_id": d.id, "name": d.name,
                      "text": (d.last_error or "").splitlines()[0][:160] if d.last_error else ""} for d in err[:6]]
        attention += [{"kind": "cve", "device_id": i, "name": by_id[i].name, "n": len(set(c)),
                       "version": by_id[i].os_version or ""} for i, c in list(crit_devs.items())[:4] if i in by_id]
        recent = []
        for v in db.scalars(select(ConfigVersion).where(ConfigVersion.kind == "running").order_by(ConfigVersion.id.desc()).limit(40)):
            if v.device_id in ids and len(recent) < 6:
                recent.append({"device_id": v.device_id, "name": by_id[v.device_id].name, "added": v.added, "removed": v.removed,
                               "at": _iso(v.created_at), "trigger": v.trigger})
        drift_rows = [{"device_id": d.id, "name": d.name} for d in drift[:4]]
        new_week = sum(1 for d in devs if first.get(d.id) and first[d.id] >= week)
    return {"user": user.username, "role": user.role, "server": {"version": __version__},
            "total": len(devs), "new_week": new_week, "backed24": backed, "comp_pct": comp,
            "errors": len(err), "error_names": _ago_names(err), "drift": len(drift), "pending_changes": pending,
            "crit_cves": len({c for c in [v.cve_id for v in crit]}), "crit_devices": len(crit_devs), "alarms": alarms,
            "attention": attention, "recent": recent, "drift_rows": drift_rows,
            "todo": len(err) + pending + len(crit_devs) + len(drift)}


# =========================================================================== equipos
def _dev_row(d, cves: dict, prof=None) -> dict:
    from . import vendorlogos
    prof = prof or PROFILE_MAP.get(d.device_type)
    k = vendorlogos.key(d.device_type)
    return {"id": d.id, "name": d.name, "host": d.host, "port": d.port, "protocol": d.protocol, "type": d.device_type,
            "platform": prof.label if prof else d.device_type, "vendor": prof.vendor if prof else "",
            "brand": k, "brand_name": vendorlogos.name(k), "model": d.model, "version": d.os_version, "serial": d.serial,
            "group": d.group.name if d.group else None, "group_id": d.group_id, "node": d.node.name if d.node else None,
            "node_id": d.node_id, "location": d.location, "status": d.last_status, "enabled": d.enabled is not False,
            "last_error": (d.last_error or "")[:300], "last_backup": _iso(d.last_backup_at), "drift": bool(d.baseline_drift),
            "conflict": bool(d.startup_conflict), "crit_cves": cves.get(d.id, 0),
            "web_url": (d.web_url or f"https://{d.host}/") if d.web_enabled is not False else None}


def _crit_counts(db) -> dict:
    out: dict[int, set] = {}
    for did, cve in db.execute(select(Vulnerability.device_id, Vulnerability.cve_id).where(Vulnerability.severity == "CRITICAL")):
        out.setdefault(did, set()).add(cve)
    return {k: len(v) for k, v in out.items()}


@router.get("/api/mobile/devlist")
def devlist(request: Request):
    """Lista de Dispositivos con lo que filtran los chips (estado, grupo, nodo y marca)."""
    user = require(request)
    with SessionLocal() as db:
        cves = _crit_counts(db)
        devs = _visible(db, user)
        rows = []
        for d in devs:
            d.group, d.node  # carga las relaciones antes de cerrar la sesión
            rows.append(_dev_row(d, cves))
        groups = [{"id": g.id, "name": g.name} for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
        nodes = [{"id": n.id, "name": n.name} for n in db.scalars(select(NetNode).order_by(NetNode.name))]
    brands = Counter((r["brand"], r["brand_name"]) for r in rows)
    from . import vendorlogos
    return {"devices": rows, "groups": groups, "nodes": nodes, "logos_small": vendorlogos.small_on(),
            "brands": [{"key": k, "name": n, "n": c} for (k, n), c in brands.most_common()],
            "counts": {"all": len(rows), "error": sum(r["status"] == "error" for r in rows),
                       "drift": sum(r["drift"] for r in rows),
                       "pending": sum(r["status"] not in ("ok", "error") for r in rows)}}


@router.get("/api/mobile/newdevice")
def newdevice(request: Request):
    """Lo que pide «Agregar equipo»: plataformas, credenciales y grupos."""
    require(request, "admin")
    with SessionLocal() as db:
        creds = [{"id": c.id, "name": c.name} for c in db.scalars(select(Credential).order_by(Credential.name))]
        groups = [{"id": g.id, "name": g.name} for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
    from . import vendorlogos
    plats = sorted(({"key": k, "label": p.label, "vendor": p.vendor, "brand": vendorlogos.key(k)} for k, p in PROFILE_MAP.items()), key=lambda x: x["label"].lower())
    return {"platforms": plats, "credentials": creds, "groups": groups}


@router.get("/api/mobile/device/{dev_id}")
def device(request: Request, dev_id: int):
    """La ficha con sus 7 pestañas: resumen, puertos, versiones, cumplimiento, vulnerabilidades, firmware y eventos."""
    user = require(request)
    check_device(user, dev_id)
    from . import fwupdates, ports as ports_mod
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None or d.retired_at is not None:
            return JSONResponse({"error": "not_found"}, 404)
        d.group, d.node
        cred = d.credential.name if d.credential else None
        out = _dev_row(d, _crit_counts(db))
        versions = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id)
                              .order_by(ConfigVersion.id.desc()).limit(30)).all()
        total = db.scalar(select(func.count(ConfigVersion.id)).where(ConfigVersion.device_id == dev_id)) or 0
        last_run = next((v for v in versions if v.kind == "running"), None)
        pv = ports_mod.view(d, try_decrypt(last_run.content_enc, "") if last_run else "")
        nums = {v.id: total - i for i, v in enumerate(versions)}
        comp = []
        for r in db.scalars(select(ComplianceResult).where(ComplianceResult.device_id == dev_id)):
            comp.append({"id": r.id, "rule": r.rule.name, "policy": r.policy.name, "severity": r.rule.severity,
                         "passed": bool(r.passed), "fix": bool(r.rule.remediation), "at": _iso(r.checked_at)})
        kev = services.kev_ids()
        vulns = [{"cve": v.cve_id, "score": v.score, "severity": (v.severity or "").lower(), "desc": (v.description or "")[:300],
                  "url": v.url, "kev": v.cve_id in kev}
                 for v in db.scalars(select(Vulnerability).where(Vulnerability.device_id == dev_id)
                                     .order_by(Vulnerability.score.desc().nulls_last()))]
        events = [{"id": e.id, "severity": e.severity, "category": _(e.category), "message": (e.message or "")[:400],
                   "at": _iso(e.created_at), "ack": e.acknowledged}
                  for e in db.scalars(select(Event).where(Event.device_id == dev_id).order_by(Event.id.desc()).limit(30))]
        out.update({
            "credential": cred, "baseline": nums.get(d.baseline_version_id) if d.baseline_version_id else None,
            "baseline_id": d.baseline_version_id, "versions_total": total,
            "ports": None if pv is None else {"items": pv["ports"], "status": pv["status"], "at": _iso(pv["at"]),
                                               "layout": pv["layout"], "rear": pv["rear"]},
            "versions": [{"id": v.id, "n": nums[v.id], "kind": v.kind, "at": _iso(v.created_at), "trigger": v.trigger,
                          "user": v.username, "added": v.added, "removed": v.removed, "label": v.label, "size": v.size,
                          "baseline": v.id == d.baseline_version_id, "fw": v.fw_version} for v in versions],
            "compliance": sorted(comp, key=lambda c: (c["passed"], c["policy"], c["rule"])), "vulns": vulns,
            "events": events, "mikrotik": d.device_type.startswith("mikrotik")})
    fw = fwupdates.for_device(dev_id) or {}
    out["firmware"] = {"current": fw.get("current") or out.get("version"), "latest": fw.get("latest"), "target": fw.get("target"),
                       "status": fw.get("status"), "fixed_cves": [c for c, _s in (fw.get("fixed") or [])], "checked_at": _iso(fw.get("checked_at")),
                       "notes_url": fw.get("notes_url")}
    return out


@router.get("/api/mobile/diff")
def diff(request: Request, a: int, b: int):
    """Diferencias entre dos versiones (las de un mismo equipo), línea a línea."""
    user = require(request)
    with SessionLocal() as db:
        va, vb = db.get(ConfigVersion, a), db.get(ConfigVersion, b)
        if not va or not vb:
            return JSONResponse({"error": "not_found"}, 404)
        check_device(user, va.device_id)
        check_device(user, vb.device_id)
        ta, tb = decrypt(va.content_enc), decrypt(vb.content_enc)
        fa, fb = va.fw_version, vb.fw_version
    from . import fwtag
    return {"lines": services.diff_lines(ta, tb)[:4000], "fw_a": fa, "fw_b": fb,
            "fw_diff": bool(fa and fb and not fwtag.same(fa, fb))}


# =========================================================================== alarmas
@router.get("/api/mobile/alarms")
def alarms(request: Request, unack: int = 1, cat: str = "", limit: int = 200):
    from . import nbwatch
    from .web import _event_title
    user = require(request)
    scope = device_scope(user)
    nb_cats = (nbwatch.CAT_NEW, nbwatch.CAT_LOST)
    groups = {"vecinos": tuple(c.lower() for c in nb_cats), "respaldo": ("respaldo",), "cve": ("vulnerabilidad",)}
    with SessionLocal() as db:
        stmt = select(Event).order_by(Event.id.desc())
        if unack:
            stmt = stmt.where(Event.acknowledged.is_(False))
        if scope is not None:
            stmt = stmt.where(Event.device_id.in_(scope))
        rows = list(db.scalars(stmt.limit(3000)))

        def in_group(e, g):
            c = (e.category or "").lower()
            return any(x in c for x in groups[g])
        counts = {g: sum(1 for e in rows if in_group(e, g)) for g in groups}
        shown = [e for e in rows if not cat or (cat in groups and in_group(e, cat))][:min(max(limit, 1), 500)]
        items = [{"id": e.id, "severity": e.severity, "title": _event_title(e), "category": _(e.category),
                  "message": (e.message or "")[:600], "at": _iso(e.created_at), "ack": e.acknowledged,
                  "device_id": e.device_id, "device": e.device.name if e.device else None} for e in shown]
        open_n = db.scalar(select(func.count(Event.id)).where(Event.acknowledged.is_(False),
                                                              *([Event.device_id.in_(scope)] if scope is not None else []))) or 0
    return {"items": items, "open": open_n, "total": len(rows), "counts": counts,
            "nb": {"interval": nbwatch.interval_min(), "reads": nbwatch.cfg()["reads"]}}


# =========================================================================== consola
@router.get("/api/mobile/console")
def console_data(request: Request, q: str = ""):
    from . import console, consolecenter as cc_ui, nettools
    user = require(request)
    if not console.console_allowed(user):
        return JSONResponse({"error": "forbidden", "detail": _("Su usuario no tiene permiso para la consola.")}, 403)
    devs = cc_ui._devices(user)
    recs = cc_ui.recordings(user, q)
    reqs = []
    with SessionLocal() as db:
        if user.role == "admin":
            for r in db.scalars(select(ConsoleAccessRequest).order_by(ConsoleAccessRequest.id.desc()).limit(40)):
                reqs.append({"id": r.id, "user": r.username, "device": r.device.name if r.device else "?", "reason": r.reason,
                             "hours": r.hours, "status": r.status, "at": _iso(r.created_at), "until": _iso(r.until)})
    rec = [{"key": r["key"], "name": r["name"], "os": r.get("os"), "os_label": r.get("os_label") or "",
            "vendor": r.get("vendor", ""), "verified_on": r.get("verified_on") or "", "intro": r.get("intro", ""),
            "vars": r.get("vars", []), "steps": r.get("steps", []), "mine": r.get("mine", False)}
           for r in cc_ui.all_recipes(user)]
    return {"devices": [{"id": d["id"], "name": d["name"], "host": d["host"], "os": d["os"], "vendor": d["vendor"],
                         "platform": d["label"], "status": d["status"], "group": d["group"], "version": d["ver"], "brand": d["vl"]}
                        for d in devs],
            "recipes": rec, "recordings": [{**r, "when": _iso(r["when"])} for r in recs], "requests": reqs,
            "tools": list(getattr(nettools, "HELP", {}).keys()), "is_admin": user.role == "admin",
            "record": get_setting("console_record", "1") == "1"}


def _ws_route():
    """La consola interactiva del teléfono: la misma de la web (/devices/{id}/console/ws, protocolo v2), entrando con
    el token de la app en la cabecera Authorization."""
    from . import console
    router.add_api_websocket_route("/api/mobile/devices/{dev_id}/console/ws", console.console_ws)


_ws_route()


@router.get("/api/mobile/subnet")
def subnet(request: Request, cidr: str):
    """Calculadora de subredes."""
    import ipaddress
    require(request)
    try:
        n = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return JSONResponse({"error": "bad", "detail": _("Escriba una red como 192.168.1.10/24.")}, 400)
    hosts = n.num_addresses - (2 if n.version == 4 and n.prefixlen < 31 else 0)
    first = n.network_address + (1 if n.version == 4 and n.prefixlen < 31 else 0)
    last = n.broadcast_address - (1 if n.version == 4 and n.prefixlen < 31 else 0)
    return {"network": str(n.network_address), "prefix": n.prefixlen, "mask": str(n.netmask),
            "broadcast": str(n.broadcast_address) if n.version == 4 else None, "first": str(first), "last": str(last),
            "hosts": max(hosts, 0), "wildcard": str(n.hostmask)}


@router.get("/api/mobile/where")
def where(request: Request, q: str):
    """Buscar MAC / IP: en qué equipo y puerto está."""
    from . import macfind
    user = require(request)
    return macfind.lookup(user, q)


# =========================================================================== Más
@router.get("/api/mobile/more")
def more(request: Request):
    """Los recuentos que muestra cada entrada de «Más»."""
    from . import kb
    user = require(request)
    with SessionLocal() as db:
        devs = _visible(db, user)
        ids = {d.id for d in devs}
        res = [r for r in db.scalars(select(ComplianceResult)) if r.device_id in ids]
        nbs = sum(1 for e in db.scalars(select(Event).where(Event.acknowledged.is_(False))) if "vecino" in (e.category or "").lower())
        out = {
            "user": user.username, "role": user.role, "server": __version__,
            "pending_changes": db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0,
            "configlets": db.scalar(select(func.count(Configlet.id)).where(Configlet.kind != "script")) or 0,
            "scripts": db.scalar(select(func.count(Configlet.id)).where(Configlet.kind == "script")) or 0,
            "policies": db.scalar(select(func.count(Policy.id))) or 0,
            "comp_pct": round(100 * sum(r.passed for r in res) / len(res)) if res else None,
            "crit_cves": len({c for c, did in db.execute(select(Vulnerability.cve_id, Vulnerability.device_id)
                                                        .where(Vulnerability.severity == "CRITICAL")) if did in ids}),
            "images": db.scalar(select(func.count(FirmwareImage.id))) or 0,
            "devices": len(devs), "neighbor_changes": nbs,
            "kb": kb.can_see(user), "is_admin": user.role == "admin",
            "two_fa": bool(user.totp_enabled)}
    return out


# =========================================================================== buscar
@router.get("/api/mobile/search")
def search(request: Request, q: str = "", regex: int = 0, limit: int = 200):
    user = require(request)
    q = q.strip()
    if len(q) < 2:
        return {"hits": [], "devices": 0}
    try:
        rx = re.compile(q if regex else re.escape(q), re.I)
    except re.error as e:
        return JSONResponse({"error": "bad_regex", "detail": str(e)}, 400)
    hits, devs = [], set()
    with SessionLocal() as db:
        for dev in _visible(db, user):
            v = services.latest_version(db, dev.id)
            if not v:
                continue
            for n, line in enumerate(decrypt(v.content_enc).splitlines(), 1):
                if rx.search(line):
                    hits.append({"device": dev.name, "device_id": dev.id, "line_no": n, "line": line.strip()[:200]})
                    devs.add(dev.id)
                    if len(hits) >= limit:
                        return {"hits": hits, "devices": len(devs), "more": True}
    return {"hits": hits, "devices": len(devs)}


# =========================================================================== configlets y tareas
@router.get("/api/mobile/configlets")
def configlets(request: Request):
    from . import vendorlogos, verified
    from .auth import filter_ids
    user = require(request)
    ver = verified.all_of("configlet")
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).where(Device.enabled.is_(True), Device.retired_at.is_(None)))]
        allowed = set(filter_ids(user, [d.id for d in devs]))
        items = []
        for c in db.scalars(select(Configlet).order_by(Configlet.name)):
            try:
                rx = re.compile(c.device_type_filter) if c.device_type_filter else None
            except re.error:
                rx = None
            mine = [d for d in devs if d.id in allowed and (rx is None or rx.search(d.device_type))]
            plats = sorted({get_profile(d.device_type).label for d in mine})
            items.append({"id": c.id, "name": c.name, "description": c.description or "", "kind": c.kind or "configlet",
                          "body": c.body or "", "filter": c.device_type_filter or "", "devices": len(mine),
                          "device_ids": [d.id for d in mine], "platforms": plats,
                          "brands": sorted({vendorlogos.key(d.device_type) for d in mine}),
                          "vars": sorted(set(re.findall(r"\{\{\s*(\w+)", c.body or ""))),
                          "verified_on": ver.get(str(c.id)) or ver.get(c.id) or ""})
    return {"items": items}


@router.get("/api/mobile/tasks")
def tasks(request: Request):
    from . import cronutil, scheduler
    from .main import SCHEDULE_ACTIONS
    require(request)
    jobs = {j.id: j.next_run_time for j in scheduler.scheduler.get_jobs()}
    with SessionLocal() as db:
        items = []
        for s in db.scalars(select(Schedule).order_by(Schedule.name)):
            nxt = jobs.get(f"sched-{s.id}")
            items.append({"id": s.id, "name": s.name, "action": s.action, "action_label": _(SCHEDULE_ACTIONS.get(s.action, s.action)),
                          "cron": s.cron, "when": cronutil.describe(s.cron), "enabled": s.enabled,
                          "group": s.group.name if s.group else None, "configlet_id": s.configlet_id,
                          "last_run": _iso(s.last_run), "next_run": _iso(nxt)})
        groups = [{"id": g.id, "name": g.name} for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
    return {"items": items, "actions": {k: _(v) for k, v in SCHEDULE_ACTIONS.items()}, "groups": groups}


# =========================================================================== proteger
@router.get("/api/mobile/compliance")
def compliance(request: Request):
    user = require(request)
    with SessionLocal() as db:
        ids = {d.id for d in _visible(db, user)}
        res = [r for r in db.scalars(select(ComplianceResult)) if r.device_id in ids]
        by_pol: dict[int, list] = {}
        for r in res:
            by_pol.setdefault(r.policy_id, []).append(r)
        pols = []
        for p in db.scalars(select(Policy).order_by(Policy.name)):
            rs = by_pol.get(p.id, [])
            pols.append({"id": p.id, "name": p.name, "description": p.description or "", "enabled": p.enabled,
                         "rules": len(p.rules), "checked": len({r.device_id for r in rs}),
                         "pct": round(100 * sum(r.passed for r in rs) / len(rs)) if rs else None,
                         "fails": sum(1 for r in rs if not r.passed)})
        fails = [{"id": r.id, "device_id": r.device_id, "device": r.device.name, "rule": r.rule.name, "policy": r.policy.name,
                  "severity": r.rule.severity, "fix": bool(r.rule.remediation)} for r in res if not r.passed][:300]
        last = db.scalar(select(func.max(ComplianceResult.checked_at)))
    return {"policies": pols, "pct": round(100 * sum(r.passed for r in res) / len(res)) if res else None,
            "fails": fails, "last_audit": _iso(last)}


@router.get("/api/mobile/vulns")
def vulns(request: Request):
    user = require(request)
    kev = services.kev_ids()
    with SessionLocal() as db:
        devs = {d.id: d for d in _visible(db, user)}
        groups: dict[str, dict] = {}
        for v in db.scalars(select(Vulnerability).order_by(Vulnerability.score.desc().nulls_last()).limit(5000)):
            if v.device_id not in devs:
                continue
            g = groups.setdefault(v.cve_id, {"cve": v.cve_id, "score": v.score, "severity": (v.severity or "").lower(),
                                             "desc": (v.description or "")[:300], "url": v.url, "kev": v.cve_id in kev,
                                             "devices": [], "versions": set()})
            d = devs[v.device_id]
            if d.id not in [x["id"] for x in g["devices"]]:
                g["devices"].append({"id": d.id, "name": d.name})
                g["versions"].add(f"{get_profile(d.device_type).label} {d.os_version or ''}".strip())
    rows = sorted(groups.values(), key=lambda g: (not g["kev"], -(g["score"] or 0), g["cve"]))
    for g in rows:
        g["versions"] = sorted(g["versions"])
    return {"items": rows, "critical": sum(1 for g in rows if g["severity"] == "critical"),
            "kev": sum(1 for g in rows if g["kev"]), "affected": len({d["id"] for g in rows for d in g["devices"]})}


@router.get("/api/mobile/firmware")
def firmware(request: Request):
    from . import eol, fwupdates
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        devs = _visible(db, user)
        imgs = []
        for img in db.scalars(select(FirmwareImage).order_by(FirmwareImage.uploaded_at.desc())):
            try:
                n = sum(1 for d in devs if not img.device_type_filter or re.search(img.device_type_filter, d.device_type))
            except re.error:
                n = 0
            imgs.append({"id": img.id, "filename": img.filename, "version": img.version, "size": img.size,
                         "filter": img.device_type_filter, "compatible": n, "at": _iso(img.uploaded_at)})
    ups = [{"device_id": r["device_id"], "name": r["name"], "device_type": r["device_type"], "status": r.get("status"),
            "current": r.get("current"), "target": r.get("target"), "latest": r.get("latest"),
            "download_url": r.get("download_url"), "fixed": len(r.get("fixed") or []), "fixed_crit": r.get("fixed_crit", 0),
            "checked_at": _iso(r.get("checked_at"))} for r in fwupdates.all_rows(scope)]
    life = [{"device_id": d.id, "device": d.name, "model": d.model, "status": st, "label": _(eol.STATUS_LABEL.get(st, st)),
             "end_of_support": _iso(e.end_of_support) if e else None, "replacement": e.replacement if e else None}
            for d, e, st in eol.report(scope)]
    return {"images": imgs, "updates": ups, "lifecycle": life, "state": fwupdates.state()}


# =========================================================================== observar
@router.get("/api/mobile/analytics")
def analytics(request: Request, days: int = 14):
    from . import reports
    user = require(request)
    data = reports.analytics(device_scope(user), max(1, min(days, 365)))
    keep = ("per_day", "by_trigger", "by_user", "by_vendor", "by_device", "compliance_pct", "changes", "devices_changed",
            "drift", "conflicts", "total", "prev_changes", "heat")
    return {k: data[k] for k in keep}


@router.get("/api/mobile/map")
def netmap(request: Request):
    """Los equipos y sus vecinos (LLDP/CDP) para dibujar el mapa en el teléfono."""
    user = require(request)
    from . import topology
    with SessionLocal() as db:
        devs = _visible(db, user)
        names = {topology.norm_name(d.name): d for d in devs}
        nodes, edges, seen = [], [], set()
        for d in devs:
            try:
                nbrs = json.loads(d.neighbors or "[]")
            except ValueError:
                nbrs = []
            nodes.append({"id": d.id, "name": d.name, "type": d.device_type, "role": topology.device_role(d.device_type, d.model, None, d.ports, d.map_role)[0],
                          "status": d.last_status, "node": d.node.name if d.node else None, "neighbors": len(nbrs)})
            for n in nbrs if isinstance(nbrs, list) else []:
                if not isinstance(n, dict):
                    continue
                peer = names.get(topology.norm_name(str(n.get("name") or "")))
                key = tuple(sorted((d.name, peer.name if peer else str(n.get("name") or "?"))))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"a": d.id, "b": peer.id if peer else None, "b_name": None if peer else str(n.get("name") or "?"),
                              "port": str(n.get("local") or ""), "peer_port": str(n.get("port") or "")})
        nb_changes = sum(1 for e in db.scalars(select(Event).where(Event.acknowledged.is_(False)))
                         if "vecino" in (e.category or "").lower())
    return {"nodes": nodes, "edges": edges, "neighbor_changes": nb_changes}


@router.get("/api/mobile/ipam")
def ipam(request: Request, group: int = 0):
    from .main import _ipam_data
    user = require(request)
    rows, dups, vl = _ipam_data(user, group)
    return {"ips": [{"device": r["dev"], "device_id": r.get("dev_id"), "iface": r["iface"], "ip": r["ip"], "prefix": r["prefix"],
                     "net": r["net"], "dup": bool(r["dup"])} for r in rows][:3000],
            "vlans": [{"id": e["id"], "names": e["names"], "devices": [n for _i, n in e["devs"]]} for e in vl][:2000],
            "dups": len(dups)}


@router.get("/api/mobile/reports")
def reports_list(request: Request):
    from . import reports
    user = require(request)
    out = [{"kind": k, "title": _(t), "csv": f"/reports/{k}.csv", "admin": k == "audit"} for k, t in reports.TITLES.items()
           if k != "audit" or user.role == "admin"]
    return {"items": out, "can_email": user.role == "admin"}


@router.get("/api/mobile/report/{kind}")
def report_rows(request: Request, kind: str, days: int = 30):
    from . import reports
    user = require(request, "admin" if kind == "audit" else "viewer")
    if kind not in reports.TITLES:
        return JSONResponse({"error": "not_found"}, 404)
    header, rows = reports.build(kind, device_scope(user), days)
    return {"title": _(reports.TITLES[kind]), "header": header, "rows": [[str(c) if c is not None else "" for c in r] for r in rows[:2000]]}


@router.get("/api/mobile/kb")
def kb_list(request: Request, q: str = ""):
    from . import kb
    from .db import KbCase
    user = require(request)
    if not kb.can_see(user):
        return {"allowed": False, "items": []}
    with SessionLocal() as db:
        stmt = select(KbCase).order_by(KbCase.updated_at.desc().nulls_last(), KbCase.id.desc()).limit(300)
        if q:
            stmt = stmt.where(or_(KbCase.title.ilike(f"%{q}%"), KbCase.symptom.ilike(f"%{q}%")))
        items = [{"id": c.id, "code": kb.code(c.id), "title": c.title, "status": c.status, "category": c.category,
                  "vendor": c.vendor, "author": c.author, "at": _iso(c.updated_at or c.created_at),
                  "symptom": (c.symptom or "")[:300], "cause": (c.cause or "")[:300]} for c in db.scalars(stmt)]
        from .db import KbPending
        pending = db.scalar(select(func.count(KbPending.id))) or 0
    return {"allowed": True, "can_write": kb.can_write(user), "items": items, "pending": pending}


# =========================================================================== administrar y cuenta
@router.get("/api/mobile/admin")
def admin(request: Request):
    user = require(request, "admin")
    from . import updates
    with SessionLocal() as db:
        users = [{"id": u.id, "username": u.username, "role": u.role, "active": u.active, "two_fa": bool(u.totp_enabled),
                  "email": u.email, "last_login": _iso(u.last_login_at), "source": u.auth_source or "local"}
                 for u in db.scalars(select(User).order_by(User.username))]
        creds = [{"id": c.id, "name": c.name, "username": c.username,
                  "devices": db.scalar(select(func.count(Device.id)).where(Device.credential_id == c.id)) or 0}
                 for c in db.scalars(select(Credential).order_by(Credential.name))]
        groups = [{"id": g.id, "name": g.name, "devices": db.scalar(select(func.count(Device.id)).where(Device.group_id == g.id)) or 0}
                  for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
        nodes = [{"id": n.id, "name": n.name, "description": n.description,
                  "devices": db.scalar(select(func.count(Device.id)).where(Device.node_id == n.id)) or 0}
                 for n in db.scalars(select(NetNode).order_by(NetNode.name))]
        audit_rows = [{"at": _iso(a.created_at) if hasattr(a, "created_at") else None, "user": a.username, "action": a.action,
                       "detail": (a.detail or "")[:200], "ip": getattr(a, "ip", None)}
                      for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(100))]
    from . import vendorlogos
    plats = Counter(p.vendor for p in PROFILE_MAP.values())
    vkey = {}
    for k, p in PROFILE_MAP.items():
        vkey.setdefault(p.vendor, vendorlogos.key(k))
    try:
        upd = updates.status()
    except Exception:  # noqa: BLE001
        upd = {}
    return {"users": users, "credentials": creds, "groups": groups, "nodes": nodes, "audit": audit_rows,
            "platforms": {"total": len(PROFILE_MAP), "vendors": [{"vendor": v, "n": n, "key": vkey.get(v, "other")} for v, n in plats.most_common()]},
            "system": {"version": __version__, "update": {k: v for k, v in (upd or {}).items() if isinstance(v, (str, int, bool, type(None)))}},
            "roles": list(ROLES.keys())}


@router.get("/api/mobile/account")
def account(request: Request):
    user = require(request)
    with SessionLocal() as db:
        toks = [{"id": t.id, "name": t.name, "prefix": t.prefix, "client": t.client, "created": _iso(t.created_at),
                 "last_used": _iso(t.last_used_at)} for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id)
                                                                        .order_by(ApiToken.id.desc()))]
    return {"username": user.username, "role": user.role, "email": user.email, "two_fa": bool(user.totp_enabled),
            "theme": user.theme, "source": user.auth_source or "local", "tokens": toks}
