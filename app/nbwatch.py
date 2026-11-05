"""Aviso de vecino nuevo o perdido (Ajustes › Mapa de la red, Alarmas y el mapa).

Usa las lecturas de vecinos que ya hace el mapa en cada respaldo (LLDP, CDP, MNDP y RoMON): no agrega consultas a los
equipos. Por cada equipo se guarda el estado de cada vecino (NeighborState):
* La primera lectura de un equipo se toma como conocida: no crea alarmas.
* Vecino nuevo: avisa en la primera lectura en que aparece.
* Vecino perdido: avisa solo si falta en N lecturas seguidas (por defecto 2), para no avisar por un corte de segundos.
* Si vuelve a verse, la alarma se cierra sola y queda en el historial con la duración del corte.
Con el aviso apagado el mapa sigue mostrando los cambios, pero no se crean alarmas."""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from . import topology
from .auth import Forbidden, check_device, device_scope, require
from .db import Device, Event, NeighborState, SessionLocal, audit, get_setting, now, set_setting
from .i18n import N_, _, _f
from .web import ago, flash, go, render

log = logging.getLogger("opennccm.nbwatch")
router = APIRouter()

PROTOS = ("lldp", "cdp", "mndp", "romon")
PROTO_LABEL = {"lldp": "LLDP", "cdp": "CDP", "mndp": "MNDP", "romon": "RoMON"}
CAT_NEW, CAT_LOST = N_("Vecino nuevo"), N_("Vecino perdido")
HIGHLIGHT_H = 24


# --------------------------------------------------------------------------- ajustes
def cfg() -> dict:
    g = get_setting
    protos = [p for p in (g("nb_protos", ",".join(PROTOS)) or "").split(",") if p in PROTOS]
    try:
        reads = min(max(int(g("nb_lost_reads", "2") or 2), 1), 10)
    except ValueError:
        reads = 2
    return {"on": g("nb_alerts", "0") == "1", "new_on": g("nb_new_on", "1") == "1", "new_sev": g("nb_new_sev", "warning"),
            "lost_on": g("nb_lost_on", "1") == "1", "lost_sev": g("nb_lost_sev", "major"), "reads": reads, "protos": protos,
            "skip_phones": g("nb_skip_phones", "1") == "1", "skip_dot1x": g("nb_skip_dot1x", "1") == "1",
            "skip_locs": [x for x in (g("nb_skip_locs", "") or "").split("\n") if x.strip()]}


SEVS = ("info", "warning", "major", "critical")


def save_settings(form) -> None:
    was = get_setting("nb_alerts", "0") == "1"
    on = form.get("nb_alerts") == "on"
    set_setting("nb_alerts", "1" if on else "0")
    if on and not was:  # todo lo que se ve ahora se toma como conocido: la primera lectura no crea alarmas
        with SessionLocal() as db:
            for r in db.scalars(select(NeighborState).where(NeighborState.status == "new")):
                r.status, r.known = "ok", True
            db.commit()
    for k in ("nb_new_on", "nb_lost_on", "nb_skip_phones", "nb_skip_dot1x"):
        set_setting(k, "1" if form.get(k) == "on" else "0")
    for k, d in (("nb_new_sev", "warning"), ("nb_lost_sev", "major")):
        set_setting(k, form.get(k) if form.get(k) in SEVS else d)
    v = (form.get("nb_lost_reads") or "2").strip()
    set_setting("nb_lost_reads", str(min(max(int(v), 1), 10)) if v.isdigit() else "2")
    set_setting("nb_protos", ",".join(p for p in PROTOS if p in form.getlist("nb_protos")) or "none")
    set_setting("nb_skip_locs", "\n".join(x for x in form.getlist("nb_skip_locs") if x.strip())[:4000])


def interval_min() -> int | None:
    """Cada cuánto se leen los vecinos: el respaldo programado más frecuente (minutos)."""
    from . import cronutil
    from .db import Schedule
    best = None
    with SessionLocal() as db:
        crons = [s.cron for s in db.scalars(select(Schedule).where(Schedule.action == "backup", Schedule.enabled.is_(True)))]
    for c in crons:
        try:
            t = cronutil.trigger(c)
            a = t.get_next_fire_time(None, now().astimezone())
            b = t.get_next_fire_time(a, a + timedelta(seconds=1))
            m = int((b - a).total_seconds() // 60)
            best = m if best is None or m < best else best
        except Exception:  # noqa: BLE001
            continue
    return best


def known_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(NeighborState).where(NeighborState.known.is_(True))) or 0


def settings_values() -> dict:
    with SessionLocal() as db:
        locs = sorted({d.location for d in db.scalars(select(Device)) if d.location})
    c = cfg()
    return {**c, "interval": interval_min(), "known_n": known_count(), "locations": locs, "sevs": SEVS,
            "proto_labels": PROTO_LABEL}


# --------------------------------------------------------------------------- detección
def key_of(n: dict) -> str:
    who = topology.norm_name(n.get("remote_name")) or (n.get("mac") or "").upper() or n.get("remote_ip") or ""
    port = "romon" if n.get("romon") else topology.short_port(n.get("local_port") or "")
    return f"{port}|{who}"[:160]


def _fill(r: NeighborState, n: dict) -> None:
    r.port = (n.get("local_port") or "")[:64]
    r.name = (n.get("remote_name") or "")[:128]
    r.mac = (n.get("mac") or "")[:17]
    r.ip = (n.get("remote_ip") or "")[:45]
    r.platform = (n.get("platform") or "")[:128]
    r.proto = (n.get("proto") or ("romon" if n.get("romon") else "lldp"))[:8]
    r.romon = bool(n.get("romon"))
    r.hops = n.get("hops") or None
    r.via = (n.get("via") or "")[:17]
    r.caps = ",".join(n.get("caps") or [])[:64]
    r.remote_port = (n.get("remote_port") or "")[:64]


def dot1x_ports(config: str) -> set[str]:
    """Puertos con 802.1X en la configuración (estilo Cisco): ya controlan quién se conecta."""
    out, cur = set(), None
    for line in (config or "").splitlines():
        if line.startswith("interface "):
            cur = topology.short_port(line[10:].strip())
        elif not line.startswith((" ", "\t")):
            cur = None
        elif cur and ("dot1x pae authenticator" in line or "authentication port-control auto" in line
                      or "access-session port-control auto" in line):
            out.add(cur)
    return out


def _skipped(dev: Device, r: NeighborState, c: dict, dot1x: set[str]) -> bool:
    if r.proto not in c["protos"]:
        return True
    if c["skip_phones"] and set((r.caps or "").split(",")) & {"phone", "wlan"}:
        return True
    if c["skip_dot1x"] and not r.romon and topology.short_port(r.port) in dot1x:
        return True
    return bool(dev.location and dev.location in c["skip_locs"])


def _is_ours(db, name: str) -> Device | None:
    if not name:
        return None
    nn = topology.norm_name(name)
    return next((d for d in db.scalars(select(Device)) if topology.norm_name(d.name) == nn), None)


def texts(db, dev: Device, r: NeighborState, kind: str) -> tuple[str, str]:
    """Título y subtítulo de la alarma (y de la ficha del vecino)."""
    who = r.name or r.mac or r.ip or "?"
    proto = PROTO_LABEL.get(r.proto, r.proto.upper())
    ours = _is_ours(db, r.name)
    if kind == "new" and r.romon:
        return (_f("Vecino nuevo por RoMON: {0}", who),
                _f("A {0} saltos de {1} · {2}", r.hops or 1, dev.name, _("está en OpenNCCM") if ours else _("no está en OpenNCCM")))
    if kind == "new":
        return (_f("Vecino nuevo en {0} · {1}", dev.name, r.port),
                (_f("Equipo de OpenNCCM «{0}» · {1}", who, proto) if ours else _f("Equipo desconocido «{0}» · {1}", who, proto)))
    if r.romon:
        return _f("Vecino perdido: {0}", who), _f("Ya no se ve por RoMON desde {0}", dev.name)
    return _f("Vecino perdido: {0}", who), _f("Ya no se ve desde {0} {1} · {2}", dev.name, r.port, proto)


def _alarm(db, dev: Device, r: NeighborState, kind: str, c: dict) -> None:
    from . import services
    title, sub = texts(db, dev, r, kind)
    sev = c["new_sev"] if kind == "new" else c["lost_sev"]
    e = Event(severity=sev, category=CAT_NEW if kind == "new" else CAT_LOST, device_id=dev.id, message=f"{title}\n{sub}")
    db.add(e)
    db.flush()
    r.event_id = e.id
    minimum = get_setting("alert_min_severity", "warning")
    if services.SEV_ORDER.get(sev, 0) >= services.SEV_ORDER.get(minimum, 1):
        services.pool.submit(services.send_alert, f"[OpenNCCM][{sev.upper()}] {_(e.category)}", e.message)


def update(db, dev: Device, nbrs: list[dict], config: str = "") -> dict:
    """Compara la lectura nueva con la anterior (dentro de la sesión del respaldo; no hace commit)."""
    t = now()
    c = cfg()
    romon_read = get_setting("topology_romon", "0") == "1"
    rows = {r.key: r for r in db.scalars(select(NeighborState).where(NeighborState.device_id == dev.id))}
    first = not dev.nb_tracked
    dot1x = dot1x_ports(config) if c["skip_dot1x"] else set()
    seen, res = set(), {"new": 0, "lost": 0, "back": 0}
    for n in nbrs:
        if not (n.get("remote_name") or n.get("remote_ip") or n.get("mac")):
            continue
        k = key_of(n)
        if k in seen:
            continue
        seen.add(k)
        r = rows.get(k)
        if r is None:
            r = NeighborState(device_id=dev.id, key=k, first_seen=t, last_seen=t, status="ok" if first else "new",
                              known=first, missing=0)
            _fill(r, n)
            db.add(r)
            rows[k] = r
            if not first:
                res["new"] += 1
                if c["on"] and c["new_on"] and not _skipped(dev, r, c, dot1x):
                    _alarm(db, dev, r, "new", c)
            continue
        _fill(r, n)
        r.missing, r.last_seen = 0, t
        if r.status == "lost":  # volvió: la alarma se cierra sola y queda la duración del corte
            r.back_at = t
            r.back_after = int((t - (r.lost_from or r.lost_at or t)).total_seconds())
            r.status = "ok" if r.known else "new"
            res["back"] += 1
            if r.event_id:
                e = db.get(Event, r.event_id)
                if e is not None and not e.acknowledged:
                    e.acknowledged = True
                    e.message = (e.message or "") + "\n" + _f("Volvió a verse {0} después.", duration(r.back_after))
                r.event_id = None
    for k, r in rows.items():
        if k in seen:
            continue
        if r.romon and not romon_read:  # sin leer RoMON esta vez: no cuenta como ausencia
            continue
        r.missing = (r.missing or 0) + 1
        if r.missing == 1:
            r.lost_from = r.last_seen
        if r.status != "lost" and r.missing >= c["reads"]:
            r.status, r.lost_at = "lost", t
            res["lost"] += 1
            if c["on"] and c["lost_on"] and not _skipped(dev, r, c, dot1x):
                _alarm(db, dev, r, "lost", c)
    dev.nb_tracked = True
    return res


def duration(secs: int | None) -> str:
    s = int(secs or 0)
    if s < 3600:
        return _f("{0} min", max(1, s // 60))
    if s < 86400:
        h, m = divmod(s // 60, 60)
        return _f("{0} h {1} min", h, m) if m else _f("{0} h", h)
    return _f("{0} días", s // 86400)


# --------------------------------------------------------------------------- mapa
def _acked(db, r: NeighborState) -> bool:
    if not r.event_id:
        return False
    e = db.get(Event, r.event_id)
    return e is None or e.acknowledged


def marks(dev_ids: list[int]) -> dict:
    """Cambios de las últimas 24 h para resaltar en el mapa y para su panel lateral."""
    since = now() - timedelta(hours=HIGHLIGHT_H)
    out = {"new": [], "lost": [], "feed": []}
    if not dev_ids:
        return out
    with SessionLocal() as db:
        names = {d.id: d.name for d in db.scalars(select(Device).where(Device.id.in_(dev_ids)))}
        rows = db.scalars(select(NeighborState).where(NeighborState.device_id.in_(dev_ids)))
        for r in rows:
            base = {"id": r.id, "dev": r.device_id, "dev_name": names.get(r.device_id, ""), "name": r.name or r.mac or r.ip,
                    "port": r.port, "proto": PROTO_LABEL.get(r.proto, r.proto), "romon": r.romon, "mac": r.mac, "ip": r.ip,
                    "remote_port": r.remote_port, "key": topology.norm_name(r.name) or r.mac or r.ip}
            if r.status == "new" and not r.known and r.first_seen >= since and not _acked(db, r):
                out["new"].append(base)
                out["feed"].append({**base, "kind": "new", "at": r.first_seen.isoformat(), "ago": ago(r.first_seen)})
            elif r.status == "lost" and r.lost_at and r.lost_at >= since and not _acked(db, r):
                out["lost"].append({**base, "ago": ago(r.lost_from or r.lost_at)})
                out["feed"].append({**base, "kind": "lost", "at": r.lost_at.isoformat(), "ago": ago(r.lost_at)})
            if r.back_at and r.back_at >= since:
                out["feed"].append({**base, "kind": "back", "at": r.back_at.isoformat(), "ago": ago(r.back_at),
                                    "dur": duration(r.back_after)})
    out["feed"].sort(key=lambda x: x["at"], reverse=True)
    return out


# --------------------------------------------------------------------------- ficha del vecino y acciones
def _state(user, sid: int):
    with SessionLocal() as db:
        r = db.get(NeighborState, sid)
        if r is None:
            raise Forbidden()
        check_device(user, r.device_id)
        return r


def detail(user, sid: int) -> dict:
    from . import macfind
    r = _state(user, sid)
    with SessionLocal() as db:
        dev = db.get(Device, r.device_id)
        kind = "lost" if r.status == "lost" else ("romon" if r.romon else "new")
        title, sub = texts(db, dev, r, "lost" if kind == "lost" else "new")
        ports = (json.loads(dev.ports) if dev.ports else {}).get("ports", [])
        p = next((x for x in ports if topology.short_port(x.get("n", "")) == topology.short_port(r.port)), {}) if r.port else {}
        before = [x for x in db.scalars(select(NeighborState).where(NeighborState.device_id == dev.id,
                                                                    NeighborState.id != r.id)) if r.port and
                  topology.short_port(x.port) == topology.short_port(r.port)]
        before.sort(key=lambda x: x.last_seen, reverse=True)
        ours = _is_ours(db, r.name)
        ev = db.get(Event, r.event_id) if r.event_id else None
        info = {"id": r.id, "kind": kind, "title": title, "sub": sub, "dev": dev.name, "dev_id": dev.id, "port": r.port,
                "name": r.name, "platform": r.platform, "mac": r.mac, "ip": r.ip, "proto": PROTO_LABEL.get(r.proto, r.proto),
                "romon": r.romon, "hops": r.hops, "via": r.via, "first": r.first_seen, "last": r.last_seen,
                "missing": r.missing, "status": r.status, "known": r.known, "ours": ours.id if ours else None,
                "event": ev.id if ev else None, "acked": bool(ev and ev.acknowledged),
                "port_vlan": p.get("net", ""), "port_desc": p.get("d", ""), "port_state": p.get("s", ""),
                "can_shut": bool(r.port) and not r.romon}
        seen_json = json.loads(dev.port_seen) if dev.port_seen else {}
    if before:
        b = before[0]
        info["before"] = _f("«{0}» hasta {1}", b.name or b.mac or b.ip, ago(b.last_seen))
    else:
        last = next((v for k, v in seen_json.items() if topology.short_port(k) == topology.short_port(r.port)), None) if r.port else None
        info["before"] = (_f("Nada: sin enlace {0}", ago(__import__("datetime").datetime.fromisoformat(last)))
                          if last else (_("Nada que OpenNCCM haya visto") if r.port else ""))
    info["vendor"] = macfind.vendor(macfind.norm_mac(r.mac) or "") if r.mac else ""
    if r.ip:
        info["ip_from"] = _f("anunciada por {0}", info["proto"])
    elif r.mac and macfind.norm_mac(r.mac):
        w = macfind.lookup(user, r.mac)
        if w.get("ip"):
            info["ip"], info["ip_from"] = w["ip"], _f("según la tabla ARP de {0}", w["arp"]["device"]) if w.get("arp") else ""
    iv = interval_min()
    info["missing_t"] = (_f("{0} lecturas seguidas (unos {1} min)", r.missing, r.missing * iv) if iv
                         else _f("{0} lecturas seguidas", r.missing)) if r.missing else ""
    if kind == "lost" and r.port:
        try:
            from . import impact
            imp = impact.analyze(impact._graph(), [], {r.device_id: [r.port]}, user)
            names = [x["name"] for x in imp["isolated"] + imp["branches"]]
            info["depends"] = (_f("Dependen de este enlace: {0}", ", ".join(names)) if names
                               else _("Nada queda aislado sin este enlace."))
        except Exception:  # noqa: BLE001
            info["depends"] = ""
    return info


@router.get("/neighbors/{sid}")
def page(request: Request, sid: int):
    user = require(request)
    from .console import console_allowed
    d = detail(user, sid)
    return render(request, "neighbor.html", user, d=d, ago=ago, can_console=console_allowed(user, d["dev_id"]))


def _ack(db, r: NeighborState) -> None:
    if r.event_id:
        e = db.get(Event, r.event_id)
        if e is not None:
            e.acknowledged = True


@router.post("/neighbors/{sid}/known")
def mark_known(request: Request, sid: int):
    """«Es conocido, no avisar más»: queda en la lista de vecinos conocidos (si después desaparece, también avisa)."""
    user = require(request, "operator")
    r = _state(user, sid)
    with SessionLocal() as db:
        r = db.get(NeighborState, sid)
        r.known = True
        if r.status == "new":
            r.status = "ok"
        _ack(db, r)
        db.commit()
        name = r.name or r.mac
    audit(user.username, "neighbor_known", name)
    flash(request, _f("«{0}» quedó en la lista de vecinos conocidos.", name))
    return go(request.headers.get("referer") or "/events")


@router.post("/neighbors/{sid}/forget")
def forget(request: Request, sid: int):
    """«Ya no existe, quitar del mapa»."""
    user = require(request, "operator")
    _state(user, sid)
    with SessionLocal() as db:
        r = db.get(NeighborState, sid)
        name = r.name or r.mac
        _ack(db, r)
        db.delete(r)
        db.commit()
    audit(user.username, "neighbor_forget", name)
    flash(request, _f("«{0}» se quitó del mapa.", name))
    return go("/events?cat=vecinos")


@router.post("/neighbors/{sid}/shutdown")
def shutdown(request: Request, sid: int):
    """«Preparar shutdown del puerto»: una solicitud de cambio; no se apaga nada sin aprobación."""
    from .main import _create_cr
    from .netreports import _prev_desc, shutdown_commands
    user = require(request, "operator")
    r = _state(user, sid)
    with SessionLocal() as db:
        d = db.get(Device, r.device_id)
        prev = _prev_desc(d, [r.port])
        cmds = shutdown_commands(d.device_type, [r.port], prev, "VECINO-NUEVO-" + now().strftime("%Y-%m"))
        name = d.name
    if not cmds:
        flash(request, _("OpenNCCM no sabe apagar puertos en este tipo de equipo."), "error")
        return go(f"/neighbors/{sid}")
    cr = _create_cr(user, _f("Apagar {0} en {1} (vecino nuevo «{2}»)", r.port, name, r.name or r.mac), cmds,
                    {"_shutdown": {str(r.device_id): [r.port]}, "_prev_desc": prev,
                     "_reason": _f("vecino nuevo «{0}»", r.name or r.mac)}, [r.device_id])
    audit(user.username, "neighbor_shutdown_request", f"#{cr} {name} {r.port}")
    flash(request, _f("Solicitud #{0} creada: apagar {1} en {2}. Espera aprobación.", cr, r.port, name))
    return go(f"/changes/{cr}")


@router.get("/neighbors")
def known_page(request: Request):
    """Vecinos conocidos: ver y editar la lista."""
    user = require(request, "operator")
    scope = device_scope(user)
    with SessionLocal() as db:
        names = {d.id: d.name for d in db.scalars(select(Device))}
        rows = [r for r in db.scalars(select(NeighborState).where(NeighborState.known.is_(True))
                                      .order_by(NeighborState.device_id, NeighborState.port))
                if scope is None or r.device_id in scope]
    return render(request, "neighbors_known.html", user, rows=rows, names=names, labels=PROTO_LABEL, ago=ago)


@router.post("/neighbors/{sid}/unknown")
def unknown(request: Request, sid: int):
    """Quitar de la lista de conocidos: si sigue ahí, en la próxima lectura avisa como vecino nuevo."""
    user = require(request, "operator")
    _state(user, sid)
    with SessionLocal() as db:
        r = db.get(NeighborState, sid)
        name = r.name or r.mac
        db.delete(r)
        db.commit()
    audit(user.username, "neighbor_unknown", name)
    flash(request, _f("«{0}» ya no está en la lista de conocidos.", name))
    return go("/neighbors")


@router.get("/api/neighbors/changes")
def api_changes(request: Request):
    user = require(request)
    scope = device_scope(user)
    with SessionLocal() as db:
        ids = [d.id for d in db.scalars(select(Device)) if scope is None or d.id in scope]
    return JSONResponse(marks(ids))
