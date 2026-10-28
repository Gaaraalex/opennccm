"""Mapa de la red: vecinos LLDP/CDP de cada equipo, leídos en el respaldo.

Cada plataforma tiene su orden para ver vecinos y su formato de salida; aquí están los dos. Lo que no se reconoce no
cuenta: si un equipo no admite la orden o no tiene LLDP/CDP activado, simplemente no aporta enlaces.
Cada vecino es {"local_port", "remote_name", "remote_port", "remote_ip", "platform"}.
"""
from __future__ import annotations

import re

CISCO_LIKE = ["show cdp neighbors detail", "show lldp neighbors detail"]
COMMANDS = {
    **{k: CISCO_LIKE for k in ("cisco_ios", "cisco_xe", "cisco_nxos", "cisco_xr", "cisco_s300", "cisco_s200", "cisco_s500",
                               "cisco_wlc", "cisco_ioswlc")},
    **{k: ["show lldp neighbors detail"] for k in ("arista_eos", "dell_os10", "dell_os6", "dell_os9", "dell_force10",
                                                    "ruckus_fastiron", "allied_telesis_awplus", "ubiquiti_unifiswitch")},
    "aruba_aoscx": ["show lldp neighbor-info detail"],
    "aruba_osswitch": ["show lldp info remote-device detail"], "hp_procurve": ["show lldp info remote-device detail"],
    "hp_comware": ["display lldp neighbor-information verbose"], "h3c_comware": ["display lldp neighbor-information verbose"],
    "huawei": ["display lldp neighbor brief"], "huawei_vrpv8": ["display lldp neighbor brief"],
    "juniper_junos": ["show lldp neighbors"],
    "mikrotik_routeros": ["/ip neighbor print detail terse without-paging"],
    "ubiquiti_edgeswitch": ["show lldp remote-device all"],
    "extreme_exos": ["show lldp neighbors detailed"],
}
MARK = "### vecinos: "  # separa la salida de cada orden en el texto guardado


def commands(device_type: str) -> list[str]:
    return COMMANDS.get(device_type, [])


def _clean(v: str | None) -> str:
    return (v or "").strip().strip('"').strip()


def _cdp(text: str) -> list[dict]:
    out = []
    for blk in re.split(r"(?m)^-{5,}\s*$", text):
        name = re.search(r"Device ID:\s*(\S+)", blk)
        if not name:
            continue
        intf = re.search(r"Interface:\s*([^,\n]+),\s*Port ID \(outgoing port\):\s*(\S+)", blk)
        ip = re.search(r"(?:IP(?:v4)? [Aa]ddress|IP address):\s*(\d+\.\d+\.\d+\.\d+)", blk)
        plat = re.search(r"Platform:\s*([^,\n]+)", blk)
        out.append({"local_port": _clean(intf.group(1)) if intf else "", "remote_name": _clean(name.group(1)),
                    "remote_port": _clean(intf.group(2)) if intf else "", "remote_ip": ip.group(1) if ip else "",
                    "platform": _clean(plat.group(1)) if plat else ""})
    return out


# LLDP «detalle» en formato «Clave: valor» (Cisco, NX-OS, Arista, Dell, Aruba, HP, Comware, EXOS…): un bloque por vecino
LLDP_START = re.compile(r"(?im)^\s*(?:Local Intf|Local Interface|Local Port(?: ID| Number)?|LLDP neighbor-information of port"
                        r"|Interface (\S+) detected|LLDP Port (\S+) detected|Port\s*:)\b")


def _lldp_detail(text: str) -> list[dict]:
    out = []
    starts = [m.start() for m in LLDP_START.finditer(text)]
    for a, b in zip(starts, starts[1:] + [len(text)]):
        blk = text[a:b]
        local = re.search(r"(?im)^\s*(?:Local Intf|Local Interface|Local Port(?: ID| Number)?)\s*[:=]\s*(\S+)", blk) or \
            re.search(r"(?i)(?:Interface|LLDP Port|neighbor-information of port)\s+\[?(\S+?)\]?\s+detected|of port\s+\d+\[(\S+)\]", blk) or \
            re.search(r"(?im)^\s*Port\s*:\s*(\S+)", blk)
        # «System Name: x», «- System Name: "x"» (EXOS); «Port ID type: …; Port ID: "1:24"» (EXOS): el valor tras «Port ID:»
        name = re.search(r"(?im)^\s*(?:-\s*)?(?:System Name|SysName|System name|Neighbor Device|NeighborSysName)\s*[:=]\s*\"?([^\"\r\n]+)", blk)
        port = re.search(r"(?i)(?<![\w-])(?:Port id|Port ID|PortId|Port-ID|Neighbor Port ID)\s*[:=]\s*\"?([^\"\r\n;]+)", blk)
        ip = re.search(r"(?is)Management Address(?:es)?\s*[:=]?\s*(?:\n\s*)?(?:IP(?:v4)?\s*[:=]?\s*)?(\d+\.\d+\.\d+\.\d+)", blk) or \
            re.search(r"(?im)^\s*(?:IP|IPv4 Address|Address)\s*[:=]\s*(\d+\.\d+\.\d+\.\d+)", blk)
        desc = re.search(r"(?im)^\s*System Description\s*[:=]\s*\"?([^\"\r\n]*)\"?\s*\n?\s*([^\r\n]*)", blk)
        if not name and not port:
            continue
        lp = next((g for g in (local.groups() if local else ()) if g), "")
        out.append({"local_port": _clean(lp), "remote_name": _clean(name.group(1)) if name else "",
                    "remote_port": _clean(port.group(1)) if port else "", "remote_ip": ip.group(1) if ip else "",
                    "platform": _clean((desc.group(1) or desc.group(2))[:120]) if desc else ""})
    return out


def _mikrotik(text: str) -> list[dict]:
    out, by = [], []
    for raw in re.split(r"(?m)^\s*\d+\s+", text):
        # en «terse» los valores con espacios van sin comillas («identity=SW-C-Los Llanos»): llegan hasta la clave siguiente
        kv = dict(re.findall(r'([\w-]+)=("[^"]*"|.*?)(?=\s+[\w-]+=|\s*$)', raw.strip(), re.S))
        if "interface" not in kv:
            continue
        local = _clean(kv["interface"]).split(",")[0]
        # RouterOS 7 anuncia por LLDP el puerto como «bridge1/ether1»: el puerto es el de después de la barra
        port = re.sub(r"^[A-Za-z][\w.-]*/(?=[A-Za-z])", "", _clean(kv.get("interface-name")))
        ip = next((v for v in (_clean(kv.get("address4")), _clean(kv.get("address"))) if re.fullmatch(r"[\d.]+", v)), "")
        out.append({"local_port": local, "remote_name": _clean(kv.get("identity") or kv.get("system-description")),
                    "remote_port": port, "remote_ip": ip,
                    "platform": _clean(" ".join(x for x in (kv.get("platform"), kv.get("board")) if x))})
        by.append(_clean(kv.get("discovered-by")) if "discovered-by" in kv else None)
    # MNDP y CDP atraviesan los bridges y switches: por un puerto llegan todos los MikroTik del mismo segmento, no solo el
    # de enfrente. LLDP no se reenvía, así que en un puerto con varios vecinos solo cuentan los vistos por LLDP.
    many = {p for p in {n["local_port"] for n in out} if sum(n["local_port"] == p for n in out) > 1}
    return [n for n, d in zip(out, by) if d is None or n["local_port"] not in many or "lldp" in d]


def _table(text: str, cols: dict[str, int], header: str) -> list[dict]:
    """Tablas de una línea por vecino (Huawei «brief», EdgeSwitch, Junos): columnas por posición."""
    out, seen = [], False
    for line in text.splitlines():
        if re.search(header, line, re.I):
            seen = True
            continue
        t = line.split()
        if not seen or len(t) < max(cols.values()) + 1 or set(line.strip()) <= set("-= "):
            continue
        out.append({"local_port": t[cols["local"]], "remote_name": t[cols["name"]] if cols.get("name") is not None else "",
                    "remote_port": t[cols["port"]] if cols.get("port") is not None else "", "remote_ip": "", "platform": ""})
    return out


def parse(text: str, device_type: str) -> list[dict]:
    """Vecinos de la salida guardada (las órdenes separadas por MARK). Sin duplicados (CDP y LLDP del mismo enlace)."""
    found: list[dict] = []
    parts = re.split(re.escape(MARK) + r"(.*)\n", "\n" + text)
    pairs = list(zip(parts[1::2], parts[2::2])) or [("", text)]
    for cmd, out in pairs:
        if re.search(r"(?im)^\s*%|invalid input|unrecognized command|bad command name|syntax error", out[:400]):
            continue
        if "cdp" in cmd:
            found += _cdp(out)
        elif device_type == "mikrotik_routeros":
            found += _mikrotik(out)
        elif device_type in ("huawei", "huawei_vrpv8"):
            found += _table(out, {"local": 0, "name": 1, "port": 2}, r"Local Intf\s+Neighbor Dev")
        elif device_type == "ubiquiti_edgeswitch":
            found += _table(out, {"local": 0, "port": 3, "name": 4}, r"Interface\s+RemID")
        elif device_type == "juniper_junos":
            found += [dict(r, remote_name=r["remote_name"]) for r in
                      _table(out, {"local": 0, "port": -2, "name": -1}, r"Local Interface")]
        else:
            found += _lldp_detail(out)
    uniq, keys = [], set()
    for n in found:
        if not (n["remote_name"] or n["remote_ip"]) or is_client(n):
            continue
        k = (short_port(n["local_port"]), norm_name(n["remote_name"]) or n["remote_ip"])
        if k not in keys:
            keys.add(k)
            uniq.append(n)
    return uniq


WIRELESS = re.compile(r"(?i)^(?:ath|wlan|wifi|wlp|radio)\d")


def is_client(n: dict) -> bool:
    """Equipo de usuario: el vecino nos ve por su radio (CPE como LiteBeam o NanoStation, o un MikroTik cliente) y no
    por un cable. No es un enlace de la red: está detrás de un punto de acceso. Si el puerto local también es de radio,
    es un enlace inalámbrico entre dos equipos de red y sí cuenta."""
    return bool(WIRELESS.match(n.get("remote_port") or "")) and not WIRELESS.match(n.get("local_port") or "")


def norm_name(name: str) -> str:
    """Nombre comparable: sin dominio ni mayúsculas («SW-2.empresa.local» = «sw-2»)."""
    return (name or "").split(".")[0].strip().lower()


def short_port(p: str) -> str:
    """«GigabitEthernet0/1» = «Gi0/1» (CDP y LLDP nombran el mismo puerto distinto)."""
    m = re.match(r"([A-Za-z]{2})[A-Za-z\-]*\s*([\d/.:]+)$", (p or "").strip())
    return (m.group(1) + m.group(2)).lower() if m else (p or "").strip().lower()
