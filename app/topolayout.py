"""Distribución del Mapa de la red: preferencias de cada usuario, «Mi distribución» (posiciones guardadas por usuario o
para todo el equipo), sitios renombrados o con equipos movidos y el plano de fondo («Sobre un plano»).
Las posiciones de cada distribución se calculan en el navegador (static/topo-layouts.js)."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .auth import Forbidden, require
from .config import DATA_DIR
from .db import get_setting, now, set_setting
from .i18n import _
from .web import log_action

router = APIRouter()
MODES = ("free", "down", "right", "radial", "sites", "roles", "circle", "fold", "plan", "mine")
SUBS = ("none", "ip", "mac")
PLAN_MAX = 8 * 1024 * 1024


def _json(key: str, default):
    try:
        v = json.loads(get_setting(key, "") or "null")
        return v if v is not None else default
    except ValueError:
        return default


def prefs(user) -> dict:
    p = _json(f"topo_pref_{user.id}", {})
    out = {"mode": "free", "center": "", "sub": "mac", "fit": True, "remember": True}
    if p.get("remember", True):
        out.update({k: v for k, v in p.items() if k in out})
    else:
        out["remember"] = False
    if out["mode"] not in MODES:
        out["mode"] = "free"
    return out


def plan_path() -> Path | None:
    for ext in ("png", "jpg"):
        p = DATA_DIR / "topology" / f"plan.{ext}"
        if p.exists():
            return p
    return None


def _img_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":  # JPEG: busca el marcador SOF
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def page_data(user) -> dict:
    """Lo que necesita el mapa: preferencias, posiciones guardadas, sitios y plano."""
    mine = _json(f"topo_my_{user.id}", {})
    team = _json("topo_team_pos", {})
    plan = _json("topo_plan", {})
    p = plan_path()
    return {"prefs": prefs(user), "mine": mine.get("pos") or team.get("pos") or {},
            "mine_at": mine.get("at") or team.get("at") or "", "mine_scope": "me" if mine.get("pos") else ("team" if team.get("pos") else ""),
            "sites": _json("topo_sites", {}),
            "plan": {"url": f"/topology/plan?v={plan.get('v', 0)}" if p else "", "w": plan.get("w"), "h": plan.get("h"),
                     "pos": plan.get("pos", {})}}


@router.post("/api/topology/prefs")
async def save_prefs(request: Request):
    user = require(request)
    b = await request.json()
    p = {"mode": b.get("mode") if b.get("mode") in MODES else "free", "center": str(b.get("center") or "")[:200],
         "sub": b.get("sub") if b.get("sub") in SUBS else "mac", "fit": bool(b.get("fit", True)),
         "remember": bool(b.get("remember", True))}
    if not p["remember"]:
        p = {"remember": False}
    set_setting(f"topo_pref_{user.id}", json.dumps(p))
    return JSONResponse({"ok": True})


def _pos(b) -> dict:
    out = {}
    for k, v in (b.get("pos") or {}).items():
        try:
            out[str(k)[:200]] = [round(float(v[0]), 1), round(float(v[1]), 1)]
        except (TypeError, ValueError, IndexError):
            continue
    return out


@router.post("/api/topology/positions")
async def save_positions(request: Request):
    """«Mi distribución»: solo para mí o, los administradores, para todo el equipo. scope=clear la borra."""
    user = require(request)
    b = await request.json()
    scope = b.get("scope")
    if scope == "team" and user.role != "admin":
        raise Forbidden()
    key = "topo_team_pos" if scope == "team" else f"topo_my_{user.id}"
    if b.get("clear"):
        set_setting(key, "{}")
        return JSONResponse({"ok": True})
    pos = _pos(b)
    at = now().strftime("%Y-%m-%d %H:%M")
    set_setting(key, json.dumps({"pos": pos, "at": at}))
    if scope == "team":
        log_action(request, user, "topology_layout_team", f"{len(pos)} equipos")
    return JSONResponse({"ok": True, "at": at})


@router.post("/api/topology/sites")
async def save_sites(request: Request):
    """Sitios: renombrar uno ({rename: [clave, nombre]}) o mover un equipo a otro ({move: [id, sitio]}). Solo administradores."""
    user = require(request, "admin")
    b = await request.json()
    s = _json("topo_sites", {})
    s.setdefault("names", {})
    s.setdefault("assign", {})
    if b.get("rename"):
        k, v = str(b["rename"][0])[:200], str(b["rename"][1]).strip()[:80]
        if v:
            s["names"][k] = v
        else:
            s["names"].pop(k, None)
    if b.get("move"):
        nid, site = str(b["move"][0])[:200], str(b["move"][1]).strip()[:80]
        if site:
            s["assign"][nid] = site
        else:
            s["assign"].pop(nid, None)
    set_setting("topo_sites", json.dumps(s))
    log_action(request, user, "topology_sites", json.dumps(b, ensure_ascii=False)[:300])
    return JSONResponse({"ok": True, "sites": s})


@router.get("/topology/plan")
def plan_image(request: Request):
    require(request)
    p = plan_path()
    if p is None:
        return Response(status_code=404)
    return Response(p.read_bytes(), media_type="image/png" if p.suffix == ".png" else "image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.post("/topology/plan")
async def plan_upload(request: Request, file: UploadFile):
    """Subir el plano (PNG o JPG, hasta 8 MB). Solo administradores."""
    user = require(request, "admin")
    data = await file.read(PLAN_MAX + 1)
    size = _img_size(data) if len(data) <= PLAN_MAX else None
    if not size:
        return JSONResponse({"error": _("Sube una imagen PNG o JPG de hasta 8 MB.")}, 400)
    d = DATA_DIR / "topology"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("plan.*"):
        old.unlink()
    ext = "png" if data[:4] == b"\x89PNG" else "jpg"
    (d / f"plan.{ext}").write_bytes(data)
    plan = _json("topo_plan", {})
    plan.update({"w": size[0], "h": size[1], "v": int(plan.get("v", 0)) + 1})
    set_setting("topo_plan", json.dumps(plan))
    log_action(request, user, "topology_plan_upload", f"{size[0]}×{size[1]}")
    return JSONResponse({"ok": True, "url": f"/topology/plan?v={plan['v']}", "w": size[0], "h": size[1]})


@router.post("/api/topology/plan-pos")
async def plan_pos(request: Request):
    """Lugar de un equipo en el plano (fracciones del ancho y del alto); sin x/y vuelve a «Sin ubicación»."""
    user = require(request, "admin")
    b = await request.json()
    nid = str(b.get("id") or "")[:200]
    plan = _json("topo_plan", {})
    pos = plan.setdefault("pos", {})
    try:
        x, y = float(b["x"]), float(b["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError
        pos[nid] = [round(x, 4), round(y, 4)]
    except (KeyError, TypeError, ValueError):
        pos.pop(nid, None)
    set_setting("topo_plan", json.dumps(plan))
    del user
    return JSONResponse({"ok": True})
