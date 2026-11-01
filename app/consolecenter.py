"""Operar › Consola: el centro de consola con seis secciones.

- Sesiones: lanzador (favoritos, recientes, grupos, Ctrl K), varias consolas a la vez (1, 2 o 4), compartir en solo
  lectura y archivos del equipo por SFTP (bajar y subir, en Auditoría).
- Multi-equipo: el mismo comando de consulta en N equipos, con las respuestas agrupadas en iguales y distintas; un
  cambio no se ejecuta directo: se crea una solicitud de cambio.
- Recetas: guías paso a paso (app/recipes.py) y las de cada usuario.
- Grabaciones: cada sesión grabada, con búsqueda en lo escrito y lo visto y un reproductor.
- Herramientas: ping, traceroute, puerto TCP y DNS desde el servidor de OpenNCCM.
- Acceso (administradores): consola por rol, por grupo (comandos prohibidos y aprobación previa) y pedidos de acceso.
"""
from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import socket
import stat
import subprocess
import time
from concurrent.futures import as_completed
from datetime import timedelta

import paramiko
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import select

from . import consolecore as cc
from . import console, recipes
from .auth import Forbidden, check_device, device_scope, require
from .crypto import decrypt
from .db import (ConsoleAccessRequest, ConsoleSession, Device, DeviceGroup, Recipe, SessionLocal, audit, get_setting,
                 now, set_setting)
from .drivers import get_profile
from .i18n import _, _f
from .web import flash, go, render

router = APIRouter()
TABS = ("sesiones", "multi", "recetas", "grabaciones", "herramientas", "acceso")
TARGET = re.compile(r"^(?!-)[A-Za-z0-9.:\-]{1,253}$")


def _devices(user) -> list[dict]:
    scope = device_scope(user)
    with SessionLocal() as db:
        q = select(Device).where(Device.enabled.is_(True)).order_by(Device.name)
        out = []
        for d in db.scalars(q):
            if scope is not None and d.id not in scope:
                continue
            prof = get_profile(d.device_type)
            out.append({"id": d.id, "name": d.name, "host": d.host, "vend": prof.vendor[:3].upper(), "vendor": prof.vendor,
                        "os": cc.os_of(d.device_type), "label": prof.label, "group": d.group.name if d.group else "",
                        "gid": d.group_id or 0, "status": d.last_status})
        return out


def _favs(username: str) -> list[int]:
    try:
        return [int(x) for x in json.loads(get_setting(f"console_fav_{username}", "[]"))]
    except (ValueError, TypeError):
        return []


@router.get("/console")
def center(request: Request, tab: str = "sesiones", q: str = "", rec: int = 0, empty: int = 0):
    user = require(request)
    if not console.console_allowed(user):
        raise Forbidden()
    tab = tab if tab in TABS else "sesiones"
    if tab == "acceso" and user.role != "admin":
        tab = "sesiones"
    devs = _devices(user)
    ids = {d["id"] for d in devs}
    with SessionLocal() as db:
        recent = []
        for (did,) in db.execute(select(ConsoleSession.device_id).where(ConsoleSession.username == user.username)
                                 .order_by(ConsoleSession.id.desc()).limit(60)):
            if did in ids and did not in recent:
                recent.append(did)
        groups = [{"id": g.id, "name": g.name} for g in db.scalars(select(DeviceGroup).order_by(DeviceGroup.name))]
        pending = db.scalar(select(ConsoleAccessRequest.id).where(ConsoleAccessRequest.status == "pending").limit(1)) is not None
        reqs = []
        if user.role == "admin":
            for r in db.scalars(select(ConsoleAccessRequest).order_by(ConsoleAccessRequest.id.desc()).limit(40)):
                reqs.append({"id": r.id, "u": r.username, "d": r.device.name if r.device else "?", "why": r.reason,
                             "h": r.hours, "status": r.status, "when": r.created_at, "until": r.until, "by": r.reviewed_by})
    n_pending = sum(1 for r in reqs if r["status"] == "pending")
    from . import assistant
    ctx = {"tab": tab, "devs": devs, "favs": [i for i in _favs(user.username) if i in ids], "recent": recent[:8],
           "groups": groups, "reqs": reqs, "n_pending": n_pending if user.role == "admin" else (1 if pending else 0),
           "ai": assistant.panel_info(user), "q": q, "rec": rec}
    # las seis secciones van en la misma página (las sesiones abiertas siguen vivas al cambiar de sección)
    ctx["recipes"] = all_recipes(user)
    ctx["cats"] = {k: _(v) for k, v in recipes.CATEGORIES.items()}
    ctx["recs"] = recordings(user, q, bool(empty))
    ctx["empty"] = bool(empty)
    if user.role == "admin":
        ctx["acc"] = {"roles": {r: console.role_mode(r) for r in ("admin", "operator", "viewer")},
                      "record": get_setting("console_record", "1") == "1",
                      "idle": get_setting("console_idle_minutes", "15"), "deny": get_setting("console_enforce_deny", "1") == "1",
                      "days": get_setting("console_retention_days", "180"), "rules": console.group_rules()}
    return render(request, "console_center.html", user, **ctx)


@router.post("/console/fav")
async def fav(request: Request):
    user = require(request)
    data = await request.json()
    did, on = int(data.get("id") or 0), bool(data.get("on"))
    check_device(user, did)
    f = [x for x in _favs(user.username) if x != did] + ([did] if on else [])
    set_setting(f"console_fav_{user.username}", json.dumps(f[-50:]))
    return JSONResponse({"ok": True, "favs": f})


@router.get("/console/live")
def live_list(request: Request):
    """Sesiones abiertas de este usuario (para «Compartir» en cada panel)."""
    user = require(request)
    return JSONResponse([{"sid": x.sid, "device_id": x.dev_id, "device": x.dev_name, "watchers": sorted(set(x.watchers.values()))}
                         for x in list(console.LIVE.values()) if x.owner == user.username])


# --------------------------------------------------------------------------- archivos (SFTP)
def _sftp(dev: Device):
    cred = dev.credential
    if cred is None:
        raise RuntimeError(_("El equipo no tiene credencial asignada."))
    from . import legacy_ssh
    legacy_ssh.enable()
    t = paramiko.Transport((dev.host, dev.port))
    t.banner_timeout = 30
    t.connect(username=cred.username, password=decrypt(cred.password_enc) if cred.password_enc else "")
    return t, paramiko.SFTPClient.from_transport(t)


def _file_dev(user, dev_id: int, write: bool) -> Device:
    check_device(user, dev_id)
    db = SessionLocal()
    dev = db.get(Device, dev_id)
    if dev is None:
        db.close()
        raise Forbidden()
    ok, acc = console._open_allowed(user, dev)
    if not ok or (write and acc["mode"] != "rw"):
        db.close()
        raise Forbidden()
    dev.credential  # noqa: B018 - se carga antes de cerrar la sesión
    db.expunge_all()
    db.close()
    return dev


@router.get("/console/files/{dev_id}")
def files_list(request: Request, dev_id: int, path: str = "/"):
    user = require(request)
    dev = _file_dev(user, dev_id, False)
    try:
        t, s = _sftp(dev)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": _f("No se pudo abrir SFTP: {0}", str(e)[:200])}, 400)
    try:
        items = []
        for a in s.listdir_attr(path or "/"):
            items.append({"n": a.filename, "dir": stat.S_ISDIR(a.st_mode or 0), "size": a.st_size or 0})
        items.sort(key=lambda x: (not x["dir"], x["n"].lower()))
        return JSONResponse({"path": path, "items": items[:500]})
    except OSError as e:
        return JSONResponse({"error": str(e)[:200]}, 400)
    finally:
        s.close()
        t.close()


@router.get("/console/files/{dev_id}/get")
def files_get(request: Request, dev_id: int, path: str):
    user = require(request)
    dev = _file_dev(user, dev_id, False)
    try:
        t, s = _sftp(dev)
        buf = io.BytesIO()
        s.getfo(path, buf)
        s.close()
        t.close()
    except Exception as e:  # noqa: BLE001
        return PlainTextResponse(_f("No se pudo bajar el archivo: {0}", str(e)[:200]), 400)
    audit(user.username, "sftp_get", f"{dev.name}: {path} ({buf.tell()} bytes)")
    name = path.rstrip("/").rsplit("/", 1)[-1] or "archivo"
    return Response(buf.getvalue(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9._-]", "_", name)}"'})


@router.post("/console/files/{dev_id}/put")
async def files_put(request: Request, dev_id: int, file: UploadFile, path: str = "/"):
    user = require(request, "operator")
    dev = _file_dev(user, dev_id, True)
    data = await file.read()
    name = re.sub(r"[/\\]", "_", file.filename or "archivo")
    dest = (path.rstrip("/") or "") + "/" + name
    try:
        t, s = _sftp(dev)
        s.putfo(io.BytesIO(data), dest)
        s.close()
        t.close()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": _f("No se pudo subir el archivo: {0}", str(e)[:200])}, 400)
    audit(user.username, "sftp_put", f"{dev.name}: {dest} ({len(data)} bytes)")
    return JSONResponse({"ok": True, "path": dest})


# --------------------------------------------------------------------------- multi-equipo
def _run_one(dev_id: int, cmd: str) -> tuple[str, str, bool]:
    from . import services
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        name = dev.name
        try:
            conn = services.connect(dev)
        except Exception as e:  # noqa: BLE001
            return name, str(e)[:300], False
    try:
        out = conn.send_command(cmd, read_timeout=60)
        return name, out, True
    except Exception as e:  # noqa: BLE001
        return name, str(e)[:300], False
    finally:
        try:
            conn.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _norm(text: str) -> str:
    return "\n".join(x.rstrip() for x in text.replace("\r", "").strip().splitlines())


@router.post("/console/multi")
async def multi(request: Request):
    user = require(request)
    data = await request.json()
    cmd = str(data.get("command") or "").strip()
    ids = [int(x) for x in data.get("device_ids") or []][:100]
    if not cmd or not ids:
        return JSONResponse({"error": _("Elija equipos y escriba el comando.")}, 400)
    with SessionLocal() as db:
        devs = []
        for i in ids:
            check_device(user, i)
            d = db.get(Device, i)
            if d is None:
                continue
            ok, _acc = console._open_allowed(user, d)
            if not ok:
                return JSONResponse({"error": _f("Sin consola en {0}.", d.name)}, 403)
            devs.append((d.id, cc.os_of(d.device_type)))
    if any(cc.is_change(o, cmd) for _i, o in devs):
        return JSONResponse({"change": True, "msg": _("Este comando cambia la configuración. En multi-equipo no se ejecuta directo: se crea una solicitud de cambio para aprobar, con respaldo y reversión.")})
    from . import services
    t0 = time.time()
    futs = [services.pool.submit(_run_one, i, cmd) for i, _o in devs]
    res = [f.result() for f in as_completed(futs)]
    groups: dict[str, dict] = {}
    failed = []
    for name, out, ok in res:
        if not ok:
            failed.append({"d": name, "err": out})
            continue
        k = hashlib.sha256(_norm(out).encode()).hexdigest()
        groups.setdefault(k, {"devs": [], "out": _norm(out)})["devs"].append(name)
    gl = sorted(groups.values(), key=lambda g: -len(g["devs"]))
    for g in gl:
        g["devs"].sort()
    audit(user.username, "console_multi", f"{len(devs)} equipos: {cmd}")
    same = len(gl[0]["devs"]) if gl else 0
    return JSONResponse({"groups": gl, "failed": failed, "same": same, "diff": sum(len(g["devs"]) for g in gl[1:]),
                         "answered": len(res) - len(failed), "total": len(devs), "ms": int((time.time() - t0) * 1000)})


_CONFIG_WRAP = re.compile(r"^\s*(enable|configure terminal|conf t|configure|end|system-view|return|commit)\s*$", re.I)


@router.post("/console/change-request")
async def change_request(request: Request):
    """Solicitud de cambio desde multi-equipo, una receta o el asistente (pasa por aprobación como cualquier otra)."""
    user = require(request, "operator")
    data = await request.json()
    ids = [int(x) for x in data.get("device_ids") or []][:200]
    for i in ids:
        check_device(user, i)
    body = "\n".join(x for x in str(data.get("body") or "").splitlines() if x.strip() and not _CONFIG_WRAP.match(x))
    if not ids or not body:
        return JSONResponse({"error": _("Elija equipos y escriba los comandos.")}, 400)
    from .main import _create_cr
    cr_id = _create_cr(user, str(data.get("title") or _("Desde la consola"))[:200], body, {}, ids)
    audit(user.username, "change_request", f"#{cr_id} desde la consola")
    return JSONResponse({"ok": True, "url": f"/changes/{cr_id}", "id": cr_id})


# --------------------------------------------------------------------------- recetas
def _uses() -> dict:
    try:
        return json.loads(get_setting("recipe_uses", "{}"))
    except ValueError:
        return {}


def all_recipes(user) -> list[dict]:
    uses = _uses()
    out = []
    for r in recipes.BUILTIN:
        out.append({**r, "name": _(r["name"]), "intro": _(r["intro"]),
                    "vars": [{**v, "l": _(v["l"])} for v in r["vars"]],
                    "steps": [{**s, "t": _(s["t"])} for s in r["steps"]],
                    "scope": "opennccm", "by": "OpenNCCM", "uses": uses.get(r["key"], 0), "mine": False})
    with SessionLocal() as db:
        for r in db.scalars(select(Recipe).order_by(Recipe.id)):
            try:
                d = json.loads(r.data)
            except ValueError:
                continue
            mine = r.owner == user.username
            out.append({**d, "key": f"u{r.id}", "id": r.id, "scope": "mine" if mine else "team", "by": r.owner,
                        "uses": r.uses, "mine": mine})
    return out


def _clean_recipe(d: dict) -> dict:
    steps = [{"t": str(s.get("t") or "")[:500], "c": str(s.get("c") or "")[:4000], "warn": bool(s.get("warn")),
              "check": bool(s.get("check"))} for s in (d.get("steps") or [])[:40]]
    vs = [{"n": re.sub(r"[^\w]", "", str(v.get("n") or ""))[:32], "l": str(v.get("l") or "")[:80], "v": str(v.get("v") or "")[:200]}
          for v in (d.get("vars") or [])[:20] if v.get("n")]
    return {"name": str(d.get("name") or _("Receta sin nombre"))[:200], "vendor": str(d.get("vendor") or "")[:64],
            "os": d.get("os") if d.get("os") in cc.OS_LABEL else "generic", "os_label": str(d.get("os_label") or "")[:64],
            "cat": d.get("cat") if d.get("cat") in recipes.CATEGORIES else "diagnostico",
            "intro": str(d.get("intro") or "")[:2000], "doc": "", "vars": vs, "steps": steps}


@router.post("/console/recipes")
async def recipe_save(request: Request):
    user = require(request, "operator")
    d = await request.json()
    clean = _clean_recipe(d)
    with SessionLocal() as db:
        rid = int(d.get("id") or 0)
        r = db.get(Recipe, rid) if rid else None
        if r is not None and r.owner != user.username and user.role != "admin":
            return JSONResponse({"error": _("Solo quien la creó puede editarla.")}, 403)
        if r is None:
            r = Recipe(owner=user.username, data="")
            db.add(r)
        r.data = json.dumps(clean, ensure_ascii=False)
        db.commit()
        rid = r.id
    audit(user.username, "recipe_save", clean["name"])
    return JSONResponse({"ok": True, "id": rid})


@router.post("/console/recipes/{rid}/delete")
def recipe_delete(request: Request, rid: int):
    user = require(request, "operator")
    with SessionLocal() as db:
        r = db.get(Recipe, rid)
        if r and (r.owner == user.username or user.role == "admin"):
            db.delete(r)
            db.commit()
            audit(user.username, "recipe_delete", str(rid))
    return JSONResponse({"ok": True})


@router.post("/console/recipes/used")
async def recipe_used(request: Request):
    require(request)
    key = str((await request.json()).get("key") or "")
    if key in recipes.BY_KEY:
        u = _uses()
        u[key] = u.get(key, 0) + 1
        set_setting("recipe_uses", json.dumps(u))
    elif key.startswith("u") and key[1:].isdigit():
        with SessionLocal() as db:
            r = db.get(Recipe, int(key[1:]))
            if r:
                r.uses += 1
                db.commit()
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------- grabaciones
def recordings(user, q: str = "", empty: bool = False) -> list[dict]:
    """Sesiones grabadas; las vacías (sin comandos, ya cerradas) solo si se piden."""
    with SessionLocal() as db:
        stmt = select(ConsoleSession).order_by(ConsoleSession.id.desc()).limit(300)
        if user.role != "admin":
            stmt = stmt.where(ConsoleSession.username == user.username)
        rows = list(db.scalars(stmt))
    out = []
    ql = q.strip().lower()
    for r in rows:
        f = console.REC_DIR / f"{r.id}.jsonl"
        if ql:
            try:
                if ql not in f.read_text(encoding="utf-8").lower():
                    continue
            except OSError:
                continue
        if not empty and not r.commands and r.ended_at is not None:
            continue
        dur = int(((r.ended_at or now()) - r.started_at).total_seconds())
        out.append({"id": r.id, "u": r.username, "d": r.device_name, "when": r.started_at, "dur": dur, "cmds": r.commands,
                    "danger": r.danger, "ip": r.ip, "rec": r.recorded and f.exists(), "live": r.ended_at is None})
    return out[:150]


def _rec(user, sid: int) -> tuple[ConsoleSession, list]:
    with SessionLocal() as db:
        r = db.get(ConsoleSession, sid)
        if r is None or (user.role != "admin" and r.username != user.username):
            raise Forbidden()
        db.expunge(r)
    ev = []
    try:
        with open(console.REC_DIR / f"{sid}.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    ev.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return r, ev


@router.get("/console/rec/{sid}")
def rec_events(request: Request, sid: int):
    user = require(request)
    r, ev = _rec(user, sid)
    with SessionLocal() as db:
        d = db.get(Device, r.device_id) if r.device_id else None
        os_key = cc.os_of(d.device_type) if d else "generic"
    danger = [e[0] for e in ev if e[1] == "i" and cc.is_danger(os_key, e[2])]
    return JSONResponse({"events": ev, "danger": danger, "u": r.username, "d": r.device_name, "ip": r.ip,
                         "when": r.started_at.isoformat() if r.started_at else ""})


@router.get("/console/rec/{sid}/export")
def rec_export(request: Request, sid: int):
    user = require(request)
    r, ev = _rec(user, sid)
    head = f"# OpenNCCM · sesión {sid} · {r.username} en {r.device_name} · {r.started_at:%Y-%m-%d %H:%M:%S} · desde {r.ip or '?'}\n"
    body = "".join((f"\n[{e[0]:>8.2f}s] $ {e[2]}\n" if e[1] == "i" else f"\n[{e[0]:>8.2f}s] # {e[2]}\n" if e[1] == "n" else e[2])
                   for e in ev)
    audit(user.username, "console_rec_export", f"sesión {sid}")
    return PlainTextResponse(head + body, headers={"Content-Disposition": f'attachment; filename="sesion-{sid}.txt"'})


# --------------------------------------------------------------------------- herramientas
def _run(args: list[str], timeout: int) -> str:
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)  # noqa: S603
        return (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    except FileNotFoundError:
        return _f("Este servidor no tiene la orden «{0}». Instálela con: sudo apt install {1}", args[0],
                  "traceroute" if "trace" in args[0] else "iputils-ping")
    except subprocess.TimeoutExpired as e:
        part = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return (part + "\n" if part else "") + _("Se acabó el tiempo de espera.")


def _hint(out: str) -> str:
    """Si el sistema no deja abrir sockets ICMP, cómo arreglarlo (instalaciones anteriores a la 2.16)."""
    if re.search(r"Operation not permitted|cap_net_raw|socket: Permission denied", out, re.I):
        out += "\n\n" + _("El servicio de OpenNCCM no tiene permiso para enviar ICMP. Actualice OpenNCCM (sudo opennccm update): la actualización se lo da.")
    return out


@router.post("/console/tools")
async def tools(request: Request):
    user = require(request)
    if not console.console_allowed(user):
        raise Forbidden()
    data = await request.json()
    tool, target = str(data.get("tool") or ""), str(data.get("target") or "").strip()
    if not TARGET.match(target):
        return JSONResponse({"error": _("Escriba una IP o un nombre válido.")}, 400)
    win = platform.system() == "Windows"
    summary: list[list[str]] = []
    if tool == "ping":
        out = _hint(_run(["ping", "-n", "4", target] if win else ["ping", "-c", "4", "-W", "2", target], 25))
        m = re.search(r"(\d+(?:\.\d+)?)% (?:packet )?loss", out) or re.search(r"\((\d+)% (?:perdidos|loss)\)", out)
        if m:
            summary.append([_("Pérdida"), m.group(1) + " %"])
        m = re.search(r"= [\d.]+/([\d.]+)/", out) or re.search(r"(?:Average|Media) = (\d+)ms", out)
        if m:
            summary.append([_("Tiempo medio"), m.group(1) + " ms"])
    elif tool == "traceroute":
        out = _run(["tracert", "-d", "-h", "20", "-w", "1000", target] if win else
                   ["traceroute", "-n", "-w", "1", "-q", "1", "-m", "20", "-N", "4", target], 45)
        if "«traceroute»" in out:
            out = _run(["tracepath", "-n", "-m", "20", target], 45)
        out = _hint(out)
        hops = [x for x in out.splitlines() if re.match(r"\s*\d+\s", x)]
        summary.append([_("Saltos"), str(len(hops))])
    elif tool == "tcp":
        port = int(data.get("port") or 22) if str(data.get("port") or "22").isdigit() else 22
        t0 = time.time()
        try:
            with socket.create_connection((target, port), timeout=5):
                ms = int((time.time() - t0) * 1000)
                out = _f("Puerto {0} de {1}: abierto ({2} ms).", port, target, ms)
                summary += [[_("Estado"), _("abierto")], [_("Tiempo"), f"{ms} ms"]]
        except OSError as e:
            out = _f("Puerto {0} de {1}: no responde ({2}).", port, target, e)
            summary.append([_("Estado"), _("cerrado o filtrado")])
    elif tool == "dns":
        lines = []
        try:
            addrs = sorted({a[4][0] for a in socket.getaddrinfo(target, None)})
            lines += [f"{target} → {a}" for a in addrs]
            summary.append([_("Direcciones"), str(len(addrs))])
        except OSError as e:
            lines.append(_f("No se pudo resolver {0}: {1}", target, e))
        try:
            lines.append(f"{target} ← {socket.gethostbyaddr(target)[0]}")
        except OSError:
            pass
        out = "\n".join(lines)
    else:
        return JSONResponse({"error": _("Herramienta desconocida.")}, 400)
    audit(user.username, "console_tool", f"{tool} {target}")
    dev = next((d for d in _devices(user) if d["host"] == target), None)
    return JSONResponse({"out": out, "summary": summary, "device": dev})


# --------------------------------------------------------------------------- acceso
@router.post("/console/access")
async def access_save(request: Request):
    user = require(request, "admin")
    form = await request.form()
    for r in ("admin", "operator", "viewer"):
        v = form.get(f"role_{r}")
        if v in console.MODES:
            set_setting(f"console_role_{r}", v)
    set_setting("console_record", "1" if form.get("record") == "on" else "0")
    set_setting("console_enforce_deny", "1" if form.get("deny") == "on" else "0")
    idle = (form.get("idle") or "15").strip()
    set_setting("console_idle_minutes", str(min(max(int(idle), 1), 240)) if idle.isdigit() else "15")
    days = (form.get("days") or "180").strip()
    set_setting("console_retention_days", str(min(max(int(days), 1), 3650)) if days.isdigit() else "180")
    rules = {}
    with SessionLocal() as db:
        for g in db.scalars(select(DeviceGroup)):
            mode = "approval" if form.get(f"g{g.id}_mode") == "approval" else "normal"
            deny = "\n".join(x.strip() for x in (form.get(f"g{g.id}_deny") or "").splitlines() if x.strip())[:4000]
            if mode != "normal" or deny:
                rules[str(g.id)] = {"mode": mode, "deny": deny}
    set_setting("console_groups", json.dumps(rules))
    from .auth import clear_settings_cache
    clear_settings_cache()
    audit(user.username, "console_access_save", json.dumps({r: form.get(f"role_{r}") for r in ("admin", "operator", "viewer")}))
    flash(request, _("Acceso a la consola guardado."))
    return go("/console?tab=acceso")


@router.post("/console/access/{rid}/{decision}")
def access_review(request: Request, rid: int, decision: str):
    user = require(request, "admin")
    with SessionLocal() as db:
        r = db.get(ConsoleAccessRequest, rid)
        if r is None or r.status != "pending":
            return go("/console?tab=acceso")
        if decision == "approve":
            r.status, r.until = "approved", now() + timedelta(hours=r.hours)
        else:
            r.status = "rejected"
        r.reviewed_by = user.username
        db.commit()
        detail = f"{r.username} en equipo {r.device_id} · {r.hours} h"
    audit(user.username, f"console_access_{'approve' if decision == 'approve' else 'reject'}", detail)
    flash(request, _("Pedido aprobado.") if decision == "approve" else _("Pedido rechazado."))
    return go("/console?tab=acceso")
