"""Correlación con Zabbix, que muestra los problemas y algunas métricas de Zabbix junto a la línea de tiempo de cambios
de cada equipo, para ver de un vistazo si un problema empezó justo después de un cambio de configuración.

Se activa en Administración › Ajustes › Zabbix (dirección, token de API cifrado y verificación del certificado). El
equipo se busca en Zabbix por su dirección (interfaz con esa IP o nombre DNS) y, si no, por su nombre. La página del
equipo pide los datos aparte (GET /devices/{id}/zabbix), así que un Zabbix lento o caído nunca frena la página: solo
esa tarjeta lo dice. Las respuestas se guardan un minuto en memoria y la búsqueda del host, diez.

Llamadas a la API (iguales de 6.0 a 7.4): hostinterface.get / host.get (buscar el host), event.get (problemas que
empezaron en el periodo y la hora de su resolución), problem.get (los que siguen abiertos y empezaron antes), item.get
(qué métricas hay) e history.get (hasta 3 días) o trend.get (periodos más largos)."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import zabbix
from .auth import check_device, require
from .db import ConfigVersion, Device, SessionLocal, get_setting
from .i18n import N_, _, _f

router = APIRouter()
# origen de cada versión, con los mismos nombres que la línea de tiempo de la ficha
TRIGGERS = {"manual": N_("Manual"), "scheduled": N_("Programado"), "syslog": "Syslog", "push": "Push", "api": "API",
            "console": N_("Consola")}
SEVERITY = {0: N_("Sin clasificar"), 1: N_("Información"), 2: N_("Aviso"), 3: N_("Media"), 4: N_("Alta"),
            5: N_("Desastre")}
# métricas que se buscan en el host (prefijo de la clave en Zabbix), en este orden; la primera que exista se muestra
METRICS = [("icmppingsec", N_("Latencia (ping)"), "ms", 1000.0), ("icmppingloss", N_("Pérdida de paquetes"), "%", 1.0),
           ("system.cpu.util", N_("CPU"), "%", 1.0), ("vm.memory.util", N_("Memoria"), "%", 1.0)]
RANGES = (1, 7, 30)        # días que se pueden elegir
RELATED_MIN = 120          # un problema que empieza hasta 2 h después de un cambio se marca como posible relación
MAX_POINTS = 360           # puntos por métrica que se envían al navegador
_cache: dict = {}          # (dev_id, días) -> (cuándo, datos)
_hosts: dict = {}          # (url, dirección, nombre) -> (cuándo, host o None)
_lock = threading.Lock()


def enabled() -> bool:
    return get_setting("zabbix_link", "0") == "1" and bool(get_setting("zabbix_url", "")) \
        and bool(get_setting("zabbix_token", ""))


def client(url: str | None = None, token: str | None = None, verify: bool | None = None) -> zabbix.Client:
    """Cliente conectado con el token guardado (o con los valores que se pasen, para «Probar conexión»)."""
    url = url if url is not None else get_setting("zabbix_url", "")
    verify = verify if verify is not None else get_setting("zabbix_verify", "1") == "1"
    cli = zabbix.Client(url, verify=verify)
    cli.connect(token=token if token is not None else get_setting("zabbix_token", ""))
    return cli


def web_base(cli: zabbix.Client) -> str:
    return cli.url.removesuffix("/api_jsonrpc.php")


def test(url: str, token: str, verify: bool) -> tuple[bool, str]:
    """«Probar conexión»: versión y cuántos hosts ve el token."""
    try:
        cli = client(url, token, verify)
        n = cli.call("host.get", {"countOutput": True})
    except zabbix.ZabbixError as e:
        return False, str(e)
    return True, _f("Conectado a Zabbix {0}: el token ve {1} hosts.", cli.version, n)


def find_host(cli: zabbix.Client, dev: Device) -> dict | None:
    """El host de Zabbix de un equipo: por la dirección (IP o DNS de una interfaz) y, si no, por el nombre."""
    key = (cli.url, (dev.host or "").lower(), (dev.name or "").lower())
    with _lock:
        hit = _hosts.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    name = (dev.name or "").strip()
    found = None
    hostids: list[str] = []
    addr = (dev.host or "").strip()
    if addr:
        for field in ("ip", "dns"):
            rows = cli.call("hostinterface.get", {"output": ["hostid"], "filter": {field: addr}}) or []
            hostids += [r["hostid"] for r in rows if r.get("hostid") not in hostids]
    if hostids:
        hosts = cli.call("host.get", {"output": ["hostid", "host", "name"], "hostids": hostids}) or []
        found = next((h for h in hosts if name.lower() in (h.get("name", "").lower(), h.get("host", "").lower())),
                     hosts[0] if hosts else None)
    if found is None and name:
        hosts = cli.call("host.get", {"output": ["hostid", "host", "name"], "search": {"host": name, "name": name},
                                      "searchByAny": True, "limit": 50}) or []
        found = next((h for h in hosts if name.lower() in (h.get("name", "").lower(), h.get("host", "").lower())), None)
    with _lock:
        _hosts[key] = (time.time(), found)
    return found


def _downsample(points: list[tuple[int, float]], n: int = MAX_POINTS) -> list[list]:
    """Promedio por tramos de tiempo iguales: como mucho n puntos, sin perder los picos de golpe (se guarda el máximo)."""
    if len(points) <= n:
        return [[t, round(v, 3)] for t, v in points]
    t0, t1 = points[0][0], points[-1][0]
    step = max((t1 - t0) / n, 1)
    out, cur, acc = [], None, []
    for t, v in points:
        b = int((t - t0) / step)
        if cur is not None and b != cur:
            out.append([int(sum(x for x, _y in acc) / len(acc)), round(max(y for _x, y in acc), 3)])
            acc = []
        cur = b
        acc.append((t, v))
    if acc:
        out.append([int(sum(x for x, _y in acc) / len(acc)), round(max(y for _x, y in acc), 3)])
    return out


def problems(cli: zabbix.Client, hid: str, t0: int, t1: int) -> list[dict]:
    out_fields = ["eventid", "clock", "name", "severity", "r_eventid", "objectid", "acknowledged"]
    evs = cli.call("event.get", {"output": out_fields, "source": 0, "object": 0, "value": 1, "hostids": [hid],
                                 "time_from": t0, "time_till": t1, "sortfield": ["clock"], "sortorder": "ASC",
                                 "limit": 500}) or []
    seen = {e["eventid"] for e in evs}
    # los que siguen abiertos aunque hayan empezado antes del periodo
    for p in cli.call("problem.get", {"output": out_fields, "hostids": [hid], "source": 0, "object": 0}) or []:
        if p["eventid"] not in seen and int(p["clock"]) < t0:
            evs.append(p)
            seen.add(p["eventid"])
    r_ids = [e["r_eventid"] for e in evs if str(e.get("r_eventid", "0")) != "0"]
    ends = {}
    if r_ids:
        ends = {r["eventid"]: int(r["clock"]) for r in
                cli.call("event.get", {"output": ["eventid", "clock"], "eventids": r_ids}) or []}
    base = web_base(cli)
    res = []
    for e in sorted(evs, key=lambda x: int(x["clock"])):
        sev = int(e.get("severity") or 0)
        res.append({"id": e["eventid"], "name": e.get("name") or "", "sev": sev, "sev_label": _(SEVERITY.get(sev, "")),
                    "start": int(e["clock"]), "end": ends.get(e.get("r_eventid")),
                    "ack": str(e.get("acknowledged", "0")) == "1",
                    "url": f"{base}/tr_events.php?triggerid={e.get('objectid')}&eventid={e['eventid']}"})
    return res


def metrics(cli: zabbix.Client, hid: str, t0: int, t1: int) -> list[dict]:
    items = cli.call("item.get", {"output": ["itemid", "key_", "name", "value_type", "units"], "hostids": [hid],
                                  "filter": {"value_type": [0, 3]}, "monitored": True, "sortfield": "key_"}) or []
    res = []
    for prefix, label, unit, mult in METRICS:
        it = next((i for i in items if i["key_"] == prefix or i["key_"].startswith(prefix + "[")), None)
        if it is None:
            continue
        if t1 - t0 <= 3 * 86400:
            rows = cli.call("history.get", {"output": ["clock", "value"], "history": int(it["value_type"]),
                                            "itemids": [it["itemid"]], "time_from": t0, "time_till": t1,
                                            "sortfield": "clock", "sortorder": "ASC", "limit": 20000}) or []
            pts = [(int(r["clock"]), float(r["value"]) * mult) for r in rows]
        else:
            rows = cli.call("trend.get", {"output": ["clock", "value_avg", "value_max"], "itemids": [it["itemid"]],
                                          "time_from": t0, "time_till": t1, "limit": 5000}) or []
            pts = sorted((int(r["clock"]), float(r["value_max"]) * mult) for r in rows)
        res.append({"key": prefix, "label": _(label), "unit": unit, "item": it.get("name") or it["key_"],
                    "points": _downsample(pts)})
    return res


def changes(dev_id: int, t0: int) -> list[dict]:
    """Versiones del equipo en el periodo, numeradas como en la línea de tiempo (v1…vN, todas juntas)."""
    with SessionLocal() as db:
        ids = list(db.scalars(select(ConfigVersion.id).where(ConfigVersion.device_id == dev_id)
                              .order_by(ConfigVersion.id)))
        rows = db.execute(select(ConfigVersion.id, ConfigVersion.kind, ConfigVersion.created_at, ConfigVersion.trigger,
                                 ConfigVersion.username, ConfigVersion.added, ConfigVersion.removed)
                          .where(ConfigVersion.device_id == dev_id,
                                 ConfigVersion.created_at >= datetime.fromtimestamp(t0))
                          .order_by(ConfigVersion.id)).all()
    num = {v: i + 1 for i, v in enumerate(ids)}
    prev_of = {v: (ids[i - 1] if i else None) for i, v in enumerate(ids)}
    return [{"id": r.id, "n": num[r.id], "kind": r.kind, "t": int(r.created_at.timestamp()),
             "src": _(TRIGGERS.get(r.trigger, r.trigger)), "who": r.username or "", "added": r.added,
             "removed": r.removed, "first": num[r.id] == 1, "prev": prev_of.get(r.id)} for r in rows]


def relate(chg: list[dict], probs: list[dict]) -> None:
    """Marca en cada problema el último cambio (con diferencias) hecho hasta RELATED_MIN minutos antes de que empezara."""
    real = [c for c in chg if c["first"] or c["added"] or c["removed"]]
    for p in probs:
        before = [c for c in real if c["t"] <= p["start"] and p["start"] - c["t"] <= RELATED_MIN * 60]
        if before:
            c = before[-1]
            p["after"] = {"id": c["id"], "n": c["n"], "prev": c["prev"], "min": max(0, round((p["start"] - c["t"]) / 60))}


def data(dev: Device, days: int) -> dict:
    key = (dev.id, days)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    t1 = int(time.time())
    t0 = t1 - days * 86400
    try:
        cli = client()
        host = find_host(cli, dev)
        if host is None:
            res = {"ok": False, "reason": "nohost",
                   "msg": _f("No se encontró este equipo en Zabbix (se buscó un host con la dirección {0} o con el nombre {1}).", dev.host or "—", dev.name)}
        else:
            probs = problems(cli, host["hostid"], t0, t1)
            res = {"ok": True, "version": cli.version, "from": t0, "till": t1,
                   "host": {"id": host["hostid"], "name": host.get("name") or host.get("host"),
                            "url": f"{web_base(cli)}/zabbix.php?action=problem.view&hostids%5B%5D={host['hostid']}"
                                   "&filter_set=1"},
                   "problems": probs, "metrics": metrics(cli, host["hostid"], t0, t1)}
    except zabbix.ZabbixError as e:
        res = {"ok": False, "reason": "error", "msg": _f("Zabbix no respondió bien: {0}", e)}
    with _lock:
        _cache[key] = (time.time(), res)
        if len(_cache) > 500:  # nunca crece sin límite
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
                _cache.pop(k, None)
    return res


@router.get("/devices/{dev_id}/zabbix")
def device_zabbix(request: Request, dev_id: int, days: int = 7):
    user = require(request)
    check_device(user, dev_id)
    days = days if days in RANGES else 7
    if not enabled():
        return JSONResponse({"ok": False, "reason": "off", "msg": _("La integración con Zabbix está desactivada.")})
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
    if dev is None:
        return JSONResponse({"ok": False, "reason": "nohost", "msg": _("El equipo no existe.")}, status_code=404)
    res = dict(data(dev, days))
    t0 = res.get("from") or int(time.time()) - days * 86400
    res["changes"] = changes(dev_id, t0)
    if res.get("ok"):
        res["problems"] = [dict(p) for p in res["problems"]]
        relate(res["changes"], res["problems"])
    return JSONResponse(res)


@router.post("/settings/zabbix-test")
async def settings_zabbix_test(request: Request):
    """«Probar conexión» con lo escrito en Ajustes › Zabbix (sin guardarlo); sin token escrito, usa el guardado."""
    from .web import log_action
    user = require(request, "admin")
    form = await request.form()
    url = (form.get("zabbix_url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "msg": _("Escribe la dirección de Zabbix.")})
    token = (form.get("zabbix_token") or "").strip() or get_setting("zabbix_token", "")
    if not token:
        return JSONResponse({"ok": False, "msg": _("Falta el token de API de Zabbix.")})
    ok, msg = test(url, token, form.get("zabbix_verify") == "on")
    log_action(request, user, "zabbix_test", url)
    return JSONResponse({"ok": ok, "msg": msg})
