"""Análisis de impacto: qué queda sin servicio si un equipo se reinicia o se apagan unos puertos.

Se calcula con el mapa de la red (vecinos LLDP/CDP que se leen en cada respaldo): se quitan del grafo los equipos que
se reinician (o los enlaces de los puertos que se apagan) y se mira qué queda separado del resto de la red.

* Rojo: queda aislado (sin camino alternativo).
* Ámbar: entre lo aislado, los routers y firewalls, o los equipos de otro sitio (otra «Ubicación»): enlaces a
  sucursales u otras redes que se cortan.
* Verde: vecinos directos que siguen funcionando por otro camino.

Lo que no se sabe se dice: los equipos que no informan vecinos no se pueden evaluar, y los datos tienen la hora del
respaldo del que salen. No se estima cuánto dura un reinicio: depende del equipo y no hay de dónde sacarlo.
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import topology
from .auth import check_device, device_scope, require
from .db import Device, SessionLocal, now
from .i18n import _, _f

router = APIRouter()

# órdenes que reinician el equipo (en una solicitud de cambio o en un configlet)
REBOOT = re.compile(r"^\s*(?:reload\b|reboot\b|/system reboot|system reboot|request system reboot|execute reboot|"
                    r"restart system|shutdown -r\b|write erase|/system reset-configuration)", re.I | re.M)
_IFACE = re.compile(r"^\s*interface\s+(?:range\s+)?(.+?)\s*$", re.I)


class Graph:
    """Equipos (de OpenNCCM y vecinos que no lo son) y enlaces, como en el mapa de la red."""

    def __init__(self, devs: list[Device]):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.adj: dict[str, set[str]] = {}
        self.nodata: list[str] = []
        ages = []
        hosts = [d.host.lower() for d in devs]
        by_ip = {d.host.lower(): d for d in devs if hosts.count(d.host.lower()) == 1}
        by_name = {topology.norm_name(d.name): d for d in devs}
        caps: dict[int, list] = {}
        for d in devs:
            self.nodes[f"d{d.id}"] = {"id": f"d{d.id}", "name": d.name, "dev": d.id, "loc": (d.location or "").strip(),
                                      "type": d.device_type, "model": d.model or ""}
            if d.neighbors is None:
                self.nodata.append(f"d{d.id}")
                continue
            if d.last_backup_at:
                ages.append(d.last_backup_at)
        seen = set()
        for d in devs:
            if d.neighbors is None:
                continue
            for n in topology.network_view(json.loads(d.neighbors))[0]:
                t = by_name.get(topology.norm_name(n.get("remote_name"))) or by_ip.get((n.get("remote_ip") or "").lower())
                if t is not None and t.id == d.id:
                    continue
                if t is not None:
                    other = f"d{t.id}"
                    caps.setdefault(t.id, []).extend(n.get("caps") or [])
                else:
                    key = topology.norm_name(n.get("remote_name")) or n.get("remote_ip") or ""
                    if not key:
                        continue
                    other = f"u:{key}"
                    if other not in self.nodes:
                        role, _by = topology.role_why(n.get("platform") or "", n.get("caps"))
                        self.nodes[other] = {"id": other, "name": n.get("remote_name") or n.get("remote_ip"), "dev": None,
                                             "loc": "", "role": role, "model": n.get("platform") or ""}
                a, pa, b, pb = f"d{d.id}", n.get("local_port") or "", other, n.get("remote_port") or ""
                k = (min(a, b), max(a, b), topology.short_port(pa if a < b else pb), topology.short_port(pb if a < b else pa))
                if k in seen or (k[0], k[1], k[2], "") in seen:
                    continue
                seen.add(k)
                self.edges.append({"a": a, "b": b, "pa": pa, "pb": pb})
                self.adj.setdefault(a, set()).add(b)
                self.adj.setdefault(b, set()).add(a)
        for d in devs:
            self.nodes[f"d{d.id}"]["role"] = topology.device_role(d.device_type, d.model, caps.get(d.id))[0]
        self.age: datetime | None = min(ages) if ages else None

    def _adj_without(self, cut: set[tuple[str, str]]) -> dict[str, set[str]]:
        if not cut:
            return self.adj
        adj: dict[str, set[str]] = {}
        for e in self.edges:
            if (e["a"], e["b"]) in cut or (e["b"], e["a"]) in cut:
                continue
            adj.setdefault(e["a"], set()).add(e["b"])
            adj.setdefault(e["b"], set()).add(e["a"])
        return adj

    @staticmethod
    def _component(adj, start, banned) -> set[str]:
        out, todo = {start}, [start]
        while todo:
            x = todo.pop()
            for y in adj.get(x, ()):
                if y not in out and y not in banned:
                    out.add(y)
                    todo.append(y)
        return out

    def _weight(self, comp: set[str]) -> tuple:
        """La parte que «es la red»: la más conectada (suma de enlaces de sus equipos en el mapa completo: el núcleo tiene
        muchos); a igualdad, la que tiene el firewall (la salida) y después la que tiene más equipos de OpenNCCM."""
        degree = sum(len(self.adj.get(n, ())) for n in comp)
        fw = any(self.nodes[n].get("role") == "firewall" for n in comp)
        devs = sum(1 for n in comp if self.nodes[n]["dev"] is not None)
        return degree, fw, devs, len(comp)

    def isolated(self, removed: set[str], cut: set[tuple[str, str]] = frozenset()) -> set[str]:
        """Lo que queda separado de la red al quitar esos equipos (o esos enlaces)."""
        adj = self._adj_without(set(cut))
        touched = set(removed) | {x for e in cut for x in e}
        out: set[str] = set()
        done: set[str] = set()
        for start in touched:  # cada parte de la red a la que pertenecía lo que se quita, antes de quitarlo
            if start in done or start not in self.nodes:
                continue
            whole = self._component(self.adj, start, set())
            done |= whole
            rest = whole - set(removed)
            parts, left = [], set(rest)
            while left:
                p = self._component(adj, next(iter(left)), set(removed))
                parts.append(p)
                left -= p
            if len(parts) <= 1:
                continue
            main = max(parts, key=self._weight)
            # si ninguna parte tiene buena parte de los enlaces (se cayó el núcleo entero), no hay «resto de la red»:
            # todo queda aislado
            total = sum(self._weight(p)[0] for p in parts)
            if total and self._weight(main)[0] < 0.4 * total:
                main = None
            for p in parts:
                if p is not main:
                    out |= p
        return out

    def parent(self, removed: set[str], node: str, isolated: set[str]) -> str | None:
        """El equipo del que cuelga un equipo aislado (camino más corto desde lo que se quita)."""
        prev, todo = {r: None for r in removed}, list(removed)
        while todo:
            x = todo.pop(0)
            for y in sorted(self.adj.get(x, ())):
                if y in prev or (y not in isolated):
                    continue
                prev[y] = x
                if y == node:
                    return x
                todo.append(y)
        return None


def _graph() -> Graph:
    with SessionLocal() as db:
        devs = list(db.scalars(select(Device).order_by(Device.name)))
        return Graph(devs)


def _ago(t: datetime | None) -> str:
    if t is None:
        return ""
    m = max(0, int((now() - t).total_seconds() // 60))
    if m < 60:
        return _f("hace {0} min", m)
    if m < 60 * 48:
        return _f("hace {0} h", m // 60)
    return _f("hace {0} días", m // 1440)


def _names(names: list[str]) -> str:
    return ", ".join(names[:-1]) + " " + _("y") + " " + names[-1] if len(names) > 1 else (names[0] if names else "")


def analyze(g: Graph, removed_ids: list[int], cut_ports: dict[int, list[str]] | None = None, user=None) -> dict:
    """Impacto de reiniciar esos equipos (juntos) o de apagar esos puertos. Para la pantalla y para Auditoría."""
    scope = device_scope(user) if user is not None else None
    removed = {f"d{i}" for i in removed_ids if f"d{i}" in g.nodes}
    cut: set[tuple[str, str]] = set()
    for dev_id, ports in (cut_ports or {}).items():
        sp = {topology.short_port(p) for p in ports}
        for e in g.edges:
            for me, other, pm in ((e["a"], e["b"], e["pa"]), (e["b"], e["a"], e["pb"])):
                if me == f"d{dev_id}" and topology.short_port(pm) in sp:
                    cut.add((me, other))
    iso = g.isolated(removed, cut)

    def label(n: str) -> str:
        nd = g.nodes[n]
        if nd["dev"] is not None and scope is not None and nd["dev"] not in scope:
            return _("(equipo de otro grupo)")
        return nd["name"]

    subject = [g.nodes[n] for n in removed]
    locs = {s["loc"] for s in subject if s["loc"]}
    red, amber = [], []
    for n in sorted(iso, key=lambda x: g.nodes[x]["name"].lower()):
        nd = g.nodes[n]
        par = g.parent(removed or {a for a, _b in cut}, n, iso)
        row = {"id": n, "name": label(n), "dev": nd["dev"], "role": nd.get("role", "unknown"),
               "via": label(par) if par else "", "direct": par in removed or any(par == a for a, _b in cut)}
        branch = nd.get("role") in ("router", "firewall") or (nd["loc"] and locs and nd["loc"] not in locs)
        (amber if branch else red).append(row)
    neigh = set()
    for r in removed:
        neigh |= g.adj.get(r, set())
    for a, b in cut:
        neigh.add(b)
    kept_ids = sorted(neigh - iso - removed)
    kept = sorted(label(n) for n in kept_ids)
    nodata = [g.nodes[n]["name"] for n in g.nodata if n not in removed]
    known = any(g.adj.get(r) for r in removed) or bool(cut) or bool(cut_ports)
    n_iso, n_br = len(red), len(amber)
    if not known:
        level = "nodata"
    elif n_iso + n_br == 0:
        level = "none"
    elif n_br or n_iso >= 3:
        level = "high"
    else:
        level = "medium"
    who = _names([s["name"] for s in subject]) if subject else ""
    if cut_ports and not removed:
        nports = sum(len(p) for p in cut_ports.values())
        what = _f("Si apagas estos {0} puertos", nports) if nports != 1 else _("Si apagas este puerto")
    else:
        what = _f("Si reinicias {0}", who)
    if level == "nodata":
        sentence = _f("OpenNCCM no conoce los vecinos de {0}: no se puede calcular qué depende de él.", who or "—")
    elif level == "none":
        sentence = what + ", " + (_f("nada queda aislado: {0} vecinos siguen por otro camino.", len(kept)) if kept
                                  else _("nada queda aislado."))
    else:
        parts = []
        if n_iso:
            parts.append(_f("quedan aislados {0} equipos", n_iso) if n_iso != 1 else _("queda aislado 1 equipo"))
        if n_br:
            parts.append(_f("se cortan {0} enlaces a routers o sucursales", n_br) if n_br != 1
                         else _("se corta 1 enlace a un router o sucursal"))
        sentence = what + " " + (" " + _("y") + " ").join(parts)
    short = {"high": _("Impacto alto"), "medium": _("Impacto medio"), "none": _("Sin impacto"),
             "nodata": _("Impacto desconocido")}[level]
    return {"level": level, "label": short, "sentence": sentence, "isolated": red, "branches": amber, "kept": kept,
            "nodata": nodata, "age": _ago(g.age), "removed": sorted(removed), "who": who, "kept_ids": kept_ids,
            "direct": [r["name"] for r in red + amber if r["direct"]],
            "svg": svg(g, removed, cut, iso, {r["id"] for r in amber}, label)}


def audit_text(imp: dict) -> str:
    """Lo que se mostró, para guardarlo en Auditoría junto con la aprobación."""
    return f"{imp['label']}: {imp['sentence']}" + (f" · aislados: {', '.join(r['name'] for r in imp['isolated'])}" if imp["isolated"] else "") \
        + (f" · routers/sucursales: {', '.join(r['name'] for r in imp['branches'])}" if imp["branches"] else "")


# --------------------------------------------------------------------------- mini-mapa

def svg(g: Graph, removed: set[str], cut: set, iso: set[str], amber: set[str], label, w: int = 600, h: int = 340) -> str:
    """Lo que se quita en el centro, sus vecinos alrededor y lo que cuelga de ellos más afuera (hasta 16 equipos)."""
    centers = sorted(removed) or sorted({a for a, _b in cut})
    if not centers:
        return ""
    cx, cy = w / 2, h / 2
    pos: dict[str, tuple[float, float]] = {}
    for i, c in enumerate(centers[:3]):
        pos[c] = (cx + (i - (len(centers[:3]) - 1) / 2) * 130, cy)
    ring1 = sorted({y for c in centers for y in g.adj.get(c, ())} - set(centers), key=lambda x: (x not in iso, g.nodes[x]["name"]))[:10]
    for i, n in enumerate(ring1):
        a = -math.pi / 2 + 2 * math.pi * i / max(1, len(ring1))
        pos[n] = (cx + 200 * math.cos(a), cy + 120 * math.sin(a))
    kids: dict[str, list[str]] = {}
    for n in sorted(iso - set(ring1), key=lambda x: g.nodes[x]["name"]):  # lo que cuelga de un vecino, más afuera
        par = next((p for p in ring1 if p in g.adj.get(n, ())), None)
        if par is not None and sum(len(v) for v in kids.values()) < 8:
            kids.setdefault(par, []).append(n)
    for par, ns in kids.items():  # hacia afuera del centro y repartidos de costado, sin taparse
        px, py = pos[par]
        dx, dy = px - cx, py - cy
        ln = math.hypot(dx, dy) or 1
        ux, uy = dx / ln, dy / ln
        for k, n in enumerate(ns):
            off = (k - (len(ns) - 1) / 2) * 116
            x, y = px + ux * 64 - uy * off, py + uy * 46 + ux * off * 0.45
            x, y = min(w - 54, max(54, x)), min(h - 17, max(17, y))
            # el lugar libre más cercano (contra un borde quedarían encimados): se prueba alrededor, de cerca a lejos
            spots = sorted(((x + dx * 116, y + dy * 38) for dx in range(-3, 4) for dy in range(-4, 5)),
                           key=lambda q: (abs(q[0] - x) / 116) ** 2 + (abs(q[1] - y) / 38) ** 2)
            x, y = next(((qx, qy) for qx, qy in spots if 54 <= qx <= w - 54 and 17 <= qy <= h - 17
                         and not any(abs(qx - ox) < 110 and abs(qy - oy) < 34 for o, (ox, oy) in pos.items() if o != n)),
                        (x, y))
            pos[n] = (x, y)

    def col(n):
        return "sub" if n in centers else "amber" if n in amber else "red" if n in iso else "green"

    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{html.escape(_("Mapa del impacto"))}" class="imp-map">']
    for e in g.edges:
        a, b = e["a"], e["b"]
        if a in pos and b in pos:
            hit = a in centers or b in centers or (a, b) in cut or (b, a) in cut
            dead = a in iso or b in iso or (a, b) in cut or (b, a) in cut
            cls = "hit" if hit and dead else "dead" if dead else "ok"
            out.append(f'<line class="il {cls}" x1="{pos[a][0]:.0f}" y1="{pos[a][1]:.0f}" x2="{pos[b][0]:.0f}" '
                       f'y2="{pos[b][1]:.0f}"/>')
    for n, (x, y) in pos.items():
        name = label(n)
        t = html.escape(name if len(name) <= 15 else name[:14] + "…")
        out.append(f'<g class="in {col(n)}"><title>{html.escape(name)}</title><rect x="{x - 52:.0f}" y="{y - 15:.0f}" '
                   f'width="104" height="30" rx="9"/><text x="{x:.0f}" y="{y + 4:.0f}" text-anchor="middle">{t}</text></g>')
    if removed:
        x, y = pos[centers[0]]
        out.append(f'<text class="itag" x="{x:.0f}" y="{y + 32:.0f}" text-anchor="middle">{html.escape(_("SE REINICIA"))}</text>')
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- orden para reiniciar varios
def waves(g: Graph, ids: list[int]) -> list[list[int]]:
    """Orden sugerido para reiniciar varios equipos: primero los que no aíslan a nadie, después de a uno los que sí.
    Dos equipos van juntos solo si juntos no dejan sin servicio más que cada uno por separado (ni uno al otro)."""
    single = {i: g.isolated({f"d{i}"}) for i in ids}
    order = sorted(ids, key=lambda i: (len(single[i]), g.nodes.get(f"d{i}", {}).get("name", "").lower()))
    out: list[list[int]] = []
    for i in order:
        placed = False
        for wave in out:
            together = g.isolated({f"d{x}" for x in wave + [i]})
            apart = set().union(*(single[x] for x in wave + [i]))
            if not single[i] and not any(single[x] for x in wave) and together == apart:  # los que aíslan, de a uno
                wave.append(i)
                placed = True
                break
        if not placed:
            out.append([i])
    return out


# --------------------------------------------------------------------------- solicitudes de cambio
def interfaces_shut(body: str) -> list[str]:
    """Interfaces que se apagan en unos comandos tipo Cisco («interface …» seguido de «shutdown»)."""
    out, cur = [], []
    for line in (body or "").splitlines():
        m = _IFACE.match(line)
        if m:
            cur = [p.strip() for p in m.group(1).split(",") if p.strip()]
            continue
        s = line.strip().lower()
        if s == "shutdown" and cur:
            out += cur
        elif s and not line.startswith((" ", "\t")) and not s.startswith(("description", "!")):
            cur = [] if not s.startswith("interface") else cur
    return out


def for_change(cr, user=None) -> dict | None:
    """Impacto de una solicitud de cambio: reinicio (firmware con reinicio o comandos que reinician) o puertos que se
    apagan. None si no reinicia nada ni apaga puertos conocidos."""
    ids = json.loads(cr.device_ids or "[]")
    variables = json.loads(cr.variables or "{}")
    g = _graph()
    if cr.kind == "firmware":
        if not cr.reload:
            return None
        res = analyze(g, ids, user=user)
        res["kind"] = "reboot"
        if len(ids) > 1:
            target = ""
            if cr.firmware_id:
                from .db import FirmwareImage
                with SessionLocal() as db:
                    img = db.get(FirmwareImage, cr.firmware_id)
                    target = (img.version or "") if img else ""
            res["plan"] = pl = plan(g, ids, user, target)
            # se reinician por pasos (no todos juntos): lo que se lee y se aprueba es lo del peor paso
            rank = {"high": 3, "medium": 2, "nodata": 1, "none": 0}
            worst = max(pl["rows"], key=lambda r: (rank[r["level"]], r["iso"] + r["br"]))
            res["level"] = worst["level"] if worst["level"] != "nodata" else "nodata"
            res["label"] = {"high": _("Impacto alto"), "medium": _("Impacto medio"), "none": _("Sin impacto"),
                            "nodata": _("Impacto desconocido")}[res["level"]]
            steps = len(pl["waves"])
            res["sentence"] = (_f("Se reinician en {0} pasos, en el orden sugerido", steps) if steps != 1
                               else _("Se reinician todos juntos: ninguno deja sin servicio a otro")) + (
                ". " + _f("El paso que más corta: {0} (aísla {1} y corta {2})", worst["name"], worst["iso"], worst["br"])
                if worst["iso"] + worst["br"] else "")
        return res
    if REBOOT.search(cr.body or ""):
        res = analyze(g, ids, user=user)
        res["kind"] = "reboot"
        return res
    shut = variables.get("_shutdown") or {}
    ports = {int(k): v for k, v in shut.items()} if shut else {i: interfaces_shut(cr.body) for i in ids}
    ports = {k: v for k, v in ports.items() if v}
    if ports:
        res = analyze(g, [], ports, user=user)
        res["kind"] = "ports"
        return res
    return None


def plan(g: Graph, ids: list[int], user=None, target: str = "") -> dict:
    """Tabla del orden sugerido (firmware de varios equipos con reinicio)."""
    ws = waves(g, ids)
    rows, n = [], 0
    with SessionLocal() as db:
        devs = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(ids)))}
    for wave in ws:
        n += 1
        for i in wave:
            a = analyze(g, [i], user=user)
            d = devs.get(i)
            rows.append({"order": n, "id": i, "name": d.name if d else str(i), "model": (d.model if d else "") or "",
                         "version": (d.os_version if d else "") or "", "target": target, "level": a["level"],
                         "iso": len(a["isolated"]), "br": len(a["branches"]),
                         "deps": _names([r["name"] for r in a["isolated"] + a["branches"]][:6]),
                         "kept": len(a["kept"])})
    worst = max(((x, y) for x in ids for y in ids if x < y), default=None,
                key=lambda p: len(g.isolated({f"d{p[0]}", f"d{p[1]}"})) - len(g.isolated({f"d{p[0]}"}) | g.isolated({f"d{p[1]}"})))
    pair = None
    if worst:
        extra = g.isolated({f"d{worst[0]}", f"d{worst[1]}"})
        if len(extra) > len(g.isolated({f"d{worst[0]}"}) | g.isolated({f"d{worst[1]}"})):
            pair = {"a": devs[worst[0]].name, "b": devs[worst[1]].name, "n": len(extra)}
    return {"rows": rows, "waves": ws, "pair": pair}


# --------------------------------------------------------------------------- ruta (mapa, ficha)
@router.get("/api/impact/{dev_id}")
def api_impact(request: Request, dev_id: int):
    user = require(request)
    check_device(user, dev_id)
    res = analyze(_graph(), [dev_id], user=user)
    return JSONResponse(res)


__all__ = ["router", "analyze", "for_change", "waves", "plan", "audit_text", "REBOOT"]
