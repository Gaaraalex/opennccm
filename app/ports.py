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
GENERIC_FALLBACK = ["show interfaces status", "show interface status", "show interfaces brief", "show interface brief",
                    "show ip interface brief"]
COMMANDS = {
    "cisco_asa": ["show interface ip brief"], "cisco_ftd": ["show interface ip brief"],
    "juniper_junos": ["show interfaces terse"], "juniper": ["show interfaces terse"],
    "cisco_nxos": ["show interface status"], "cisco_wlc": ["show port summary"],
    "hp_procurve": ["show interfaces brief"], "aruba_osswitch": ["show interfaces brief"],
    "aruba_aoscx": ["show interface brief"], "extreme_exos": ["show ports no-refresh"],
    # Extreme VOSS / Fabric Engine: tabla con STATUS ADMIN / OPERATE por puerto (1/1…1/26), probada en un 5520 con 9.3
    "extreme_vsp": ["show interfaces gigabitEthernet"],
    "fortinet": ["get system interface physical"], "paloalto_panos": ["show interface all"],
    "checkpoint_gaia": ["show interfaces all"], "nokia_sros": ["show port"],
    "pfsense": ["ifconfig -a"], "opnsense": ["ifconfig -a"],
    # ArubaOS (S2500 y demás Mobility Access Switch): todos los puertos, 0/0/N cobre y 0/1/N uplinks SFP; si el equipo no
    # la tiene se prueban las habituales
    "aruba_os": ["show port status"] + GENERIC_FALLBACK,
    "watchguard_fireware": [],  # se leen con read_fireware(): «show status-report» (enlace y velocidad) + el XML
    "ubiquiti_unifiswitch": ["show interfaces status all", "show port all"],
    "broadcom_icos": ["show interfaces status all", "show port all"],
    "netgear_prosafe": ["show interfaces status all", "show interfaces status"],
    **{k: [pt.LINUX_CMD] for k in LINUX},
}
# (al final «show ip interface brief»: los routers IOS / IOS-XE, como el CSR1000v o un ISR, no tienen «show interfaces
# status»; sin esto la ficha mostraba solo «habilitado» o «apagado» de la configuración, sin el enlace)
GENERIC = ["show interfaces status", "show interface status", "show interfaces brief", "show interface brief",
           "show ip interface brief"]
VYOS_LIKE = ("vyos", "ubiquiti_edgerouter", "ubiquiti_usg")


def commands(device_type: str) -> list[str]:
    if device_type in COMMANDS:
        return COMMANDS[device_type]
    prof = get_profile(device_type)
    if device_type in VYOS_LIKE:
        return ["show interfaces"]
    if prof.family == FASTPATH:
        return ["show interfaces status all", "show interfaces status", "show interface status", "show interfaces brief"]
    if prof.family == HUAWEI or device_type.startswith(("huawei", "h3c", "hp_comware")):
        return ["display interface brief"]
    return GENERIC


def has_status(device_type: str) -> bool:
    return device_type in ("mikrotik_routeros", "watchguard_fireware") or bool(commands(device_type))


FIREWARE_CMD = "show status-report"
FIREWARE_SECTION = "Physical Interfaces Link Status"


def read_fireware(conn) -> str | None:
    """WatchGuard Fireware: «show status-report» (solo lectura; unas 1.500 líneas) trae, en «Physical Interfaces Link
    Status», el enlace y la velocidad de cada puerto («Settings for eth2: … Speed: 1000Mb/s … Link detected: yes»,
    comprobado en un T40 con 12.8.1). Solo se guarda esa sección: el resto trae procesos, claves públicas y DHCP."""
    try:
        text = conn.send_command(FIREWARE_CMD, read_timeout=120)
    except Exception:  # noqa: BLE001
        return None
    i = text.find(FIREWARE_SECTION)
    if i < 0:
        return None
    # de esa sección, solo las líneas de cada puerto (las estadísticas por CPU de cada tarjeta no hacen falta)
    keep = [ln.strip() for ln in text[i:].splitlines()
            if re.match(r"\s*(Settings for eth\d+:|Speed:|Duplex:|Link detected:)", ln)]
    return MARK + FIREWARE_CMD + "\n" + FIREWARE_SECTION + "\n" + "\n".join(keep) if keep else None


def fireware_links(raw: str) -> dict[str, dict]:
    """{«eth2»: {"up": True, "sp": "1 Gbps", "st": "Link detected: yes · 1000Mb/s Full"}} de la sección guardada."""
    out, cur = {}, None
    for ln in (raw or "").splitlines():
        m = re.match(r"\s*Settings for (eth\d+):", ln)
        if m:
            cur = out.setdefault(m.group(1), {"up": False, "sp": "", "dup": ""})
            continue
        if cur is None:
            continue
        m = re.match(r"\s*Speed:\s*(\d+)\s*Mb/s", ln)
        if m:
            n = int(m.group(1))
            cur["sp"] = f"{n // 1000 if n % 1000 == 0 else n / 1000} Gbps" if n >= 1000 else f"{n} Mbps"
        m = re.match(r"\s*Duplex:\s*(\w+)", ln)
        if m:
            cur["dup"] = m.group(1)
        m = re.match(r"\s*Link detected:\s*(yes|no)", ln)
        if m:
            cur["up"] = m.group(1) == "yes"
    for v in out.values():
        v["st"] = f"Link detected: {'yes' if v['up'] else 'no'}" + (f" · {v['sp']} {v['dup']}" if v["up"] and v["sp"] else "")
        if not v["up"]:
            v["sp"] = ""  # sin enlace la velocidad que informa es la última negociada, no la actual
    return out


def read(conn, device_type: str, config: str = "") -> str | None:
    """Lee la tabla de puertos en la conexión del respaldo. Nunca hace fallar el respaldo: devuelve None si no puede."""
    if device_type == "watchguard_fireware":
        return read_fireware(conn)
    if device_type == "zte_olt":
        return read_zte_olt(conn, config)
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
            return "\n".join(out)
    if get_profile(device_type).family == FASTPATH:
        # EdgeSwitch X (ES-10X/ES-18X…, chip Realtek): no tiene «show interfaces status»; cada puerto se pide por número
        # con «show interfaces GigabitEthernet 1-N». Se prueba de mayor a menor hasta el número de puertos del equipo.
        for n in ESX_RANGES:
            c = f"show interfaces GigabitEthernet 1-{n}"
            try:
                text = conn.send_command(c, read_timeout=40)
            except Exception:  # noqa: BLE001
                continue
            if _esx(text):
                out.append(MARK + c + "\n" + text)
                break
    return "\n".join(out) or None


ESX_RANGES = (52, 28, 26, 18, 10, 8, 5)
_ESX_PORT = re.compile(r"(?m)^GigabitEthernet(\d+) is (administratively down|up|down)\b")
_ESX_MEDIA = re.compile(r"media type is (\w+)", re.I)


def _esx(text: str) -> list[dict]:
    """EdgeSwitch X: «GigabitEthernet9 is down» … «media type is Fiber» por cada puerto."""
    ports = []
    parts = _ESX_PORT.split(text or "")
    # split: [antes, n1, estado1, cuerpo1, n2, estado2, cuerpo2, …]
    for i in range(1, len(parts) - 2, 3):
        n, st, body = parts[i], parts[i + 1], parts[i + 2]
        m = _ESX_MEDIA.search(body)
        sfp = bool(m and m.group(1).lower().startswith("fib"))
        s = "off" if st.startswith("admin") else st
        ports.append({"n": f"gi{n}", "k": "sfp" if sfp else "cu", "s": s, "sp": "", "d": "", "net": "", "mod": "",
                      "st": f"GigabitEthernet{n} is {st}"})
    return ports


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


def _junos(text: str, model: str | None = None) -> list[dict]:
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
    return _junos_front(ports, model)


def _jpos(name: str) -> tuple[int, int, int]:
    m = re.search(r"-(\d+)/(\d+)/(\d+)$", name)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (99, 99, 99)


def _junos_front(ports: list[dict], model: str | None) -> list[dict]:
    """Junos lista los puertos por tipo (todos los ge- y después los xe-) y, en los QFX, solo los que tienen módulo
    (visto en un QFX5100-48S-6Q con 13.2X51-D25.3: «ge-0/0/16» salía primero y como cobre, y del xe-0/0/19 se pasaba al
    43). Aquí van en el orden del frente (FPC/PIC/puerto); un ge- entre puertos xe-/et- del mismo PIC es un SFP de 1 G en
    una jaula SFP+; y en los PIC de jaulas, los que faltan (hasta el número de puertos del modelo, «48S-6Q»: 48 SFP+ y
    6 QSFP+) se muestran «sin módulo»."""
    if not ports:
        return ports
    ports.sort(key=lambda p: _jpos(p["n"]))
    pics: dict[tuple[int, int], list[dict]] = {}
    for p in ports:
        pics.setdefault(_jpos(p["n"])[:2], []).append(p)
    m_sfp, m_q = model_ports(model), re.search(r"(?i)-(\d)Q\b", model or "")
    for (fpc, pic), group in pics.items():
        if not any(p["n"].startswith(("xe-", "et-")) for p in group):
            continue  # PIC de cobre (EX: ge-0/0/0…47): tal cual
        for p in group:
            if p["n"].startswith("ge-"):
                p["k"] = "sfp"
        have = {_jpos(p["n"])[2] for p in group}
        sfp_have = {_jpos(p["n"])[2] for p in group if not p["n"].startswith("et-")} or {0}
        last = max(sfp_have)
        if fpc == 0 and pic == 0 and m_sfp and m_sfp > last:  # el PIC 0 del frente tiene las jaulas SFP+ del modelo
            last = m_sfp - 1
        extra = [(n, "xe") for n in range(last + 1) if n not in have]
        if fpc == 0 and pic == 0 and m_sfp and m_q:  # QSFP+ después de los SFP+ (et-0/0/48…53)
            extra += [(n, "et") for n in range(m_sfp, m_sfp + int(m_q.group(1))) if n not in have]
        for n, kind in extra:
            group.append({"n": f"{kind}-{fpc}/{pic}/{n}", "k": "sfp", "s": "empty", "sp": "", "d": "", "net": "",
                          "mod": "", "st": ""})
    return sorted((p for g in pics.values() for p in g), key=lambda p: _jpos(p["n"]))


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


_ZTE_PORT = re.compile(r"^interface ((gpon-olt|epon-olt|xgpon-olt|gei|xgei|xlgei|cgei)_(\d+)/(\d+)/(\d+))\s*$")


# OLT ZTE ZXA10 (C300/C320): el enlace de cada PON es si sus ONU están en línea. «show gpon onu state» (sin puerto) da una
# fila por ONU: «1/2/1:3  enable  enable  operation  working  1(GPON)» (fase: working, LOS, DyingGasp, OffLine…). De esa
# salida, que en una OLT grande tiene miles de líneas, solo se guarda la cuenta por PON. Los uplinks gei_/xgei_ salen de
# «show interface <puerto>» («xgei_1/19/1 is up, line protocol is up»); de cada uno se guardan solo el estado y la velocidad.
ZTE_ONU_CMD = "show gpon onu state"
ZTE_MAX_UPLINKS = 24
_ZTE_ONU = re.compile(r"^\s*(?:[a-z]*-?onu[_-])?(\d+/\d+/\d+):\d+\s+(.+)$", re.I)
_ZTE_PHASES = {"working", "los", "dyinggasp", "offline", "logging", "syncmib", "authfailed", "lof", "sfi", "lofi", "loami"}
_ZTE_PRETTY = {"working": "working", "los": "LOS", "dyinggasp": "DyingGasp", "offline": "OffLine", "logging": "Logging",
               "syncmib": "SyncMib", "authfailed": "AuthFailed", "other": "?"}
_ZTE_IF = re.compile(r"^\s*(\S+)\s+is\s+([\w ]+?)\s*,\s*line protocol is\s+(\w+)", re.I)
_ZTE_SPEED = re.compile(r"(?i)\bspeed\s*(?:is|:)?\s*(\d+)\s*(M|G)")
_ZTE_UPLINK = re.compile(r"(?m)^interface ((?:gei|xgei|xlgei|cgei)_\d+/\d+/\d+)\s*$")


def zte_onu_counts(text: str) -> dict[str, dict[str, int]]:
    """{"1/2/1": {"working": 12, "los": 2}} de la salida de «show gpon onu state»."""
    out: dict[str, dict[str, int]] = {}
    for line in (text or "").splitlines():
        m = _ZTE_ONU.match(line)
        if not m:
            continue
        toks = [t.lower() for t in m.group(2).split()]
        ph = next((t for t in toks if t in _ZTE_PHASES), "other")
        c = out.setdefault(m.group(1), {})
        c[ph] = c.get(ph, 0) + 1
    return out


def read_zte_olt(conn, config: str) -> str | None:
    """Estado de los PON (ONU en línea) y de los uplinks de una OLT ZTE, en la conexión del respaldo."""
    out = []
    try:
        text = conn.send_command(ZTE_ONU_CMD, read_timeout=300)
    except Exception:  # noqa: BLE001
        text = ""
    counts = zte_onu_counts(text)
    if counts:
        out.append(MARK + ZTE_ONU_CMD + "\n" + "\n".join(
            k + " " + " ".join(f"{ph}={n}" for ph, n in sorted(v.items()))
            for k, v in sorted(counts.items(), key=lambda kv: tuple(int(x) for x in kv[0].split("/")))))
    for name in _ZTE_UPLINK.findall(config or "")[:ZTE_MAX_UPLINKS]:
        cmd = f"show interface {name}"
        try:
            t = conn.send_command(cmd, read_timeout=20)
        except Exception:  # noqa: BLE001
            continue
        keep = [ln.strip() for ln in t.splitlines() if _ZTE_IF.match(ln)][:1]
        keep += [ln.strip() for ln in t.splitlines() if _ZTE_SPEED.search(ln)][:1]
        if keep:
            out.append(MARK + cmd + "\n" + "\n".join(keep))
    return "\n".join(out) or None


def _zte_olt_state(raw: str) -> tuple[dict, dict]:
    """(ONU por PON, {uplink: (estado, velocidad, línea)}) de lo guardado por read_zte_olt."""
    pon: dict[str, dict[str, int]] = {}
    up: dict[str, tuple[str, str, str]] = {}
    for cmd, body in _sections(raw).items():
        if cmd == ZTE_ONU_CMD:
            for line in body.splitlines():
                k, _sp, rest = line.strip().partition(" ")
                if re.match(r"^\d+/\d+/\d+$", k):
                    pon[k] = {a: int(b) for a, b in re.findall(r"(\w+)=(\d+)", rest)}
        elif cmd.startswith("show interface "):
            name = cmd[len("show interface "):].strip()
            m = next((_ZTE_IF.match(ln) for ln in body.splitlines() if _ZTE_IF.match(ln)), None)
            if not m:
                continue
            adm, proto = m.group(2).lower(), m.group(3).lower()
            s = ("off" if "administratively" in adm or adm in ("deactivate", "disable", "disabled")
                 else "up" if proto == "up" else "down")
            sp = _ZTE_SPEED.search(body)
            speed = ""
            if sp and s == "up":
                v = int(sp.group(1))
                speed = f"{v}G" if sp.group(2).upper() == "G" else (f"{v // 1000}G" if v >= 1000 else f"{v}M")
            up[name] = (s, speed, m.group(0).strip())
    return pon, up


def _zte_olt_apply(ports: list[dict], raw: str) -> bool:
    """Pone en los puertos de la configuración el estado leído; True si el equipo informó de alguno. Un PON está activo
    si tiene al menos una ONU en línea («working»)."""
    pon, up = _zte_olt_state(raw)
    for p in ports:
        if p["s"] == "off":
            continue
        if p["n"].startswith(("gpon", "epon", "xgpon")) and pon:
            c = pon.get(p["n"].partition("_")[2], {})
            total, ok = sum(c.values()), c.get("working", 0)
            p["s"] = "up" if ok else "down"
            p["d"] = _("{0} de {1} ONU en línea").format(ok, total) if total else _("Sin ONU")
            p["st"] = " · ".join(f"{_ZTE_PRETTY.get(k, k)} {v}" for k, v in sorted(c.items())) if c else ""
        elif p["n"] in up:
            p["s"], sp, p["st"] = up[p["n"]]
            p["sp"] = sp or (p["sp"] if p["s"] == "up" else "")
    return bool(pon or up)


def _zte_olt_config(config: str) -> list[dict]:
    """OLT ZTE ZXA10 (C300, C320, C350…): las tarjetas («add-card … slotno N GTGH») y cada puerto físico de la
    configuración: PON «gpon-olt_1/2/1» (con cuántas ONU tiene dadas de alta) y uplinks «gei_»/«xgei_». La configuración
    no dice si hay enlace: solo si el puerto está apagado («shutdown»). Visto en una OLT con V2.1.0 (GTGH, HUVQ)."""
    if "add-card rackno" not in (config or ""):
        return []
    cards = {int(m.group(1)): m.group(2) for m in re.finditer(r"(?m)^add-card rackno \d+ shelfno \d+ slotno (\d+) (\S+)", config)}
    ports, cur, onus = [], None, 0

    def close():
        if cur is not None and cur["n"].startswith(("gpon", "epon", "xgpon")):
            cur["d"] = _("{0} ONU").format(onus) if onus else ""

    for line in config.splitlines():
        m = _ZTE_PORT.match(line)
        if m:
            close()
            kind, slot, port = m.group(2), int(m.group(4)), int(m.group(5))
            cur = {"n": m.group(1), "k": "sfp", "s": "on", "sp": "", "d": "", "net": "", "mod": "", "st": "",
                   "card": cards.get(slot, ""), "slot": slot, "port": port}
            if kind == "gei":
                cur["k"] = "cu"  # «hybrid-attribute fiber» lo cambia más abajo
            ports.append(cur)
            onus = 0
            continue
        if cur is None:
            continue
        if line.startswith("!") or (line and not line.startswith(" ")):
            close()
            cur = None
            continue
        t = line.strip()
        if t == "shutdown":
            cur["s"] = "off"
        elif t == "hybrid-attribute fiber":
            cur["k"] = "sfp"
        elif t.startswith("speed ") and t[6:].isdigit():
            v = int(t[6:])
            cur["sp"] = f"{v // 1000}G" if v >= 1000 else f"{v}M"
        elif re.match(r"onu \d+ type ", t):
            onus += 1
    close()
    return sorted(ports, key=lambda p: (p["slot"], p["port"]))


def _fireware_config(config: str) -> list[dict]:
    """WatchGuard Fireware: la configuración es XML («export config to console»). Cada interfaz física es un
    <physical-if> (if-num, if-dev-name, enabled, ip, netmask) dentro de una <interface> con su nombre; las VLAN dicen
    en su <member-list> por qué puerto pasan. Sin estado de enlace: la CLI no lo da en una tabla."""
    if "<interface-list>" not in (config or ""):
        return []
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(config[config.index("<profile"):] if "<profile" in config else config)
    except (ET.ParseError, ValueError):
        return []
    il = root.find("interface-list")
    if il is None:
        return []
    vlans: dict[str, list[str]] = {}
    for itf in il.findall("interface"):
        for v in itf.iter("vlan-if"):
            vid = (v.findtext("vlan-id") or "").strip()
            for m in v.iter("member"):
                dev = (m.findtext("if-dev-name") or "").strip()
                if dev and vid:
                    vlans.setdefault(dev, []).append(vid)
    ports = []
    for itf in il.findall("interface"):
        name, desc = (itf.findtext("name") or "").strip(), (itf.findtext("description") or "").strip()
        for ph in itf.iter("physical-if"):
            dev = (ph.findtext("if-dev-name") or "").strip() or f"eth{(ph.findtext('if-num') or '').strip()}"
            if not re.match(r"^eth\d+$", dev):
                continue
            ip, mask = (ph.findtext("ip") or "").strip(), (ph.findtext("netmask") or "").strip()
            ln = _mask_len(mask) if mask else None
            net = f"{ip}/{ln}" if ip and ip != "0.0.0.0" and ln is not None else ""
            if not net and vlans.get(dev):
                net = "VLAN " + ", ".join(sorted(set(vlans[dev]), key=lambda x: int(x) if x.isdigit() else 0))
            on = (ph.findtext("enabled") or "1").strip() == "1"
            ext = (ph.findtext("if-property") or "").strip() == "2"  # 1 Trusted · 2 External · 3 Disabled (T40, 12.8.1)
            ports.append({"n": dev, "k": "cu", "s": "on" if on else "off", "sp": "", "d": " · ".join(x for x in (name, desc) if x),
                          "net": net, "mod": "", "st": "", **({"ext": True} if ext else {}),
                          "xml": [f"<name>{name}</name>", f"<if-dev-name>{dev}</if-dev-name>", f"<enabled>{1 if on else 0}</enabled>"]
                          + ([f"<ip>{ip}</ip>"] if ip and ip != "0.0.0.0" else [])})
    return sorted(ports, key=lambda p: int(p["n"][3:]))


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
    # Ubiquiti (UniFi, EdgeSwitch, EdgeRouter). «cu»: puertos de cobre; los siguientes son SFP. «rear»: atrás sin
    # sensores (sin ventiladores, una sola entrada de corriente): se muestra la entrada, con corriente si el equipo responde
    (r"(?i)^ES-?18X", {"rows": 2, "grp": 8, "sfp": "pairs", "cu": 16, "rear": {"psu": ["24 V DC"], "fanless": True}}),
    (r"(?i)^ES-?10X", {"rows": 1, "sfp": "row", "cu": 8, "rear": {"psu": ["24 V DC"], "fanless": True}}),
    (r"(?i)^(US-?8|USW-?(Lite-)?8|USW-?Flex|ES-?8|ES-?10|ER-|EdgeRouter|ER\d|USW-Ultra|USG)", {"rows": 1, "sfp": "row"}),
    (r"(?i)^(US-?16|USW-?(Lite-)?16|ES-?16)", {"rows": 2, "grp": 4, "sfp": "pairs"}),
    (r"(?i)^(US-?24|USW-?24|ES-?24|US-?48|USW-?48|ES-?48|USW-Pro)", {"rows": 2, "grp": 6, "sfp": "pairs"}),
    # WatchGuard Firebox T40 (guía de hardware del fabricante): los 5 puertos van ATRÁS, entre la consola serie y los 2 USB
    # (izquierda) y Reset, interruptor y entrada de 54 V DC (derecha); el frente solo tiene luces, con los puertos de 4 a 0
    (r"(?i)^(Firebox\s*)?T40$", {"rows": 1, "sfp": "row", "ports_rear": True,
                                       "names": {"eth0": "0/WAN", "eth1": "1/LAN", "eth2": "2", "eth3": "3", "eth4": "4-PoE"},
                                       "before": ["console", "usb:USB 1", "usb:USB 2"], "after": ["reset", "switch", "dc:54 V DC"],
                                       "leds": {"left": ["Failover"], "port_rows": ["1000", "10/100"], "row_colors": ["y", ""], "order": "desc",
                                                "right": [["Status", "Mode"], ["Attn", "power"]]},
                                       "rear": {"psu": ["54 V DC"]}}),
    # Aruba S2500-48P / 48T (ArubaOS): 48 de cobre en dos filas de 12 por bloque y 4 uplinks SFP+ a la derecha. Atrás: la
    # entrada de corriente (fuente interna, sin sensores por SSH), con el puerto de gestión, la consola y el USB
    (r"(?i)^(Aruba)?S2500-48", {"rows": 2, "grp": 6, "sfp": "row", "cu": 48, "rear": {"psu": ["100-240 V AC"]}}),
    (r"(?i)^(Aruba)?S2500-24", {"rows": 2, "grp": 6, "sfp": "row", "cu": 24, "rear": {"psu": ["100-240 V AC"]}}),
    # Juniper QFX5100-48S/48SH: 48 jaulas SFP+ en dos filas (el par arriba, el impar abajo) en bloques de 12, y los 6 QSFP+
    # (et-0/0/48…53) a la derecha, también de a dos
    (r"(?i)^QFX5100-48S", {"rows": 2, "sfp": "pairs", "sgrp": 6}),
    # OLT ZTE ZXA10: chasis con tarjetas; cada ranura (GTGH de 16 PON, HUVQ de uplinks…) en su bloque, de a dos
    (r"(?i)^(ZXA10\s*)?C(220|300|320|350|600|620|650)\b", {"rows": 2, "sfp": "pairs", "slots": True}),
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
    if cu == 0 and sfp > 12:  # switch de jaulas (QFX, Nexus de fibra…): dos filas, como el frente real
        return {"rows": 2, "grp": 6, "gaps": [], "sfp": "pairs", "sgrp": 6}
    if cu <= 12:
        return {"rows": 1, "grp": 6, "gaps": [], "sfp": "row" if sfp <= 4 else "pairs"}
    cols = (cu + 1) // 2
    return {"rows": 2, "grp": 6 if cols % 6 == 0 else 4 if cols % 4 == 0 else cols, "gaps": [],
            "sfp": "bottom" if sfp <= 2 else "pairs"}


def copper_count(model: str | None) -> int | None:
    """Puertos de cobre del modelo: del catálogo o de su nombre («US-24-250W» -> 24)."""
    for pat, lay in LAYOUTS:
        if re.search(pat, (model or "").strip()) and lay.get("cu"):
            return lay["cu"]
    return model_ports(model)


def rear_from_model(model: str | None, online: bool) -> dict | None:
    """Atrás de los modelos sin sensores de fuente ni ventiladores: su entrada de corriente (con corriente si el equipo
    respondió al último respaldo) y «sin ventiladores»."""
    for pat, lay in LAYOUTS:
        if re.search(pat, (model or "").strip()) and lay.get("rear"):
            r = lay["rear"]
            return {"psu": [{"n": n, "s": "ok" if online else "fail", "st": ""} for n in r.get("psu", [])], "fan": [],
                    "fanless": bool(r.get("fanless"))}
    return None


def apply_model_kinds(ports: list[dict], model: str | None) -> None:
    """Cuando el equipo no dice el tipo de cada puerto (configuración, tablas sin «Type»): los que pasan del número de
    puertos de cobre del modelo son SFP (ES-18X: gi17 y gi18)."""
    cu = copper_count(model)
    if not cu:
        return
    for p in ports:
        m = re.search(r"(\d+)$", p["n"])
        if m and p["k"] != "sfp" and int(m.group(1)) > cu and len(ports) > cu:
            p["k"] = "sfp"


def model_ports(model: str | None) -> int | None:
    """Puertos de cobre que dice el modelo («US-24-250W» -> 24, «WS-C2960X-48FPD-L» -> 48), para saber que los
    siguientes son SFP cuando el equipo no dice el tipo de cada puerto."""
    m = re.search(r"(?i)(?:^|[-_ ])(?:[a-z]*?)(8|10|16|18|24|26|28|48|52)(?:[a-z]*)?(?:[-_ ]|$)", model or "")
    return int(m.group(1)) if m else None


def _parse_section(cmd: str, text: str, device_type: str, model: str | None = None) -> list[dict]:
    if cmd in ("show interface ip brief", "show ip interface brief"):
        return _asa(text)
    if cmd == "show interfaces terse":
        return _junos(text, model)
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
    if cmd.startswith("show interfaces GigabitEthernet 1-"):
        return _esx(text)
    if cmd == "show port status" and device_type == "aruba_os":
        return pt.arubaos(text)
    ports = pt.from_table(text, model_ports(model))
    if get_profile(device_type).family == FASTPATH or cmd in ("show interfaces status all", "show port all"):
        # FastPath (EdgeSwitch, UniFi): los puertos del frente son 0/N; 3/N son agregados (LAG) y 4/N, VLAN
        ports = [p for p in ports if re.match(r"^0/\d+$", p["n"])] or ports
    return ports


def parse(raw: str | None, device_type: str, config: str = "", model: str | None = None) -> tuple[list[dict], bool]:
    """(puertos, hay_estado_de_enlace). Sin salida de estado se usa la configuración (apagado / habilitado)."""
    ports: list[dict] = []
    if device_type == "watchguard_fireware":  # nombres, redes y apagados del XML; enlace y velocidad del informe
        ports, links = _fireware_config(config), fireware_links(raw or "")
        for p in ports:
            ln = links.get(p["n"])
            if ln and p["s"] != "off":
                p["s"], p["sp"], p["st"] = ("up" if ln["up"] else "down"), ln["sp"], ln["st"]
        return ports, bool(links) and bool(ports)
    if device_type == "zte_olt":
        ports = _zte_olt_config(config)
        if ports:
            has = _zte_olt_apply(ports, raw or "")
            apply_model_kinds(ports, model)
            return ports, has
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
            apply_model_kinds(ports, model)
            return ports, True
    # sin tabla de estado: la configuración, en el estilo que tenga (bloques con sangría, FastPath «… exit» o «set»)
    for fn in (_fireware_config, _zte_olt_config, _from_config, _fastpath_config, _set_config):
        ports = fn(config)
        if ports:
            apply_model_kinds(ports, model)
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
        elif device_type == "watchguard_fireware":
            cfg = p.get("xml") or []
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
                # un PON de una OLT ZTE: todas sus ONU («onu 3 type ZTE-F660 sn …»), que se abren desde la cabecera
                cfg = [f"interface {key}"] + (body[:129] if device_type == "zte_olt" and n.startswith(("gpon", "epon", "xgpon")) else body[:5])
                for b in body:
                    b = b.strip()
                    if b.startswith("description ") and not p.get("d"):
                        p["d"] = b[12:].strip().strip('"\'')
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


def view(dev, config: str) -> dict | None:
    """Puertos del último respaldo listos para mostrar (ficha web y app): estado, datos de la configuración y del mapa,
    disposición del frente y parte de atrás (de los sensores o, si no hay, del modelo)."""
    import json
    from datetime import datetime
    try:
        pj = json.loads(dev.ports) if dev.ports else None
    except ValueError:
        pj = None
    if not pj or not pj.get("ports"):
        return None
    try:
        nbs = json.loads(dev.neighbors or "[]")
    except ValueError:
        nbs = []
    enrich(pj["ports"], dev.device_type, config, nbs)
    rear = pj.get("rear") or {}
    if not rear.get("psu") and not rear.get("fan"):
        rear = rear_from_model(dev.model, dev.last_status == "ok") or {"psu": [], "fan": []}
    chassis = None
    if dev.device_type == "zte_olt":
        from .crypto import try_decrypt
        chassis = zte_chassis(dev.model, pj["ports"], config,
                              try_decrypt(dev.inventory_raw_enc, "") if dev.inventory_raw_enc else "")
    return {"ports": pj["ports"], "status": bool(pj.get("status")), "at": datetime.fromisoformat(pj["at"]),
            "layout": layout(dev.model, pj["ports"]), "raw": pj.get("raw") or "", "rear": rear, "chassis": chassis}


# chasis de las OLT ZTE en la cabecera de la ficha: la C300 con las tarjetas de pie (columnas, ranuras 2 a 20) y la
# C320 acostadas (filas, ranuras 1 a 4, con la fuente abajo)
_ZTE_CHASSIS = {"C300": ("v", 21, range(2, 21)), "C320": ("h", 4, range(1, 5))}
_ZTE_CTL = re.compile(r"^(SCX|SMX|SCT|SMT|SCT)", re.I)
_ZTE_PWR = re.compile(r"^PR[A-Z]", re.I)
_ZTE_UP = re.compile(r"^(HU|GU|XU)", re.I)  # tarjetas de subida (HUVQ, GUFQ…)
_ZTE_NOSIG = 0.10  # ONU sin señal por encima del 10 % de las dadas de alta en la ranura: su número en rojo


def zte_card_status(inventory: str) -> dict[int, tuple[str, str]]:
    """{ranura: (tarjeta, estado)} de «show card» (estado: INSERVICE, STANDBY, OFFLINE…)."""
    out: dict[int, tuple[str, str]] = {}
    for line in (inventory or "").splitlines():
        t = line.split()
        if len(t) >= 5 and t[0].isdigit() and t[1].isdigit() and t[2].isdigit() and not t[-1].isdigit():
            out[int(t[2])] = (t[3], t[-1].upper())
    return out


def zte_chassis(model: str | None, ports: list[dict], config: str, inventory: str = "") -> dict | None:
    """Ranuras de una OLT ZTE C300 o C320 para dibujar el chasis entero: tarjeta, tipo (servicio, controladora,
    subida, fuente, vacía), estado de la controladora (activa o en espera, de «show card») y, por ranura, los puertos
    activos y las ONU en línea y sin señal. A cada PON le agrega «onu»: [en línea, total] (en línea None sin estado)."""
    m = re.match(r"(?i)^(?:ZXA10\s*)?(C300|C320)", (model or "").strip())
    if not m or not ports:
        return None
    orient, total, base = _ZTE_CHASSIS[m.group(1).upper()]
    cards = {int(s): c for s, c in re.findall(r"(?m)^add-card rackno \d+ shelfno \d+ slotno (\d+) (\S+)", config or "")}
    status = zte_card_status(inventory)
    for s, (c, _st) in status.items():  # una tarjeta que «show card» ve y la configuración no
        cards.setdefault(s, c)
    by: dict[int, list[dict]] = {}
    for p in ports:
        if p.get("slot") is not None:
            by.setdefault(p["slot"], []).append(p)
            cards.setdefault(p["slot"], p.get("card") or "")
        if p["n"].startswith(("gpon", "epon", "xgpon")):
            ph = {k.lower(): int(v) for k, v in re.findall(r"(\w+) (\d+)", p.get("st") or "")}
            if ph:
                p["onu"] = [ph.get("working", 0), sum(ph.values())]
            else:
                n = re.match(r"(\d+)", p.get("d") or "")
                p["onu"] = [None, int(n.group(1)) if n else 0]
    slots, power = [], []
    for s in sorted(set(base) | set(cards)):
        c = cards.get(s, "")
        ps = sorted(by.get(s, []), key=lambda p: p.get("port") or 0)
        st = status.get(s, ("", ""))[1]
        if c and _ZTE_PWR.match(c):
            power.append({"n": s, "t": c, "k": "pwr", "st": "ok" if st in ("", "INSERVICE") else "bad"})
            continue
        kind = ("empty" if not c and not ps else "ctl" if _ZTE_CTL.match(c) else
                "pon" if any(p["n"].startswith(("gpon", "epon", "xgpon")) for p in ps) else
                "up" if ps or _ZTE_UP.match(c) else "other")
        on = [p["onu"][0] or 0 for p in ps if p.get("onu")]
        tot = [p["onu"][1] for p in ps if p.get("onu")]
        slots.append({"n": s, "t": c, "k": kind, "ps": [p["n"] for p in ps],
                      "st": "standby" if st == "STANDBY" else "active" if st == "INSERVICE" else st.lower(),
                      "up": sum(1 for p in ps if p["s"] == "up"), "onu": sum(on), "onut": sum(tot),
                      # sin señal: solo de los PON con estado leído (un PON apagado no cuenta sus ONU como caídas)
                      "bad": sum(p["onu"][1] - p["onu"][0] for p in ps if p.get("onu") and p["onu"][0] is not None)})
    for sl in slots:
        sl["warn"] = sl["onut"] > 0 and sl["bad"] > sl["onut"] * _ZTE_NOSIG
    return {"model": m.group(1).upper(), "orient": orient, "total": total, "slots": slots, "power": power}
