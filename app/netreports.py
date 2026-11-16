"""Informes › Puertos sin uso.

Puertos sin uso
* Cada respaldo anota, por puerto, la última vez que tuvo enlace (Device.port_seen) desde que OpenNCCM empezó a mirarlos
  (Device.port_seen_since). No hay historial anterior: si se piden 90 días y solo hay datos de 10, se dice.
* Solo switches con estado de enlace. Se excluyen los uplinks (con un vecino LLDP/CDP que es switch, router o firewall),
  las troncales, los puertos con 802.1X y los que tienen una excepción.
* «Preparar shutdown» arma la configuración de cada fabricante y crea una solicitud de cambio, que pasa por aprobación y
  por su análisis de impacto (app/impact.py). La descripción anterior queda guardada en la solicitud.

El Documento de la red pasó al módulo de pago Informes profesionales (repositorio privado opennccm-pro) el 14 nov 2026.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from . import topology
from .auth import Forbidden, check_device, device_scope, require
from .crypto import try_decrypt
from .db import Device, SessionLocal, get_setting, now, set_setting
from .drivers import get_profile
from .i18n import _, _f
from .web import log_action, render

router = APIRouter()
DAYS = (30, 60, 90)
FULL = 0.10  # «casi lleno»: menos del 10 % de los puertos de acceso libres o sin uso


# --------------------------------------------------------------------------- seguimiento (cada respaldo)
def track(seen_json: str | None, since: datetime | None, plist: list[dict]) -> tuple[str, datetime]:
    """Anota la hora en los puertos que tienen enlace ahora; los demás conservan la última vez que lo tuvieron."""
    try:
        seen = json.loads(seen_json) if seen_json else {}
    except ValueError:
        seen = {}
    t = now().isoformat(timespec="minutes")
    for p in plist:
        k = p.get("n")
        if not k:
            continue
        if p.get("s") == "up":
            seen[k] = t
        else:
            seen.setdefault(k, None)
    return json.dumps(seen), since or now()


# --------------------------------------------------------------------------- configuración de cada interfaz
_IFACE = re.compile(r"^interface\s+(\S.*?)\s*$", re.I)


def iface_blocks(config: str) -> dict[str, str]:
    """{puerto corto: líneas de su bloque «interface …»} (configuraciones con sangría, tipo Cisco, Aruba, Huawei…)."""
    out, cur = {}, None
    for line in (config or "").splitlines():
        m = _IFACE.match(line)
        if m:
            cur = topology.short_port(m.group(1))
            out[cur] = ""
        elif cur is not None and line[:1] in (" ", "\t"):
            out[cur] += line.strip() + "\n"
        elif line.strip() and not line.startswith("!"):
            cur = None
    return out


TRUNK = re.compile(r"switchport mode trunk|port link-type trunk|vlan trunk|tagged vlan|trunk allowed", re.I)
DOT1X = re.compile(r"dot1x|authentication port-control|\bmab\b|access-session|aaa port-access|port-security mac-auth", re.I)


def _exceptions() -> set[str]:
    try:
        return set(json.loads(get_setting("unused_except", "[]")))
    except ValueError:
        return set()


# --------------------------------------------------------------------------- informe
def _sw(d: Device) -> bool:
    return topology.device_role(d.device_type, d.model, manual=d.map_role)[0] == "switch"


def unused(user, days: int = 30, site: str = "", vlan: str = "") -> dict:
    """Switches con su capacidad (en uso / sin enlace / libres) y los puertos de acceso sin enlace hace más de N días."""
    from . import services
    days = days if days in DAYS else 30
    scope = device_scope(user)
    exc = _exceptions()
    t = now()
    switches, nostatus, sites, vlans = [], [], set(), set()
    oldest = None
    with SessionLocal() as db:
        for d in db.scalars(select(Device).where(Device.retired_at.is_(None)).order_by(Device.name)):
            if (scope is not None and d.id not in scope) or not d.ports or not _sw(d):
                continue
            if d.location:
                sites.add(d.location)
            if site and (d.location or "") != site:
                continue
            pj = json.loads(d.ports)
            if not pj.get("status"):
                nostatus.append(d.name)
                continue
            v = services.latest_version(db, d.id)
            cfg = try_decrypt(v.content_enc) if v else ""
            blocks = iface_blocks(cfg or "")
            seen = json.loads(d.port_seen) if d.port_seen else {}
            since = d.port_seen_since
            oldest = since if oldest is None or (since and since < oldest) else oldest
            up_links = set()
            for n in topology.network_view(topology.lldp_only(json.loads(d.neighbors) if d.neighbors else []))[0]:
                role, _by = topology.role_why(n.get("platform") or "", n.get("caps"))
                if role in ("switch", "router", "firewall", "unknown"):
                    up_links.add(topology.short_port(n.get("local_port") or ""))
            rows, inuse, nolink, free, excl = [], 0, 0, 0, 0
            for p in pj.get("ports", []):
                sp = topology.short_port(p["n"])
                blk = blocks.get(sp, "")
                net = str(p.get("net") or "")
                why = ("uplink" if sp in up_links else "trunk" if (net.lower() == "trunk" or TRUNK.search(blk)) else
                       "dot1x" if DOT1X.search(blk) else "exception" if f"{d.id}:{p['n']}" in exc else "")
                if why:
                    excl += 1
                    continue
                if net and net.lower() != "trunk":
                    vlans.add(net)
                last = seen.get(p["n"])
                last_dt = datetime.fromisoformat(last) if last else None
                span = (t - since).days if since else None  # días con datos: nunca se dice más que eso
                age = (t - last_dt).days if last_dt else None
                capped = age is not None and span is not None and age > span
                if capped:
                    age = span
                if p.get("s") == "up":
                    inuse += 1
                    continue
                desc = (p.get("d") or "").strip()
                is_free = p.get("s") in ("off", "empty") or desc.upper().startswith("SIN-USO")
                known = (age is not None and age >= days) or (last_dt is None and since and (t - since).days >= days)
                if is_free:
                    free += 1
                    state = "free"
                elif known:
                    nolink += 1
                    state = "nolink"
                else:
                    inuse += 1  # sin enlace hace poco, o sin datos de tantos días: se cuenta como en uso
                    state = "recent"
                if state == "nolink" and (not vlan or net == vlan):
                    rows.append({"n": p["n"], "vlan": net, "d": desc, "days": age, "capped": capped,
                                 "since_short": since.strftime("%d-%m") if since else "",
                                 "since": since.strftime("%d-%m-%Y") if since else "", "s": p.get("s")})
            total = inuse + nolink + free
            if not total:
                continue
            switches.append({"id": d.id, "name": d.name, "site": d.location or "", "model": d.model or "",
                             "total": total, "inuse": inuse, "nolink": nolink, "free": free, "excluded": excl,
                             "full": (free + nolink) < FULL * total, "rows": rows, "type": d.device_type,
                             "can_shut": bool(shutdown_commands(d.device_type, ["x"], {})),
                             "since": since.strftime("%d-%m-%Y") if since else ""})
    tot = sum(s["total"] for s in switches)
    nl = sum(s["nolink"] for s in switches)
    fr = sum(s["free"] for s in switches)
    data_days = (t - oldest).days if oldest else 0
    return {"switches": switches, "days": days, "site": site, "vlan": vlan, "sites": sorted(sites), "vlans": sorted(vlans),
            "kpi": {"access": tot, "nolink": nl, "nolink_pct": round(100 * nl / tot) if tot else 0, "free": fr,
                    "free_pct": round(100 * fr / tot) if tot else 0, "full": sum(1 for s in switches if s["full"])},
            "nostatus": nostatus, "oldest": oldest.strftime("%d-%m-%Y") if oldest else "", "data_days": data_days,
            "short": bool(oldest) and data_days < days}


# --------------------------------------------------------------------------- «shutdown» como solicitud de cambio
def shutdown_commands(device_type: str, ports: list[str], prev: dict[str, str], tag: str = "") -> str:
    """Apagar esos puertos y marcarlos «SIN-USO-AAAA-MM», con la sintaxis de cada fabricante ('' si no se sabe)."""
    from . import consolecore as cc
    tag = tag or "SIN-USO-" + now().strftime("%Y-%m")
    os_key = cc.os_of(device_type)
    if device_type == "mikrotik_routeros":
        return "\n".join(f'/interface ethernet set [find name="{p}"] comment="{tag}" disabled=yes' for p in ports)
    if os_key == "junos":
        return "\n".join(f"set interfaces {p} description {tag}\nset interfaces {p} disable" for p in ports)
    if device_type == "extreme_exos":
        return "\n".join(f"configure ports {p} display-string {tag}\ndisable ports {p}" for p in ports)
    if device_type in ("hp_procurve", "aruba_osswitch"):
        return "\n".join(f'interface {p} name "{tag}"\ninterface {p} disable' for p in ports)
    if os_key == "vrp" or device_type.startswith(("huawei", "hp_comware", "h3c")):
        return "\n".join(f"interface {p}\n description {tag}\n shutdown\n quit" for p in ports)
    if os_key in ("ios", "nxos", "eos", "fastpath") or device_type in ("aruba_aoscx", "dell_os10", "dell_os6"):
        return "\n".join(f"interface {p}\n description {tag}\n shutdown" + ("\n exit" if os_key == "fastpath" else "")
                         for p in ports)
    return ""


def _prev_desc(d: Device, ports: list[str]) -> dict[str, str]:
    pj = json.loads(d.ports) if d.ports else {}
    by = {p["n"]: (p.get("d") or "") for p in pj.get("ports", [])}
    return {p: by.get(p, "") for p in ports}


@router.post("/reports/unused/preview")
async def unused_preview(request: Request):
    user = require(request, "operator")
    data = await request.json()
    dev_id, ports = int(data.get("device_id") or 0), [str(p) for p in (data.get("ports") or [])][:200]
    check_device(user, dev_id)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None:
            raise Forbidden()
        prev = _prev_desc(d, ports)
        cmds = shutdown_commands(d.device_type, ports, prev)
        name = d.name
    return JSONResponse({"commands": cmds, "name": name, "prev": prev,
                         "error": "" if cmds else _f("OpenNCCM no sabe apagar puertos en {0}: hágalo desde su consola.",
                                                    get_profile(d.device_type).label)})


@router.post("/reports/unused/shutdown")
async def unused_shutdown(request: Request):
    """Crea la solicitud de cambio (pasa por aprobación, con su análisis de impacto)."""
    from .main import _create_cr
    user = require(request, "operator")
    data = await request.json()
    dev_id, ports = int(data.get("device_id") or 0), [str(p) for p in (data.get("ports") or [])][:200]
    days = int(data.get("days") or 30)
    check_device(user, dev_id)
    if not ports:
        return JSONResponse({"error": _("Elija al menos un puerto.")}, 400)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        prev = _prev_desc(d, ports)
        cmds = shutdown_commands(d.device_type, ports, prev)
        name = d.name
    if not cmds:
        return JSONResponse({"error": _("OpenNCCM no sabe apagar puertos en este tipo de equipo.")}, 400)
    title = _f("Apagar {0} puertos sin uso en {1}", len(ports), name) if len(ports) != 1 else _f("Apagar 1 puerto sin uso en {0}", name)
    cr = _create_cr(user, title, cmds, {"_shutdown": {str(dev_id): ports}, "_prev_desc": prev,
                                         "_reason": _f("sin enlace hace más de {0} días", days)}, [dev_id])
    log_action(request, user, "unused_shutdown_request", f"#{cr} {name}: {', '.join(ports)}")
    return JSONResponse({"ok": True, "id": cr, "url": f"/changes/{cr}",
                         "msg": _f("Solicitud #{0} creada: apagar {1} puertos en {2}. Espera aprobación.", cr, len(ports), name)
                         if len(ports) != 1 else _f("Solicitud #{0} creada: apagar 1 puerto en {1}. Espera aprobación.", cr, name)})


@router.post("/reports/unused/except")
async def unused_except(request: Request):
    """Marca (o desmarca) puertos como excepción: no se proponen para apagar."""
    user = require(request, "operator")
    data = await request.json()
    dev_id = int(data.get("device_id") or 0)
    check_device(user, dev_id)
    exc = _exceptions()
    keys = {f"{dev_id}:{p}" for p in (data.get("ports") or [])}
    exc = (exc - keys) if data.get("remove") else (exc | keys)
    set_setting("unused_except", json.dumps(sorted(exc)))
    log_action(request, user, "unused_except", f"{dev_id}: {', '.join(sorted(keys))}" + (" (quitada)" if data.get("remove") else ""))
    return JSONResponse({"ok": True, "n": len(keys)})


@router.get("/reports/unused")
def unused_page(request: Request, days: int = 30, site: str = "", vlan: str = "", sw: int = 0):
    user = require(request)
    data = unused(user, days, site, vlan)
    cur = next((s for s in data["switches"] if s["id"] == sw), None) or next((s for s in data["switches"] if s["rows"]), None) \
        or (data["switches"][0] if data["switches"] else None)
    return render(request, "unused_ports.html", user, u=data, cur=cur, month=now().strftime("%Y-%m"))


@router.get("/reports/unused/csv")
def unused_csv(request: Request, days: int = 30, site: str = "", vlan: str = ""):
    user = require(request)
    data = unused(user, days, site, vlan)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["switch", "sitio", "puerto", "vlan", "descripcion", "dias_sin_enlace", "datos_desde"])
    for s in data["switches"]:
        for r in s["rows"]:
            w.writerow([s["name"], s["site"], r["n"], r["vlan"], r["d"], "nunca" if r["days"] is None else r["days"], r["since"]])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="puertos-sin-uso-{data["days"]}d.csv"'})


__all__ = ["router", "track", "unused", "shutdown_commands", "iface_blocks"]
