"""Puertos físicos del equipo y su estado (ficha del equipo: franja de puertos y pestaña «Puertos»).

La configuración solo dice si un puerto está apagado («disabled=yes», «shutdown»); si tiene enlace no sale de ella.
Por eso el estado se lee en la misma conexión del respaldo y se guarda en el equipo con la hora del respaldo:

* MikroTik: «/interface ethernet print terse» (bandera R = con enlace, X = deshabilitado) y, por puerto,
  «/interface ethernet monitor <puerto> once» (status, rate, sfp-module-present y datos del módulo).
* ASA / FTD: «show interface ip brief». Juniper: «show interfaces terse».
* El resto, con la orden de cada fabricante (COMMANDS) y los lectores de app/porttable.py; si el equipo no tiene ninguna
  de ellas se prueban las habituales («show interfaces status», «show interface brief»…).
* Si ninguna da la tabla: solo lo que dice la configuración (apagado o habilitado), sin estado de enlace.

Cada puerto es {"n": nombre, "k": "cu" | "sfp", "s": "up" | "down" | "off" | "empty" | "on", "sp": velocidad,
"d": descripción, "net": dirección o VLAN, "mod": módulo SFP, "st": línea de estado tal como la dio el equipo}.
«on» = habilitado en la configuración, sin estado de enlace conocido.
"""
import re

from . import porttable as pt
from .drivers import FASTPATH, HUAWEI, get_profile
from .i18n import _

MARK = "### puertos: "  # separa la salida de cada orden en el texto leído
MAX_MONITOR = 64        # MikroTik: puertos a consultar uno por uno con «monitor» (los CCR grandes tienen ~30)

# orden(es) que dan el estado de los puertos, por tipo de equipo; se prueban en orden hasta que una da la tabla
LINUX = ("linux", "ubuntu_server", "alpine_linux", "cumulus_linux", "dell_sonic", "edgecore_sonic", "opengear_linux",
         "ubiquiti_airos", "ubiquiti_airfiber", "ubiquiti_unifi_ap")
COMMANDS = {
    "cisco_asa": ["show interface ip brief"], "cisco_ftd": ["show interface ip brief"],
    "juniper_junos": ["show interfaces terse"], "juniper": ["show interfaces terse"],
    "cisco_nxos": ["show interface status"], "cisco_wlc": ["show port summary"],
    "hp_procurve": ["show interfaces brief"], "aruba_osswitch": ["show interfaces brief"],
    "aruba_aoscx": ["show interface brief"], "extreme_exos": ["show ports no-refresh"],
    "fortinet": ["get system interface physical"], "paloalto_panos": ["show interface all"],
    "checkpoint_gaia": ["show interfaces all"], "nokia_sros": ["show port"],
    "pfsense": ["ifconfig -a"], "opnsense": ["ifconfig -a"],
    "ubiquiti_unifiswitch": ["show interfaces status all", "show port all"],
    "broadcom_icos": ["show interfaces status all", "show port all"],
    "netgear_prosafe": ["show interfaces status all", "show interfaces status"],
    **{k: [pt.LINUX_CMD] for k in LINUX},
}
GENERIC = ["show interfaces status", "show interface status", "show interfaces brief", "show interface brief"]
VYOS_LIKE = ("vyos", "ubiquiti_edgerouter", "ubiquiti_usg")


def commands(device_type: str) -> list[str]:
    if device_type in COMMANDS:
        return COMMANDS[device_type]
    prof = get_profile(device_type)
    if device_type in VYOS_LIKE:
        return ["show interfaces"]
    if prof.family == FASTPATH:
        return ["show interfaces status all", "show interfaces status", "show interface status"]
    if prof.family == HUAWEI or device_type.startswith(("huawei", "h3c", "hp_comware")):
        return ["display interface brief"]
    return GENERIC


def has_status(device_type: str) -> bool:
    return device_type == "mikrotik_routeros" or bool(commands(device_type))


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
    for c in commands(device_type):
        try:
            text = conn.send_command(c, read_timeout=30)
        except Exception:  # noqa: BLE001
            continue
        out.append(MARK + c + "\n" + text)
        if _parse_section(c, text, device_type):  # la primera que da puertos basta
            break
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
                                              r"Ethernet|port\s?\d|xe\d|xg\d)", name)) or "." in name:
            continue
        off = any(re.match(r"^\s*shutdown\s*$", b) for b in body)
        ports.append({"n": name, "k": "sfp" if _is_sfp_name(name) or re.match(r"(?i)^(X|10GE|25GE|40GE|100GE)", name)
                      else "cu", "s": "off" if off else "on", "sp": "", "d": "", "net": "", "mod": "",
                      "st": ""})
    return ports


def _fastpath_blocks(config: str) -> list[tuple[str, list[str]]]:
    """FastPath (EdgeSwitch, UniFi): «interface 0/1» … «exit», sin sangría."""
    out, cur = [], None
    for line in (config or "").splitlines():
        m = re.match(r"^interface\s+(\d+/\d+)\s*$", line.strip())
        if m:
            cur = (m.group(1), [])
            out.append(cur)
        elif cur is not None:
            if line.strip() == "exit":
                cur = None
            else:
                cur[1].append(line.strip())
    return out


def _fastpath_config(config: str) -> list[dict]:
    return [{"n": n, "k": "cu", "s": "off" if "shutdown" in body else "on", "sp": "",
             "d": next((b[12:].strip("'\"") for b in body if b.startswith("description ")), ""), "net": "", "mod": "", "st": ""}
            for n, body in _fastpath_blocks(config) if n.startswith("0/")]


def _set_config(config: str) -> list[dict]:
    """Junos / VyOS / EdgeOS en formato «set»: interfaces físicas y si tienen «disable»."""
    seen: dict[str, dict] = {}
    for m in re.finditer(r"(?m)^set interfaces (?:ethernet )?((?:ge|xe|et|fe|mge|xle)-\d+/\d+/\d+|eth\d+)(?:\s+(.*))?$",
                         config or ""):
        n, rest = m.group(1), (m.group(2) or "").strip()
        p = seen.setdefault(n, {"n": n, "k": "sfp" if n.startswith(("xe-", "et-", "xle-")) else "cu", "s": "on", "sp": "",
                                "d": "", "net": "", "mod": "", "st": ""})
        if rest == "disable":
            p["s"] = "off"
        elif rest.startswith("description "):
            p["d"] = rest[12:].strip("'\"")
    return list(seen.values())


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


# Frente de los modelos más comunes (fotos del fabricante): filas del cobre, columnas por bloque, huecos en una fila y
# si los SFP van en una fila (al lado de la fila de abajo, como en el CRS326) o en dos.
#   rows: 1 | 2 · grp: columnas por bloque (fila doble) · gaps: tras qué puertos hay un hueco (fila simple)
#   sfp: "row" (todos en una fila), "bottom" (en la fila de abajo, junto al cobre) o "pairs" (de dos en dos)
LAYOUTS = [
    # MikroTik
    (r"(?i)^(CRS3\d\d|CSS3\d\d|CRS354|CSS610|CRS510|CRS518)", {"rows": 2, "grp": 4, "sfp": "bottom"}),
    (r"(?i)^(CCR1036-12G|CCR1016-12G|CCR2116-12G|CCR1072)", {"rows": 2, "grp": 6, "sfp": "pairs"}),
    (r"(?i)^(CCR2004-16G|CCR2216)", {"rows": 2, "grp": 4, "sfp": "pairs"}),
    (r"(?i)^(RB4011|RB2011|RB3011|RB1100)", {"rows": 1, "gaps": [5], "sfp": "row"}),
    (r"(?i)^(RB5009|CCR2004-1G|CCR1009|hEX|hAP|RB750|RB760|RB951|RB952|RB941|L009|RB450|RB850)", {"rows": 1, "sfp": "row"}),
    # Ubiquiti (UniFi, EdgeSwitch, EdgeRouter)
    (r"(?i)^(US-?8|USW-?(Lite-)?8|USW-?Flex|ES-?8|ES-?10|ER-|EdgeRouter|ER\d|USW-Ultra|USG)", {"rows": 1, "sfp": "row"}),
    (r"(?i)^(US-?16|USW-?(Lite-)?16|ES-?16)", {"rows": 2, "grp": 4, "sfp": "pairs"}),
    (r"(?i)^(US-?24|USW-?24|ES-?24|US-?48|USW-?48|ES-?48|USW-Pro)", {"rows": 2, "grp": 6, "sfp": "pairs"}),
    # firewalls de escritorio: una fila
    (r"(?i)^(FortiGate|FG-?\d|FGT|PA-?2\d\d|PA-?4\d\d|PA-?1[45]\d\d|SG-\d|Netgate|XG\s?\d|Firebox)", {"rows": 1, "sfp": "row"}),
]


def layout(model: str | None, ports: list[dict]) -> dict:
    """Disposición del frente: del catálogo por modelo o, si no está, según el número de puertos de cobre."""
    for pat, lay in LAYOUTS:
        if re.search(pat, (model or "").strip()):
            return {"rows": 1, "grp": 6, "gaps": [], "sfp": "row", **lay}
    cu = sum(1 for p in ports if p["k"] != "sfp")
    sfp = len(ports) - cu
    if cu <= 12:
        return {"rows": 1, "grp": 6, "gaps": [], "sfp": "row" if sfp <= 4 else "pairs"}
    cols = (cu + 1) // 2
    return {"rows": 2, "grp": 6 if cols % 6 == 0 else 4 if cols % 4 == 0 else cols, "gaps": [],
            "sfp": "bottom" if sfp <= 2 else "pairs"}


def model_ports(model: str | None) -> int | None:
    """Puertos de cobre que dice el modelo («US-24-250W» -> 24, «WS-C2960X-48FPD-L» -> 48), para saber que los
    siguientes son SFP cuando el equipo no dice el tipo de cada puerto."""
    m = re.search(r"(?i)(?:^|[-_ ])(?:[a-z]*?)(8|10|16|18|24|26|28|48|52)(?:[a-z]*)?(?:[-_ ]|$)", model or "")
    return int(m.group(1)) if m else None


def _parse_section(cmd: str, text: str, device_type: str, model: str | None = None) -> list[dict]:
    if cmd == "show interface ip brief":
        return _asa(text)
    if cmd == "show interfaces terse":
        return _junos(text)
    if cmd == "show interfaces" and device_type in VYOS_LIKE:
        return pt.vyos(text)
    if cmd == "get system interface physical":
        return pt.fortigate(text)
    if cmd == "show interface all" and device_type == "paloalto_panos":
        return pt.panos(text)
    if cmd == "show interfaces all" and device_type == "checkpoint_gaia":
        return pt.gaia(text)
    if cmd == pt.LINUX_CMD:
        return pt.linux(text)
    if cmd == "ifconfig -a":
        return pt.bsd(text)
    ports = pt.from_table(text, model_ports(model))
    if get_profile(device_type).family == FASTPATH or cmd in ("show interfaces status all", "show port all"):
        # FastPath (EdgeSwitch, UniFi): los puertos del frente son 0/N; 3/N son agregados (LAG) y 4/N, VLAN
        ports = [p for p in ports if re.match(r"^0/\d+$", p["n"])] or ports
    return ports


def parse(raw: str | None, device_type: str, config: str = "", model: str | None = None) -> tuple[list[dict], bool]:
    """(puertos, hay_estado_de_enlace). Sin salida de estado se usa la configuración (apagado / habilitado)."""
    ports: list[dict] = []
    if raw:
        if device_type == "mikrotik_routeros":
            ports = _mikrotik(raw)
        else:
            for cmd, text in _sections(raw).items():
                ports = _parse_section(cmd, text, device_type, model)
                if ports:
                    break
        if ports:
            _apply_config_off(ports, device_type, config)
            return ports, True
    # sin tabla de estado: la configuración, en el estilo que tenga (bloques con sangría, FastPath «… exit» o «set»)
    for fn in (_from_config, _fastpath_config, _set_config):
        ports = fn(config)
        if ports:
            return ports, False
    return [], False


def _apply_config_off(ports: list[dict], device_type: str, config: str) -> None:
    """Algunas tablas no distinguen «sin enlace» de «apagado» (FastPath, EdgeOS): la configuración lo dice."""
    if not config:
        return
    off = {n for n, body in _fastpath_blocks(config) if any(b.strip() == "shutdown" for b in body)}
    off |= {n for n, body in _ios_blocks(config) if any(b.strip() == "shutdown" for b in body)}
    off |= {m.group(1) for m in re.finditer(r"(?m)^set interfaces (?:ethernet )?(\S+) disable\s*$", config)}
    shorts = {_short(n) for n in off}
    for p in ports:
        if (p["n"] in off or _short(p["n"]) in shorts) and p["s"] != "off":
            p["s"], p["sp"] = "off", ""


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
        bridge: dict[str, tuple[str, str]] = {}
        for l in lines:
            if l.startswith("/ip address add "):
                kv = dict(_ROS_KV.findall(l))
                if kv.get("interface"):
                    addr.setdefault(_ros_text(kv["interface"]), []).append(_ros_text(kv.get("address", "")))
            elif l.startswith("/interface bridge port add "):
                kv = dict(_ROS_KV.findall(l))
                if kv.get("interface"):
                    bridge[_ros_text(kv["interface"])] = (_ros_text(kv.get("bridge", "")), _ros_text(kv.get("pvid", "")))
    for p in ports:
        n = p["n"]
        p["nb"] = neigh.get(n) or next((v for k, v in neigh.items() if _short(k) == _short(n)), "")
        cfg: list[str] = []
        if device_type == "mikrotik_routeros":
            dn = p.get("dn") or n
            pat = re.compile(rf'(default-name={re.escape(dn)}(\s|$)|interface="?{re.escape(n)}"?(\s|$))')
            cfg = [l for l in lines if pat.search(l)][:4]
            if not p.get("net") and addr.get(n):
                p["net"], p["nk"] = ", ".join(addr[n]), "ip"
            elif not p.get("net") and n in bridge:
                br, pvid = bridge[n]
                p["net"], p["nk"] = br + (f" · VLAN {pvid}" if pvid and pvid != "1" else ""), "bridge"
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
        if p.get("net") and not p.get("nk"):  # IP leída del equipo o de la configuración; si no, VLAN o troncal
            p["nk"] = "ip" if re.match(r"^[\d.]+(/\d+)?$|^[0-9a-fA-F:]+:[0-9a-fA-F:]*(/\d+)?$", p["net"].split(",")[0].strip()) else "net"
        p["cfg"] = cfg


def _short(name: str) -> str:
    """«GigabitEthernet1/0/1» y «Gi1/0/1» son el mismo puerto."""
    m = re.match(r"^([A-Za-z]{2})[A-Za-z-]*\s*([\d/.:]+)$", name or "")
    return (m.group(1).lower() + m.group(2)) if m else (name or "").lower()
