"""Buscar una MAC o una IP: en qué switch y en qué puerto está conectado.

En cada respaldo (y con «Leer las tablas ahora») se leen la tabla MAC y la tabla ARP de los equipos que las entregan y se
guarda un historial MAC → puerto (MacSeen). La búsqueda de la barra superior detecta si lo escrito es una MAC o una IP:
con la IP saca la MAC de las tablas ARP y con la MAC busca los puertos donde se ve. La MAC también aparece en los
uplinks y troncales por donde pasa; se muestra el puerto de acceso (sin vecino LLDP/CDP y con pocas MAC detrás)."""
from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select

from . import consolecore as cc
from .auth import check_device, device_scope, require
from .db import Device, MacSeen, SessionLocal, get_setting, now
from .i18n import _, _f

log = logging.getLogger("opennccm.macfind")
router = APIRouter()

KEEP_DAYS = 90
TRUNK_MACS = 12  # un puerto con más MAC detrás que esto es un uplink o una troncal (o hay un switch sin gestionar)
PARALLEL = 8

# orden de la tabla MAC y de la tabla ARP por familia de consola (consolecore.os_of); la primera que funcione
COMMANDS = {
    "ros": (["/interface bridge host print detail without-paging"], ["/ip arp print detail without-paging"]),
    "ios": (["show mac address-table", "show mac-address-table"], ["show ip arp"]),
    "nxos": (["show mac address-table"], ["show ip arp"]),
    "eos": (["show mac address-table"], ["show ip arp"]),
    "vrp": (["display mac-address"], ["display arp"]),
    "junos": (["show ethernet-switching table"], ["show arp no-resolve"]),
    "exos": (["show fdb"], ["show iparp"]),
    "voss": (["show vlan mac-address-entry"], ["show ip arp"]),
    "fastpath": (["show mac-addr-table"], ["show arp"]),
    "linux": ([], ["ip neigh show"]),
}
_BAD = re.compile(r"(?im)^\s*%|invalid input|unrecognized command|bad command name|syntax error|unknown command|"
                  r"incomplete command|error: ")
_MAC = re.compile(r"(?<![0-9A-Fa-f:.-])((?:[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\2[0-9A-Fa-f]{2}){4})|"
                  r"(?:[0-9A-Fa-f]{4}([.-])[0-9A-Fa-f]{4}\3[0-9A-Fa-f]{4}))(?![0-9A-Fa-f:.-])")
_IP = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_PORT = re.compile(r"^(?:[A-Za-z][A-Za-z_-]*\d+(?:[/:.]\d+)*|\d+[:/]\d+(?:/\d+)?)$")
_NOT_PORT = {"cpu", "router", "switch", "drop", "self", "sup-eth1"}
# «show fdb» de EXOS 33.6 (salida real en GNS3): «0c:2c:84:eb:00:00  Default(0001) 0014  d m  1»
# (MAC, VLAN(etiqueta), edad, marcas, puerto; en una pila el puerto es «1:5»). Sin las permanentes y estáticas propias
_EXOS_FDB = re.compile(r"^([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s+(\S+)\((\d{4})\)\s+\d+\s+([A-Za-z ]*?)\s+(\d+(?::\d+)?)$")


# --------------------------------------------------------------------------- formatos
def norm_mac(v: str) -> str | None:
    """«aa:bb:cc:dd:ee:ff», «aabb.ccdd.eeff», «AA-BB-CC-DD-EE-FF» o «aabb-ccdd-eeff» → «AABBCCDDEEFF»."""
    h = re.sub(r"[^0-9A-Fa-f]", "", v or "")
    if len(h) != 12 or not _MAC.fullmatch((v or "").strip()):
        return None
    return h.upper()


def show_mac(h: str) -> str:
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)) if h and len(h) == 12 else (h or "")


def classify(q: str) -> tuple[str, str]:
    """(«mac» | «ip» | «bad» | «»; valor normalizado). «bad»: parece una MAC o una IP pero está incompleta."""
    q = (q or "").strip()
    m = norm_mac(q)
    if m:
        return "mac", m
    try:
        return "ip", str(ipaddress.IPv4Address(q))
    except ValueError:
        pass
    if re.fullmatch(r"\d{1,3}(?:\.\d{0,3}){1,3}", q) and q.count(".") >= 2:
        return "bad", q
    if re.fullmatch(r"[0-9A-Fa-f]{1,4}(?:[:.-][0-9A-Fa-f]{0,4}){1,6}", q) and re.search(r"[:.-]", q) \
            and len(re.sub(r"[^0-9A-Fa-f]", "", q)) >= 4 and not re.fullmatch(r"[\d.]+", q):
        return "bad", q
    return "", q


_OUI: dict[str, str] | None = None
_OUI_FILES = ("/usr/share/ieee-data/oui.csv", "/usr/share/ieee-data/oui.txt", "/usr/share/wireshark/manuf",
              "/usr/share/nmap/nmap-mac-prefixes", "/usr/share/arp-scan/ieee-oui.txt")


def _load_oui() -> dict[str, str]:
    """Fabricante por OUI desde la base del IEEE que traen los paquetes del sistema (ieee-data, wireshark, nmap)."""
    out: dict[str, str] = {}
    for f in _OUI_FILES:
        p = Path(f)
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if f.endswith(".csv"):
                    parts = line.split(",", 3)
                    if len(parts) >= 3 and re.fullmatch(r"[0-9A-F]{6}", parts[1]):
                        out.setdefault(parts[1], parts[2].strip('"').strip())
                    continue
                m = re.match(r"^([0-9A-Fa-f]{2})[:-]?([0-9A-Fa-f]{2})[:-]?([0-9A-Fa-f]{2})(?:\s+\(hex\))?\s+(.+)$", line)
                if m and not re.match(r"^[0-9A-Fa-f]{2}[:-]", m.group(4)):
                    name = re.split(r"\t", m.group(4).strip())[-1].strip()
                    out.setdefault((m.group(1) + m.group(2) + m.group(3)).upper(), name)
        except OSError:
            continue
    from .topology import MAC_VENDOR
    for o, v in MAC_VENDOR.items():
        out.setdefault(o, v.capitalize())
    return out


def vendor(mac: str) -> str:
    global _OUI
    if not mac:
        return ""
    if int(mac[1], 16) & 2:  # bit «administrada localmente»: los teléfonos y portátiles la cambian por privacidad
        return _("MAC privada (aleatoria)")
    if _OUI is None:
        _OUI = _load_oui()
    return _OUI.get(mac[:6], "")


# --------------------------------------------------------------------------- lectura de las tablas
def commands(device_type: str) -> tuple[list[str], list[str]]:
    return COMMANDS.get(cc.os_of(device_type or ""), ([], []))


def supported(device_type: str) -> bool:
    mac, arp = commands(device_type)
    return bool(mac or arp)


def read(conn, device_type: str) -> dict | None:
    """Tablas MAC y ARP del equipo (texto de cada orden). Nunca hace fallar el respaldo."""
    if get_setting("mac_tables", "1") != "1":
        return None
    mac_cmds, arp_cmds = commands(device_type)
    if not (mac_cmds or arp_cmds):
        return None
    out = {}
    for kind, cmds in (("mac", mac_cmds), ("arp", arp_cmds)):
        for c in cmds:
            try:
                text = conn.send_command(c, read_timeout=60)
            except Exception as e:  # noqa: BLE001
                log.info("tablas (%s): %s", c, e)
                continue
            if text and not _BAD.search(text[:300]):
                out[kind] = text
                break
    return out


def _kv(line: str) -> dict:
    return {k: v.strip('"') for k, v in re.findall(r'([\w-]+)=("[^"]*"|\S+)', line)}


def _blocks(text: str) -> list[str]:
    """Filas de «print detail» de RouterOS (pueden ocupar varias líneas) o líneas sueltas del resto."""
    if re.search(r"(?m)^\s*\d+\s+[A-Z ]{0,6}\w[\w-]*=", text or ""):
        return [b for b in re.split(r"(?m)^\s*(?=\d+\s+[A-Z ]{0,6}\w[\w-]*=)", text) if b.strip()]
    return (text or "").splitlines()


def parse_mac(text: str) -> list[tuple[str, str, str]]:
    """(MAC, VLAN, puerto) de una tabla MAC. Se descartan las propias del equipo (CPU, local=yes)."""
    rows = []
    for b in _blocks(text):
        if "=" in b and re.search(r"mac-address=", b):
            kv = _kv(b.replace("\n", " "))
            if kv.get("local") == "yes" or re.match(r"^\s*\d+\s+[A-Z]*L", b):
                continue
            mac = norm_mac(kv.get("mac-address", ""))
            port = kv.get("on-interface") or kv.get("interface") or ""
            if mac and port:
                rows.append((mac, kv.get("vid", ""), port))
            continue
        x = _EXOS_FDB.match(b.strip())
        if x:  # EXOS: el puerto de un switch suelto es solo un número («1»), que la regla general descarta
            mac = norm_mac(x.group(1))
            if mac and not re.search(r"[ps]", x.group(4)):
                rows.append((mac, str(int(x.group(3))), x.group(5)))
            continue
        m = _MAC.search(b)
        if not m:
            continue
        mac = norm_mac(m.group(1))
        before, after = b[:m.start()].split(), b[m.end():].split()
        ports = [t for t in after if _PORT.match(t) and t.lower() not in _NOT_PORT and not re.fullmatch(r"\d+", t)]
        if not mac or not ports or any(t.lower() in _NOT_PORT for t in after):
            continue
        vl = next((t for t in before if re.fullmatch(r"\d{1,4}", t) and 0 < int(t) < 4095), "") or \
            next((re.match(r"(\d{1,4})", t).group(1) for t in after if re.match(r"\d{1,4}(?:/|$)", t) and 0 < int(re.match(r"(\d{1,4})", t).group(1)) < 4095), "")
        rows.append((mac, vl, re.sub(r"^Port-", "", ports[-1])))  # VOSS: «Port-1/2» en la tabla MAC, «1/2» en LLDP
    return rows


def parse_arp(text: str) -> list[tuple[str, str, str]]:
    """(IP, MAC, interfaz) de una tabla ARP."""
    rows = []
    for b in _blocks(text):
        if "=" in b and "mac-address=" in b:
            kv = _kv(b.replace("\n", " "))
            mac, ip = norm_mac(kv.get("mac-address", "")), kv.get("address", "")
            if mac and _IP.fullmatch(ip or ""):
                rows.append((ip, mac, kv.get("interface", "")))
            continue
        m, i = _MAC.search(b), _IP.search(b)
        if not m or not i or re.search(r"(?i)incomplete|failed", b):
            continue
        after = b[m.end():].split()
        dev = re.search(r"(?:^|\s)dev\s+(\S+)", b)  # Linux: «10.0.0.1 dev eth0 lladdr …»
        port = dev.group(1) if dev else next((t for t in reversed(after) if _PORT.match(t)), "")
        rows.append((i.group(1), norm_mac(m.group(1)), port))
    return rows


def store(device_id: int, raw: dict | None, db=None) -> int:
    """Guarda lo leído en el historial. Devuelve cuántas MAC se vieron. Con «db» (el respaldo) usa esa sesión y no hace
    commit: una segunda sesión quedaría esperando a la del respaldo (SQLite bloquea la base mientras escribe)."""
    if raw is None:
        return 0
    if db is None:
        with SessionLocal() as own:
            n = store(device_id, raw, own)
            own.commit()
            return n
    t = now()
    macs = parse_mac(raw.get("mac", "")) if raw.get("mac") else []
    arps = parse_arp(raw.get("arp", "")) if raw.get("arp") else []
    dev = db.get(Device, device_id)
    if dev is None:
        return 0
    have = {(r.kind, r.mac, r.port, r.ip or ""): r for r in db.scalars(select(MacSeen).where(MacSeen.device_id == device_id))}
    for mac, vl, port in macs:
        r = have.get(("mac", mac, port, ""))
        if r is None:
            r = MacSeen(device_id=device_id, kind="mac", mac=mac, port=port[:64], ip=None, first_seen=t)
            db.add(r)
            have[("mac", mac, port, "")] = r
        r.vlan, r.last_seen = (vl or "")[:16], t
    for ip, mac, port in arps:
        r = have.get(("arp", mac, port[:64], ip))
        if r is None:
            r = MacSeen(device_id=device_id, kind="arp", mac=mac, port=port[:64], ip=ip, first_seen=t)
            db.add(r)
            have[("arp", mac, port[:64], ip)] = r
        r.last_seen = t
    if raw.get("mac") or raw.get("arp"):
        dev.tables_at, dev.tables_note = t, None
    else:
        dev.tables_note = _("No respondió a la orden de la tabla MAC ni a la de ARP.")
    db.execute(delete(MacSeen).where(MacSeen.device_id == device_id, MacSeen.last_seen < t - timedelta(days=KEEP_DAYS)))
    return len(macs)


def refresh(ids: list[int], limit_s: int = 90) -> dict:
    """«Leer las tablas ahora»: solo las dos tablas, de a PARALLEL equipos a la vez."""
    from .services import connect, device_lock
    done = {"ok": 0, "err": 0}
    lock = threading.Lock()

    def one(i):
        with SessionLocal() as db:
            dev = db.get(Device, i)
            if dev is None or not supported(dev.device_type):
                return
            dev.credential  # noqa: B018 - se carga antes de cerrar la sesión (connect la usa)
        lk = device_lock(i)
        if not lk.acquire(timeout=30):
            return
        try:
            with connect(dev) as conn:
                store(i, read(conn, dev.device_type))
            with lock:
                done["ok"] += 1
        except Exception as e:  # noqa: BLE001
            with SessionLocal() as db:
                d = db.get(Device, i)
                if d is not None:
                    d.tables_note = (_("No se pudo entrar: ") + str(e))[:250]
                    db.commit()
            with lock:
                done["err"] += 1
        finally:
            lk.release()

    pool = ThreadPoolExecutor(PARALLEL)
    futs = [pool.submit(one, i) for i in ids]
    wait(futs, timeout=limit_s)
    pool.shutdown(wait=False, cancel_futures=True)
    return done


# --------------------------------------------------------------------------- búsqueda
def _visible_devices(db, user) -> list[Device]:
    scope = device_scope(user)
    devs = list(db.scalars(select(Device).where(Device.enabled.is_(True))))
    return [d for d in devs if scope is None or d.id in scope]


def _uplinks(db, devs: dict[int, Device]) -> dict[int, set[str]]:
    """Puertos que no son de acceso: con vecino LLDP/CDP de red o con muchas MAC detrás en la última lectura."""
    from .topology import short_port
    out: dict[int, set[str]] = {}
    for d in devs.values():
        s = out.setdefault(d.id, set())
        try:
            for n in json.loads(d.neighbors or "[]"):
                if n.get("local_port") and not n.get("romon"):
                    s.add(short_port(n["local_port"]))
        except ValueError:
            pass
    q = (select(MacSeen.device_id, MacSeen.port, func.count()).join(Device, Device.id == MacSeen.device_id)
         .where(MacSeen.kind == "mac", MacSeen.last_seen == Device.tables_at).group_by(MacSeen.device_id, MacSeen.port))
    for dev_id, port, n in db.execute(q):
        if n > TRUNK_MACS and dev_id in out:
            out[dev_id].add(short_port(port))
    return out


def _port_info(dev: Device, port: str) -> dict:
    from .topology import short_port
    try:
        ports = (json.loads(dev.ports or "{}") or {}).get("ports", [])
    except ValueError:
        ports = []
    p = next((x for x in ports if short_port(x.get("n", "")) == short_port(port)), {})
    return {"desc": p.get("d", "")}


def _vlan_names(dev: Device) -> dict[str, str]:
    from . import ipam
    from .crypto import try_decrypt
    from .services import latest_version
    with SessionLocal() as db:
        v = latest_version(db, dev.id)
        text = try_decrypt(v.content_enc) if v else ""
    try:
        return {str(x["id"]): x.get("name") or "" for x in ipam.vlans(text or "", dev.device_type)}
    except Exception:  # noqa: BLE001
        return {}


def _ago(d) -> str:
    from .web import ago
    return ago(d)


def coverage(db, devs: list[Device]) -> dict:
    sup = [d for d in devs if supported(d.device_type)]
    read_ = [d for d in sup if d.tables_at]
    last = max((d.tables_at for d in read_), default=None)
    missing = [{"name": d.name, "why": d.tables_note or (_("Todavía no se leyeron: se leen en el próximo respaldo.")
                                                          if supported(d.device_type) else "")}
               for d in sup if not d.tables_at or d.tables_note]
    missing += [{"name": d.name, "why": _("Este tipo de equipo no entrega tablas MAC.")} for d in devs if not supported(d.device_type)]
    return {"read_n": len(read_), "total_n": len(sup), "read_ago": _ago(last) if last else "", "missing": missing[:20],
            "missing_n": len(missing), "eta_s": max(10, math.ceil(max(len(sup), 1) / PARALLEL) * 10)}


def lookup(user, q: str) -> dict:
    kind, val = classify(q)
    res = {"kind": kind, "q": (q or "").strip()}
    if kind not in ("mac", "ip", "bad"):
        return res
    with SessionLocal() as db:
        devs = _visible_devices(db, user)
        byid = {d.id: d for d in devs}
        res["cov"] = coverage(db, devs)
        if kind == "bad":
            return res
        arp_q = select(MacSeen).where(MacSeen.kind == "arp", MacSeen.device_id.in_(list(byid)))
        arp = list(db.scalars(arp_q.where(MacSeen.ip == val) if kind == "ip" else arp_q.where(MacSeen.mac == val))
                   .all()) if byid else []
        arp.sort(key=lambda r: r.last_seen, reverse=True)
        mac = val if kind == "mac" else (arp[0].mac if arp else "")
        ip = val if kind == "ip" else (arp[0].ip if arp else "")
        res.update(mac=show_mac(mac), ip=ip or "", vendor=vendor(mac) if mac else "")
        if arp:
            a = arp[0]
            res["arp"] = {"device": byid[a.device_id].name, "device_id": a.device_id, "port": a.port, "ago": _ago(a.last_seen),
                          "current": a.last_seen == byid[a.device_id].tables_at}
        seen = list(db.scalars(select(MacSeen).where(MacSeen.kind == "mac", MacSeen.mac == mac,
                                                     MacSeen.device_id.in_(list(byid))))) if mac and byid else []
        ups = _uplinks(db, byid) if seen else {}
    from .topology import short_port
    from .console import console_allowed
    current = [s for s in seen if s.last_seen == byid[s.device_id].tables_at]
    access = [s for s in current if short_port(s.port) not in ups.get(s.device_id, set())]
    trunk = [s for s in current if s not in access]
    vnames: dict[int, dict] = {}

    def card(s):
        d = byid[s.device_id]
        if s.vlan and d.id not in vnames:
            vnames[d.id] = _vlan_names(d)
        return {"device": d.name, "device_id": d.id, "port": s.port, "vlan": s.vlan, "vlan_name": vnames.get(d.id, {}).get(s.vlan, ""),
                "desc": _port_info(d, s.port)["desc"], "ago": _ago(s.last_seen), "first": _ago(s.first_seen),
                "console": console_allowed(user, d.id)}
    access.sort(key=lambda s: (s.first_seen, s.id), reverse=True)  # el más reciente primero (el que se enchufó último)
    res["found"] = [card(s) for s in access]
    path = []
    if res.get("arp") and res["arp"]["current"]:
        path.append({"device": res["arp"]["device"], "port": "", "role": "arp"})
    for s in trunk:
        if byid[s.device_id].name not in [p["device"] for p in path]:
            path.append({"device": byid[s.device_id].name, "port": s.port, "role": "trunk"})
    if len(res["found"]) == 1:
        path.append({"device": res["found"][0]["device"], "port": res["found"][0]["port"], "role": "access"})
    res["path"] = path if len(path) > 1 else []
    if not res["found"]:
        old = sorted([s for s in seen if s not in current], key=lambda s: s.last_seen, reverse=True)
        if old:
            s = old[0]
            res["last"] = {"device": byid[s.device_id].name, "device_id": s.device_id, "port": s.port, "ago": _ago(s.last_seen),
                           "at": s.last_seen.strftime("%d-%m-%Y %H:%M")}
        elif res.get("arp") and not res["arp"]["current"]:
            res["last"] = {"device": res["arp"]["device"], "device_id": res["arp"]["device_id"], "port": res["arp"]["port"],
                           "ago": res["arp"]["ago"], "at": ""}
    return res


@router.get("/api/where")
def api_where(request: Request, q: str = ""):
    user = require(request)
    return JSONResponse(lookup(user, q[:64]))


@router.post("/api/where/refresh")
def api_refresh(request: Request):
    """«Leer las tablas ahora» en los equipos visibles que las entregan."""
    user = require(request, "operator")
    with SessionLocal() as db:
        ids = [d.id for d in _visible_devices(db, user) if supported(d.device_type)]
    for i in ids:
        check_device(user, i)
    r = refresh(ids)
    from .db import audit
    audit(user.username, "mac_tables_refresh", _f("{0} equipos", len(ids)))
    return JSONResponse({"ok": True, **r, "n": len(ids)})
