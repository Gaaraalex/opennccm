"""«Respaldar todos» (y un grupo o los marcados) con su progreso: el respaldo corre en el servidor, de a N a la vez
(ajuste backup_concurrency, 4 de fábrica), y la página lo muestra en vivo (static/bulkbk.js consulta /backup/job cada
2 s): barra, contadores, el paso de cada equipo, el resumen al terminar, cancelar y reintentar los que fallaron.
Diseño de «Diseño UX» aprobado por el usuario (maqueta «Dispositivos: progreso de Respaldar todos»).

Solo hay un respaldo de varios equipos a la vez: si se intenta otro (otra pestaña, otro usuario) se responde «Ya hay
un respaldo en curso» y se muestra el que corre. Al terminar queda en Auditoría y en las notificaciones (la campana)."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import services
from .auth import device_scope, filter_ids, require
from .db import Device, SessionLocal, get_setting, now, select
from .errors import short_error
from .i18n import _, _f
from .web import audit, ip

router = APIRouter()
_lock = threading.Lock()
_job: dict | None = None
STEPS = {"connect": "Conectando…", "read": "Leyendo la configuración…", "save": "Guardando…"}


def concurrency() -> int:
    try:
        return max(1, min(16, int(get_setting("backup_concurrency", "4") or 4)))
    except ValueError:
        return 4


def state() -> dict | None:
    """Copia del respaldo en curso o del último (hasta que se cierra su panel)."""
    with _lock:
        if _job is None:
            return None
        j = dict(_job)
        j["devs"] = [dict(d) for d in _job["devs"]]
    done = [d for d in j["devs"] if d["st"] in ("ok", "err")]
    j["n"] = {k: sum(1 for d in j["devs"] if d["st"] == k) for k in ("ok", "err", "run", "q", "cancel")}
    j["pct"] = round(100 * len(done) / j["total"]) if j["total"] else 100
    j["elapsed"] = round((j["ended"] or time.time()) - j["started"])
    durs = [d["secs"] for d in done if d.get("secs")]
    left = j["n"]["q"] + j["n"]["run"]
    j["eta"] = round(sum(durs) / len(durs) * left / j["conc"]) if durs and left and not j["ended"] else None
    j["changed"] = sum(1 for d in j["devs"] if d.get("changed"))
    j["now"] = [d["name"] for d in j["devs"] if d["st"] == "run"]
    return j


def start(ids: list[int], username: str, what: str = "all") -> tuple[bool, dict]:
    """Lanza el respaldo de esos equipos. (False, el que corre) si ya hay uno en curso."""
    global _job
    with SessionLocal() as db:
        # los desactivados no entran: están aislados (OpenNCCM no se conecta con ellos)
        devs = [d for d in db.scalars(select(Device).where(Device.id.in_(ids), Device.enabled.is_(True)).order_by(Device.name))]
        rows = [{"id": d.id, "name": d.name, "st": "q", "step": "", "why": "", "changed": False, "fw": d.os_version or "",
                 "prev_at": d.last_backup_at.isoformat() if d.last_backup_at else None} for d in devs]
    with _lock:
        if _job is not None and not _job["ended"]:
            return False, state_unlocked()
        _job = {"id": int(time.time() * 1000), "user": username, "what": what, "started": time.time(), "ended": None,
                "total": len(rows), "conc": concurrency(), "cancel": False, "devs": rows, "closed": False}
        job = _job
    threading.Thread(target=_run, args=(job,), name="ncm-bulk", daemon=True).start()
    return True, state()


def state_unlocked() -> dict:
    j = dict(_job)
    j["devs"] = [dict(d) for d in _job["devs"]]
    return j


def _one(job: dict, row: dict) -> None:
    with _lock:
        if job["cancel"]:
            row["st"] = "cancel"
            return
        row["st"], row["step"], row["t0"] = "run", STEPS["connect"], time.time()

    def step(name):
        with _lock:
            row["step"] = STEPS.get(name, row["step"])
    services.STEP.cb = step
    try:
        res = services.backup_device(row["id"], "manual", job["user"])
    except Exception as e:  # noqa: BLE001
        res = "error"
        services.log.warning("Respaldar todos: %s: %s", row["name"], e)
    finally:
        services.STEP.cb = None
    with SessionLocal() as db:
        d = db.get(Device, row["id"])
        err = (d.last_error or "") if d else ""
        fw = (d.os_version or "") if d else ""
        at = d.last_backup_at.isoformat() if d and d.last_backup_at else None
        drift = bool(d.baseline_drift) if d else False
    with _lock:
        row["secs"] = round(time.time() - row.pop("t0", time.time()), 1)
        row["st"] = "err" if res in ("error", "missing", "busy") else "ok"
        row["changed"] = res == "changed"
        row["fw"], row["at"], row["step"], row["drift"] = fw, at, "", drift
        if row["st"] == "err":
            row["why"] = _why(err) if res == "error" else (_("Ocupado con otra tarea") if res == "busy" else _("Ya no existe"))


def _why(err: str) -> str:
    """El motivo en palabras, como en la maqueta: «No responde por SSH (tiempo agotado a los 30 s)»…"""
    s = short_error(err)
    low = err.lower()
    if s in (_("tiempo agotado"), _("sin respuesta por SSH")):
        if "timed out" in low or "timeout" in low or "10060" in low:
            return _f("No responde por SSH (tiempo agotado a los {0} s)", 20)  # conn_timeout de services.connect
        return _("No responde por SSH")
    if s == _("credencial rechazada"):
        return _("Usuario o contraseña rechazados")
    return s[:1].upper() + s[1:]


def _run(job: dict) -> None:
    with ThreadPoolExecutor(max_workers=job["conc"], thread_name_prefix="ncm-bulk") as pool:
        list(pool.map(lambda r: _one(job, r), job["devs"]))
    with _lock:
        job["ended"] = time.time()
    j = state()
    ok, er, secs = j["n"]["ok"], j["n"]["err"], j["elapsed"]
    if job["cancel"]:
        msg = _f("Respaldo cancelado: {0} terminaron y quedan guardados.", ok + er)
    else:
        msg = summary(ok, er)
    services.add_event("info" if not er else "warning", "Respaldar todos",
                       msg + " · " + _f("{0} equipos en {1}", j["total"], dur(secs)), notify=False)
    audit(job["user"], "backup_all_done", f"{msg} · {j['total']} equipos · {dur(secs)} · {j['changed']} cambiaron")


def summary(ok: int, er: int) -> str:
    if not er:
        return _f("Respaldo terminado: {0} correctos", ok) if ok != 1 else _("Respaldo terminado: 1 correcto")
    c = _("1 correcto") if ok == 1 else _f("{0} correctos", ok)
    e = _("1 con error") if er == 1 else _f("{0} con error", er)
    return _f("Respaldo terminado: {0} y {1}", c, e)


def dur(secs: int) -> str:
    m, s = divmod(int(secs), 60)
    return _f("{0} min {1} s", m, s) if m else _f("{0} s", s)


# --------------------------------------------------------------------------- rutas
def _ids_for(user, group: int | None) -> list[int]:
    return filter_ids(user, services.device_ids(group))


@router.post("/backup/all")
async def backup_all(request: Request):
    user = require(request, "operator")
    form = await request.form()
    gid = int(form.get("group") or 0) or None
    ok, j = start(_ids_for(user, gid), user.username, "group" if gid else "all")
    if ok:
        audit(user.username, "backup_all", _f("{0} equipos", j["total"]), ip(request))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": ok, "error": "" if ok else _("Ya hay un respaldo en curso"), "job": state()}, 200 if ok else 409)
    return RedirectResponse("/devices", 303)


def start_selected(user, ids: list[int], request) -> bool:
    """«Respaldar» de los marcados en la lista (mismo progreso)."""
    ok, j = start(filter_ids(user, ids), user.username, "selected")
    if ok:
        audit(user.username, "backup_selected", _f("{0} equipos", j["total"]), ip(request))
    return ok


@router.get("/backup/job")
def backup_job(request: Request):
    require(request)
    j = state()
    if j and not j["closed"]:
        scope = device_scope(require(request))
        if scope is not None:
            j["devs"] = [d for d in j["devs"] if d["id"] in scope]
    return JSONResponse({"job": j if j and not j["closed"] else None, "at": now().isoformat()})


@router.post("/backup/job/cancel")
def backup_cancel(request: Request):
    user = require(request, "operator")
    with _lock:
        if _job and not _job["ended"]:
            _job["cancel"] = True
            for d in _job["devs"]:
                if d["st"] == "q":
                    d["st"] = "cancel"
    audit(user.username, "backup_all_cancel", "")
    return JSONResponse({"ok": True, "job": state()})


@router.post("/backup/job/retry")
def backup_retry(request: Request):
    """«Reintentar los N»: respalda de nuevo solo los que fallaron."""
    user = require(request, "operator")
    j = state()
    ids = [d["id"] for d in (j or {}).get("devs", []) if d["st"] == "err"]
    if not ids:
        return JSONResponse({"ok": False, "error": _("No hay equipos con error para reintentar.")}, 400)
    ok, nj = start(filter_ids(user, ids), user.username, "retry")
    return JSONResponse({"ok": ok, "error": "" if ok else _("Ya hay un respaldo en curso"), "job": state()}, 200 if ok else 409)


@router.post("/backup/job/close")
def backup_close(request: Request):
    require(request)
    with _lock:
        if _job and _job["ended"]:
            _job["closed"] = True
    return JSONResponse({"ok": True})
