"""Puertos físicos del equipo y su estado (ficha del equipo: franja de puertos y pestaña «Puertos»).

La configuración solo dice si un puerto está apagado («disabled=yes», «shutdown»); si tiene enlace no sale de ella.
Por eso el estado se lee en la misma conexión del respaldo y se guarda en el equipo con la hora del respaldo:

* MikroTik: «/interface ethernet print terse» (bandera R = con enlace, X = deshabilitado) y, por puerto,
  «/interface ethernet monitor <puerto> once» (status, rate, sfp-module-present y datos del módulo).
* Cisco IOS / IOS-XE: «show interfaces status». ASA / FTD: «show interface ip brief». Juniper: «show interfaces terse».
* El resto de plataformas con bloques «interface …» (sintaxis Cisco/Huawei): solo lo que dice la configuración
  (apagado o habilitado), sin estado de enlace.

Cada puerto es {"n": nombre, "k": "cu" | "sfp", "s": "up" | "down" | "off" | "empty" | "on", "sp": velocidad,
"d": descripción, "net": dirección o VLAN, "mod": módulo SFP, "st": línea de estado tal como la dio el equipo}.
«on» = habilitado en la configuración, sin estado de enlace conocido.
"""
import re

from .drivers import HUAWEI, IOS, get_profile
from .i18n import _, _f

MARK = "### puertos: "  # separa la salida de cada orden en el texto leído
MAX_MONITOR = 64        # MikroTik: puertos a consultar uno por uno con «monitor» (los CCR grandes tienen ~30)

STATUS = {
    "cisco_ios": ["show interfaces status"], "cisco_xe": ["show interfaces status"],
    "cisco_asa": ["show interface ip brief"], "cisco_ftd": ["show interface ip brief"],
    "juniper_junos": ["show interfaces terse"], "juniper": ["show interfaces terse"],
}


def has_status(device_type: str) -> bool:
    return device_type == "mikrotik_routeros" or device_type in STATUS


def read(conn, device_type: str) -> str | None:
    """Lee la tabla de puertos en la conexión del respaldo. Nunca hace fallar el respaldo: devuelve None si no puede."""
    if device_type == "mikrotik_routeros":
        try:
            out = conn.send_command("/interface ethernet print terse without-paging", read_timeout=30)
        except Exception:  # noqa: BLE001
            return None
        raw = [MARK + "/interface ethernet print terse\n" + out]
        for name, flags, _kv in _ros_list(out)[:MAX_MONITOR]:
            if "X" in flags:  # deshabilitado: el equipo no informa del enlace
                continue
            cmd = f'/interface ethernet monitor "{name}" once'
            try:
                raw.append(MARK + cmd + "\n" + conn.send_command(cmd, read_timeout=20))
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(raw)
    out = []
    for c in STATUS.get(device_type, []):
        try:
            out.append(MARK + c + "\n" + conn.send_command(c, read_timeout=30))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(out) or None


def _sections(raw: str) -> dict[str, str]:
    out = {}
    for part in (raw or "").split(MARK)[1:]:
        cmd, _, body = part.partition("\n")
        out[cmd.strip()] = body
    return out


# --------------------------------------------------------------------------- MikroTik
_ROS_ROW = re.compile(r"^\s*(\d+)\s+([A-Z]*)\s+(.*)$")
_ROS_KV = re.compile(r'([\w-]+)=("(?:[^"\\]|\\.)*"|\S*)')


def _ros_text(v: str) -> str:
    from .topology import _ros_text as dec
    return dec(v.strip('"').replace('\\"', '"'))


def _ros_list(out: str) -> list[tuple[str, str, dict]]:
    rows = []
    for line in out.splitlines():
        m = _ROS_ROW.match(line)
        if not m:
            continue
        kv = {k: _ros_text(v) for k, v in _ROS_KV.findall(m.group(3))}
        if kv.get("name"):
            rows.append((kv["name"], m.group(2), kv))
    return rows


def _ros_monitor(text: str) -> dict:
    kv = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([\w-]+):\s*(.*?)\s*$", line)
        if m:
            kv[m.group(1)] = m.group(2)
    return kv


def _rate(v: str) -> str:
    """«1Gbps» -> «1 Gbps», «100Mbps» -> «100 Mbps»."""
    m = re.match(r"^\s*([\d.]+)\s*([GMK])bps", v or "")
    return f"{m.group(1)} {m.group(2)}bps" if m else (v or "")


def _is_sfp_name(n: str) -> bool:
    return bool(re.match(r"(?i)^(q?sfp|combo|xe-|et-|xle-|te|tw|fo|hu)", n))


def _mikrotik(raw: str) -> list[dict]:
    sec = _sections(raw)
    lst = sec.get("/interface ethernet print terse", "")
    ports = []
    for name, flags, kv in _ros_list(lst):
        dn = kv.get("default-name") or name
        mon = _ros_monitor(sec.get(f'/interface ethernet monitor "{name}" once', ""))
        sfp = _is_sfp_name(dn) or "sfp-module-present" in mon
        if "X" in flags:
            s, st = "off", f"{name}: disabled=yes"
        elif sfp and mon.get("sfp-module-present") == "no":
            s, st = "empty", "status: " + (mon.get("status") or "no-link") + "  sfp-module-present: no"
        elif mon.get("status"):
            s = "up" if mon["status"] == "link-ok" else "down"
            st = "status: " + mon["status"] + (f"  rate: {mon['rate']}" if mon.get("rate") and s == "up" else "")
        else:
            s, st = ("up", f"{name}: running") if "R" in flags else ("down", f"{name}: no running")
        mod = " · ".join(x for x in (mon.get("sfp-vendor-name"), mon.get("sfp-vendor-part-number"),
                                     (mon.get("sfp-wavelength") or "").replace("nm", " nm")) if x) if sfp else ""
        ports.append({"n": name, "dn": dn, "k": "sfp" if sfp else "cu", "s": s,
                      "sp": _rate(mon.get("rate", "")) if s == "up" else "", "d": kv.get("comment", ""),
                      "net": "", "mod": mod, "st": st})
    return ports


# --------------------------------------------------------------------------- Cisco IOS «show interfaces status»
_PHYS = re.compile(r"(?i)^(Gi|Fa|Te|Tw|Twe|Fo|Hu|Et|Eth|Fi|Ap|Mg)\S*\d")


def _speed(v: str) -> str:
    v = (v or "").lower().removeprefix("a-")
    m = re.match(r"^(\d+(?:\.\d+)?)(g?)$", v)
    if not m:
        return ""
    n = float(m.group(1)) * (1000 if m.group(2) else 1)
    return f"{n / 1000:g} Gbps" if n >= 1000 else f"{n:g} Mbps"


def _ios_status(text: str) -> list[dict]:
    lines = text.splitlines()
    head = next((i for i, l in enumerate(lines) if re.match(r"^\s*Port\s+Name\s+Status\s+Vlan", l)), None)
    if head is None:
        return []
    h = lines[head]
    cols = [h.index(c) for c in ("Name", "Status", "Vlan", "Duplex", "Speed", "Type")]
    ports = []
    for l in lines[head + 1:]:
        if not _PHYS.match(l):
            continue
        name = l[:cols[0]].strip()
        desc = l[cols[0]:cols[1]].strip()
        rest = l[cols[1]:].split(None, 4)
        if len(rest) < 4:
            continue
        status, vlan, _dup, speed = rest[:4]
        typ = rest[4].strip() if len(rest) > 4 else ""
        sfp = bool(re.search(r"(?i)sfp|base-?[lszecb]x|not present|gbic|xfp|qsfp", typ)) or _is_sfp_name(name)
        if status == "disabled":
            s = "off"
        elif re.search(r"(?i)not present", typ) or status.lower() == "sfpabsent":
            s = "empty"
        else:
            s = "up" if status == "connected" else "down"
        net = "trunk" if vlan == "trunk" else (f"VLAN {vlan}" if vlan.isdigit() else "")
        ports.append({"n": name, "k": "sfp" if sfp else "cu", "s": s, "sp": _speed(speed) if s == "up" else "",
                      "d": desc, "net": net, "mod": typ if sfp and s != "empty" else "", "st": l.strip()})
    return ports


# --------------------------------------------------------------------------- ASA / FTD «show interface ip brief»
_ASA = re.compile(r"^(\S+)\s+(\S+)\s+(?:YES|NO)\s+\S+\s+(administratively down|up|down|deleted)\s+(up|down)\s*$")


def _asa(text: str) -> list[dict]:
    ports = []
    for l in text.splitlines():
        m = _ASA.match(l.strip())
        if not m or "." in m.group(1) or not re.match(r"(?i)^(Gigabit|TenGigabit|Ethernet|Management|Fast)", m.group(1)):
            continue
        name, ip, admin, proto = m.groups()
        s = "off" if admin.startswith("admin") else ("up" if proto == "up" else "down")
        ports.append({"n": name, "k": "sfp" if name.lower().startswith("tengig") else "cu", "s": s, "sp": "", "d": "",
                      "net": "" if ip == "unassigned" else ip, "mod": "", "st": l.strip()})
    return ports


# --------------------------------------------------------------------------- Juniper «show interfaces terse»
_JUN_PHYS = re.compile(r"^(ge|xe|et|fe|mge|xle|ce)-\d+/\d+/\d+$")


def _junos(text: str) -> list[dict]:
    ports, by = [], {}
    for l in text.splitlines():
        t = l.split()
        if len(t) < 3:
            continue
        name = t[0]
        if _JUN_PHYS.match(name):
            s = "off" if t[1] == "down" else ("up" if t[2] == "up" else "down")
            p = {"n": name, "k": "cu" if name.startswith(("ge-", "fe-", "mge-")) else "sfp", "s": s, "sp": "", "d": "",
                 "net": "", "mod": "", "st": l.strip()}
            ports.append(p)
            by[name] = p
        elif "." in name and name.split(".")[0] in by and len(t) >= 5 and t[3] == "inet":
            by[name.split(".")[0]]["net"] = by[name.split(".")[0]]["net"] or t[4]
    return ports


# --------------------------------------------------------------------------- solo configuración (sin estado de enlace)
def _from_config(config: str) -> list[dict]:
    ports = []
    for name, body in _ios_blocks(config):
        if not (_PHYS.match(name) or re.match(r"(?i)^(GigabitEthernet|XGigabitEthernet|10GE|25GE|40GE|100GE|"
                                              r"Ethernet|port\s?\d)", name)) or "." in name:
            continue
        off = any(re.match(r"^\s*shutdown\s*$", b) for b in body)
        ports.append({"n": name, "k": "sfp" if _is_sfp_name(name) or re.match(r"(?i)^(X|10GE|25GE|40GE|100GE)", name)
                      else "cu", "s": "off" if off else "on", "sp": "", "d": "", "net": "", "mod": "",
                      "st": ""})
    return ports


def _ios_blocks(config: str) -> list[tuple[str, list[str]]]:
    out, cur = [], None
    for line in (config or "").splitlines():
        m = re.match(r"^interface\s+(.+?)\s*$", line)
        if m:
            cur = (m.group(1), [])
            out.append(cur)
        elif cur and line[:1] in (" ", "\t"):
            cur[1].append(line)
        else:
            cur = None
    return out


def parse(raw: str | None, device_type: str, config: str = "") -> tuple[list[dict], bool]:
    """(puertos, hay_estado_de_enlace). Sin salida de estado se usa la configuración (apagado / habilitado)."""
    ports: list[dict] = []
    if raw:
        sec = _sections(raw)
        if device_type == "mikrotik_routeros":
            ports = _mikrotik(raw)
        elif "show interfaces status" in sec:
            ports = _ios_status(sec["show interfaces status"])
        elif "show interface ip brief" in sec:
            ports = _asa(sec["show interface ip brief"])
        elif "show interfaces terse" in sec:
            ports = _junos(sec["show interfaces terse"])
        if ports:
            return ports, True
    if get_profile(device_type).family in (IOS, HUAWEI):
        return _from_config(config), False
    return [], False


# --------------------------------------------------------------------------- datos de la configuración (al mostrar)
def _mask_len(mask: str) -> int | None:
    try:
        return sum(bin(int(x)).count("1") for x in mask.split("."))
    except ValueError:
        return None


def enrich(ports: list[dict], device_type: str, config: str, neighbors: list[dict]) -> None:
    """Completa descripción, dirección/VLAN, vecino y las líneas de la configuración que justifican el estado."""
    neigh: dict[str, str] = {}
    for n in neighbors or []:
        lp = n.get("local_port") or ""
        if lp and lp not in neigh:
            neigh[lp] = " · ".join(x for x in (n.get("remote_name") or n.get("remote_ip"), n.get("remote_port")) if x)
    lines = (config or "").splitlines()
    blocks = dict(_ios_blocks(config)) if device_type != "mikrotik_routeros" else {}
    if device_type == "mikrotik_routeros":
        addr: dict[str, list[str]] = {}
        bridge: dict[str, str] = {}
        for l in lines:
            if l.startswith("/ip address add "):
                kv = dict(_ROS_KV.findall(l))
                if kv.get("interface"):
                    addr.setdefault(_ros_text(kv["interface"]), []).append(_ros_text(kv.get("address", "")))
            elif l.startswith("/interface bridge port add "):
                kv = dict(_ROS_KV.findall(l))
                if kv.get("interface"):
                    bridge[_ros_text(kv["interface"])] = _ros_text(kv.get("bridge", ""))
    for p in ports:
        n = p["n"]
        p["nb"] = neigh.get(n) or next((v for k, v in neigh.items() if _short(k) == _short(n)), "")
        cfg: list[str] = []
        if device_type == "mikrotik_routeros":
            dn = p.get("dn") or n
            pat = re.compile(rf'(default-name={re.escape(dn)}(\s|$)|interface="?{re.escape(n)}"?(\s|$))')
            cfg = [l for l in lines if pat.search(l)][:4]
            if not p.get("net"):
                p["net"] = ", ".join(addr.get(n, [])) or (_f("puente {0}", bridge[n]) if n in bridge else "")
        elif device_type.startswith("juniper"):
            cfg = [l for l in lines if l.startswith(f"set interfaces {n} ")][:4]
            if not p.get("d"):
                m = next((re.match(rf"^set interfaces {re.escape(n)} description (.+)$", l) for l in cfg
                          if " description " in l), None)
                p["d"] = m.group(1).strip('"') if m else ""
        else:
            key = n if n in blocks else next((k for k in blocks if _short(k) == _short(n)), None)
            body = blocks.get(key) if key else None
            if body is not None:
                cfg = [f"interface {key}"] + body[:5]
                for b in body:
                    b = b.strip()
                    if b.startswith("description ") and not p.get("d"):
                        p["d"] = b[12:]
                    m = re.match(r"^ip address (\S+) (\S+)", b)
                    if m and not p.get("net"):
                        ln = _mask_len(m.group(2))
                        p["net"] = m.group(1) + (f"/{ln}" if ln is not None else "")
                    m = re.match(r"^switchport access vlan (\d+)", b)
                    if m and not p.get("net"):
                        p["net"] = f"VLAN {m.group(1)}"
                    if b == "switchport mode trunk" and not p.get("net"):
                        p["net"] = "trunk"
        if p.get("net") == "trunk":
            p["net"] = _("Troncal")
        p["cfg"] = cfg


def _short(name: str) -> str:
    """«GigabitEthernet1/0/1» y «Gi1/0/1» son el mismo puerto."""
    m = re.match(r"^([A-Za-z]{2})[A-Za-z-]*\s*([\d/.:]+)$", name or "")
    return (m.group(1).lower() + m.group(2)) if m else (name or "").lower()
