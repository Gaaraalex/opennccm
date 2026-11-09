"""Mapa de la red: vecinos LLDP/CDP de cada equipo, leídos en el respaldo.

Cada plataforma tiene su orden para ver vecinos y su formato de salida; aquí están los dos. Lo que no se reconoce no
cuenta: si un equipo no admite la orden o no tiene LLDP/CDP activado, simplemente no aporta enlaces.
Cada vecino es {"local_port", "remote_name", "remote_port", "remote_ip", "platform"} y, si las anuncia, "caps" (capacidades
LLDP/CDP normalizadas: router, bridge, wlan, phone, station…).
"""
from __future__ import annotations

import re

from .i18n import N_

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
    "mikrotik_routeros": ["/ip neighbor print detail without-paging"],
    "ubiquiti_edgeswitch": ["show lldp remote-device all"],
    "extreme_exos": ["show lldp neighbors detailed"],
    "extreme_vsp": ["show lldp neighbor"],  # VOSS / Fabric Engine: LLDP viene activado de fábrica
}
MARK = "### vecinos: "  # separa la salida de cada orden en el texto guardado
# MikroTik con RoMON (Ajustes › Mapa de la red, desactivado de fábrica): los MikroTik que se alcanzan por RoMON, aunque
# estén a varios saltos o sin dirección IP. No dice por qué puerto: el mapa los une con una línea punteada.
# Por defecto la orden solo muestra ADDRESS, COST, HOPS, PATH y L2MTU: la identidad, la versión y el modelo hay que pedirlos.
# Cada fila empieza con su marca («A» = activo) y el camino va en varias líneas, una MAC por línea (salida real de
# RouterOS 7.23). Si la versión no acepta «proplist», se usa la orden sin él (ROMON_PLAIN).
ROMON = "/tool romon discover duration=10 proplist=address,hops,path,identity,version,board"
ROMON_PLAIN = "/tool romon discover duration=10"


def commands(device_type: str) -> list[str]:
    return COMMANDS.get(device_type, [])


def _clean(v: str | None) -> str:
    return (v or "").strip().strip('"').strip()


# capacidades que anuncian LLDP («B,R», «Bridge, Router»), CDP («Router Switch IGMP») y MikroTik («bridge,router»)
CAP_WORDS = {"b": "bridge", "bridge": "bridge", "switch": "bridge", "trans-bridge": "bridge", "source-route-bridge": "bridge",
             "r": "router", "router": "router", "w": "wlan", "wlan": "wlan", "wlan-ap": "wlan", "t": "phone", "telephone": "phone",
             "phone": "phone", "s": "station", "station": "station", "station-only": "station", "host": "station",
             "c": "docsis", "docsis-cable-device": "docsis", "p": "repeater", "repeater": "repeater", "o": "other", "other": "other"}


def _caps(text: str) -> list[str]:
    t = re.sub(r"(?i)wlan access point|access point", "wlan", text or "").lower()
    out: list[str] = []
    for w in re.split(r"[\s,;\"'()]+", t):
        c = CAP_WORDS.get(w)
        if c and c not in out:
            out.append(c)
    return out


def _with_caps(n: dict, text: str | None) -> dict:
    c = _caps(text or "")
    if c:
        n["caps"] = c
    return n


_VOSS_CAP = {"B": "bridge", "r": "router", "W": "wlan", "T": "telephone", "S": "station", "R": "repeater", "O": "other"}


def _voss_lldp(text: str) -> list[dict]:
    """«show lldp neighbor» de Extreme VOSS / Fabric Engine: un bloque por vecino que empieza con «Port: 1/1» (salida real
    de un 5520 con VOSS 9.3: ChassisId, PortId «IfName 1/1», SysName, SysCap «Br / Br», SysDescr y Address)."""
    out = []
    for blk in re.split(r"(?m)^Port:\s*", text or "")[1:]:
        local = blk.split()[0] if blk.split() else ""

        def g(k):
            m = re.search(rf"(?m)^\s*{k}\s*:\s*(.*?)\s*$", blk)
            return m.group(1) if m else ""
        mac = re.search(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", g("ChassisId"))
        pid = g("PortId").split()
        caps = g("SysCap").split("/")[-1].strip()
        ip = g("Address")
        n = {"local_port": local, "remote_name": g("SysName"), "remote_port": pid[-1] if pid else "",
             "remote_ip": ip if re.fullmatch(r"[\d.]+", ip) and ip != "0.0.0.0" else "",
             "mac": mac.group(0).upper().replace("-", ":") if mac else "", "platform": g("SysDescr")[:80]}
        out.append(_with_caps(n, ",".join(_VOSS_CAP[c] for c in caps if c in _VOSS_CAP)))
    return out


def _cdp(text: str) -> list[dict]:
    out = []
    for blk in re.split(r"(?m)^-{5,}\s*$", text):
        name = re.search(r"Device ID:\s*(\S+)", blk)
        if not name:
            continue
        intf = re.search(r"Interface:\s*([^,\n]+),\s*Port ID \(outgoing port\):\s*(\S+)", blk)
        ip = re.search(r"(?:IP(?:v4)? [Aa]ddress|IP address):\s*(\d+\.\d+\.\d+\.\d+)", blk)
        plat = re.search(r"Platform:\s*([^,\n]+)", blk)
        caps = re.search(r"Capabilities:\s*([^\n]+)", blk)
        out.append(_with_caps({"local_port": _clean(intf.group(1)) if intf else "", "remote_name": _clean(name.group(1)),
                               "remote_port": _clean(intf.group(2)) if intf else "", "remote_ip": ip.group(1) if ip else "",
                               "platform": _clean(plat.group(1)) if plat else ""}, caps.group(1) if caps else None))
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
        # las habilitadas, si se dicen aparte de las que admite
        caps = re.search(r"(?im)^\s*(?:-\s*)?(?:Enabled Capabilities|System Capabilities Enabled|Capabilities Enabled)\s*[:=]\s*(.+)$", blk) or \
            re.search(r"(?im)^\s*(?:-\s*)?System Capabilities(?: Supported)?\s*[:=]\s*(.+)$", blk)
        if not name and not port:
            continue
        lp = next((g for g in (local.groups() if local else ()) if g), "")
        out.append(_with_caps({"local_port": _clean(lp), "remote_name": _clean(name.group(1)) if name else "",
                               "remote_port": _clean(port.group(1)) if port else "", "remote_ip": ip.group(1) if ip else "",
                               "platform": _clean((desc.group(1) or desc.group(2))[:120]) if desc else ""},
                              caps.group(1) if caps else None))
    return out


# RouterOS no envía los caracteres no ASCII: los muestra como su byte en hexadecimal resaltado («Caba» + «F1» resaltado +
# «as»). Al leer la salida (services._read_neighbors) cada byte resaltado se cambia por dos caracteres privados: el byte
# (U+E100 + byte) y un relleno (U+E0FF), así las columnas no se corren. _ros_text los vuelve a juntar en texto.
# En «print detail» de 7.25 el byte resaltado lleva además su barra: «Caba» + resaltado «\F1» + «as».
_HEX_HL = re.compile(r"\x1b\[[0-9;]*m((?:\\?[0-9A-Fa-f]{2})+)\x1b\[[0-9;]*m")


def mark_hex(raw: str) -> str:
    """Salida con los códigos de la terminal: los bytes resaltados pasan a caracteres privados (ver _HEX_HL)."""
    def one(m):
        h = m.group(1).replace("\\", "")
        return "".join(chr(0xE100 + int(h[k:k + 2], 16)) + "\ue0ff" for k in range(0, len(h), 2))
    return _HEX_HL.sub(one, raw)


def _dec(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _ros_text(v: str) -> str:
    """RouterOS escribe los caracteres no ASCII como bytes: «\\XX» («Caba\\F1as») en export y terse, o resaltados en las
    tablas (ver mark_hex). En UTF-8 si lo son, si no en Windows-1252 (lo habitual en RouterOS)."""
    v = re.sub(r"(?:\\[0-9A-Fa-f]{2})+",
               lambda m: _dec(bytes(int(h, 16) for h in re.findall(r"\\([0-9A-Fa-f]{2})", m.group(0)))), v or "")
    v = v.replace("\ue0ff", "")
    v = re.sub("[\ue100-\ue1ff]+", lambda m: _dec(bytes(ord(c) - 0xE100 for c in m.group(0))), v)
    return re.sub(r"(?<=[a-z])(C3[89AB][0-9A-F]|[C-F][0-9A-F])(?=[a-z])", _bare_hex, v)


def _bare_hex(m) -> str:
    """Algunas versiones (la tabla de RoMON en 7.23, «terse» en 7.x) escriben el byte sin barra ni resaltado: «CabaF1as».
    Se lee como letra solo si va entre minúsculas y da una letra (F1 → ñ, E9 → é, C3B1 → ñ en UTF-8)."""
    t = _dec(bytes.fromhex(m.group(1)))
    return t if len(t) == 1 and t.isalpha() else m.group(1)


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
        out.append(_with_caps({"local_port": local,
                               "remote_name": _ros_text(_clean(kv.get("identity") or kv.get("system-description"))),
                               "remote_port": port, "remote_ip": ip, "mac": _clean(kv.get("mac-address")).upper(),
                               # «platform=""» (vacío, como en los vecinos LLDP) no cuenta: el modelo va en la descripción
                               "platform": (" ".join(x for x in (_clean(kv.get("platform")), _clean(kv.get("board"))) if x)
                                            or _clean(kv.get("system-description")))[:80]},
                              _clean(kv.get("system-caps-enabled") or kv.get("system-caps"))))
        by.append(_clean(kv.get("discovered-by")) if "discovered-by" in kv else None)
    # MNDP y CDP atraviesan los bridges y switches: por un puerto llegan todos los MikroTik del mismo segmento, no solo el
    # de enfrente. LLDP no se reenvía, así que en un puerto con varios vecinos solo cuentan los vistos por LLDP.
    wired = [n for n in out if not is_air(n)]
    many = {p for p in {n["local_port"] for n in wired} if sum(n["local_port"] == p for n in wired) > 1}
    for n, d in zip(out, by):  # «discovered-by=cdp,lldp,mndp»: el más específico
        if d:
            n["proto"] = next((p for p in ("lldp", "cdp", "mndp") if p in d), "mndp")
    return [n for n, d in zip(out, by) if d is None or is_air(n) or n["local_port"] not in many or "lldp" in d]


def _romon(text: str) -> list[dict]:
    """«/tool romon discover»: cada fila empieza con su marca («A») y la dirección RoMON (una MAC); el camino (PATH) va en
    varias líneas, una MAC por línea, o separado por comas. PATH, IDENTITY, VERSION y BOARD se leen por la posición de su
    título (la identidad puede tener espacios, incluso dobles). HOPS es el último número antes del camino. Cada vecino
    lleva «via»: la MAC del salto anterior (vacía si está a un salto). Si la pantalla se redibujó, vale la última fila."""
    mac = r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"
    lines = text.splitlines()
    head = next((ln for ln in lines if re.search(r"\bADDRESS\b", ln) and re.search(r"\bPATH\b", ln)
                 and not ln.lstrip().startswith("Columns")), None)
    if head is None:
        return []
    cols = {m.group(0): m.start() for m in re.finditer(r"[A-Z][A-Z0-9-]*", head)}
    order = sorted(cols.items(), key=lambda kv: kv[1])

    def field(line, name):
        if name not in cols:
            return ""
        start = cols[name]
        nxt = [pos for _n, pos in order if pos > start]
        return line[start:nxt[0] if nxt else None].strip()

    found: dict[str, dict] = {}
    cur = None
    for line in lines:
        if line is head or not line.strip():
            continue
        m = re.match(r"^[A-Z*\s]{0,6}?(" + mac + r")\b", line)
        if m and m.start(1) < cols.get("PATH", 999):
            before = line[m.end(1):cols["PATH"]] if "PATH" in cols else ""
            nums = re.findall(r"\d+", before)
            path = re.findall(mac, field(line, "PATH"))
            board = field(line, "BOARD")
            cur = {"local_port": "", "remote_name": _ros_text(field(line, "IDENTITY")), "remote_port": "", "remote_ip": "",
                   "mac": m.group(1).upper(), "platform": ("MikroTik " + board) if board else "MikroTik", "romon": True,
                   "hops": int(nums[-1]) if nums else 0, "path": [x.upper() for x in path], "version": field(line, "VERSION")}
            found[cur["mac"]] = cur
        elif cur is not None and "PATH" in cols and not re.search(mac, line[:cols["PATH"]]):
            cur["path"] += [x.upper() for x in re.findall(mac, field(line, "PATH"))]
    out = []
    for n in found.values():
        path = n.pop("path")
        n["via"] = path[-2] if len(path) > 1 else ""
        out.append(n)
    return out


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
        start = len(found)
        if cmd.startswith("/tool romon"):
            found += _romon(out)
        elif "cdp" in cmd:
            found += _cdp(out)
        elif device_type == "mikrotik_routeros":
            found += _mikrotik(out)
        elif device_type == "extreme_vsp":
            found += _voss_lldp(out)
        elif device_type in ("huawei", "huawei_vrpv8"):
            found += _table(out, {"local": 0, "name": 1, "port": 2}, r"Local Intf\s+Neighbor Dev")
        elif device_type == "ubiquiti_edgeswitch":
            found += _table(out, {"local": 0, "port": 3, "name": 4}, r"Interface\s+RemID")
        elif device_type == "juniper_junos":
            # Junos mete en la salida los avisos a la sesión («# commit confirmed will be rolled back in 5 minutes»,
            # mientras OpenNCCM confirma un cambio): eran un vecino «minutes» en el puerto «#»
            out = "\n".join(ln for ln in out.splitlines() if not ln.lstrip().startswith("#"))
            found += [dict(r, remote_name=r["remote_name"]) for r in
                      _table(out, {"local": 0, "port": -2, "name": -1}, r"Local Interface")]
        else:
            found += _lldp_detail(out)
        proto = "romon" if cmd.startswith("/tool romon") else "cdp" if "cdp" in cmd else "lldp"
        for n in found[start:]:  # por qué protocolo se vio (aviso de vecino nuevo o perdido)
            n.setdefault("proto", proto)
    uniq, keys = [], set()
    for n in found:
        if not (n["remote_name"] or n["remote_ip"] or n.get("romon")):  # RoMON sin identidad: queda su MAC
            continue
        k = (short_port(n["local_port"]), norm_name(n["remote_name"]) or n["remote_ip"] or n.get("mac"), bool(n.get("romon")))
        if k not in keys:
            keys.add(k)
            uniq.append(n)
    return uniq


WIRELESS = re.compile(r"(?i)^(?:ath|wlan|wifi|wlp|radio)\d")


def is_air(n: dict) -> bool:
    """El vecino nos ve por su radio y nosotros a él por un cable: está detrás de una antena (el otro extremo de un PtP o
    un CPE). Si el puerto local también es de radio, es un enlace inalámbrico directo y no cuenta aquí."""
    return bool(WIRELESS.match(n.get("remote_port") or "")) and not WIRELESS.match(n.get("local_port") or "")


def is_wireless(pa: str, pb: str) -> bool:
    """Enlace inalámbrico: uno de los dos extremos es una radio (ath0, wlan1…)."""
    return bool(WIRELESS.match(pa or "") or WIRELESS.match(pb or ""))


# modelos de antena de cliente (CPE): vistos por su radio son siempre equipos de usuario, aunque haya uno solo por puerto
# (con una VLAN por cliente, cada CPE llega por su propia interfaz)
CPE_MODELS = re.compile(r"(?i)litebeam|nanostation|\bloco|nanobeam|nanobridge|airgrid|powerstation|\bbullet|"
                        r"\bltu[- ]?(?:lite|pro|long|extreme)|\bwave[- ]?(?:nano|pro|lr)|\bsxt|\blhg|\bldf|\bdisc\b|\bgroove")


def lldp_only(nbrs: list[dict]) -> list[dict]:
    """Los vecinos con su puerto (LLDP, CDP, MNDP): sin los de RoMON, que no dicen por dónde se conectan."""
    return [n for n in nbrs if not n.get("romon")]


def network_view(nbrs: list[dict]) -> tuple[list[dict], int]:
    """Vecinos que se dibujan y cuántos equipos de usuario se quitan. Por un mismo puerto cableado, uno solo visto por su
    radio es el otro extremo de un PtP (o la estación) y queda como enlace inalámbrico; varios son los CPE de los clientes
    de un punto de acceso y no se dibujan (el punto de acceso, visto por su cable, sí). Un modelo de CPE visto por su radio
    nunca se dibuja."""
    air: dict[str, int] = {}
    for n in nbrs:
        if is_air(n):
            air[short_port(n.get("local_port") or "")] = air.get(short_port(n.get("local_port") or ""), 0) + 1
    kept = [n for n in nbrs if not is_air(n) or (air[short_port(n.get("local_port") or "")] == 1
                                                 and not CPE_MODELS.search(n.get("platform") or ""))]
    return kept, len(nbrs) - len(kept)


# tipos de equipo para el filtro del mapa (Ajustes › Mapa de la red): por lo que anuncia el vecino (plataforma, modelo,
# descripción) y, si no dice nada, por el nombre de sus puertos
FAMILIES = [("mikrotik", "MikroTik"), ("ubiquiti", "Ubiquiti"), ("juniper", "Juniper"), ("cisco", "Cisco"),
            ("huawei", "Huawei"), ("aruba", "HP / Aruba"), ("extreme", "Extreme"), ("other", N_("Otros y desconocidos"))]
_FAM = [
    ("mikrotik", r"mikrotik|routeros|\b(?:ccr|crs|css|rb|chr)\d|\bhex\b|\bhap\b",
     r"^(?:ether|sfp-sfpplus|sfp28-|qsfp28-|qsfpplus|combo|wlan|wifi)\d"),
    ("ubiquiti", r"ubiquiti|ubnt|edge ?(?:switch|router|point)|unifi|\busw|\busl\d|\bus-?(?:8|16|24|48|xg)\b|\bes-\d+|"
                 r"\budm\b|\busg\b|\buxg|air ?os|airmax|airfiber|\baf-?\d|litebeam|"
                 r"nanostation|nanobeam|powerbeam|rocket|prism|\bltu|\bloco|bullet|isostation", r"^ath\d"),
    ("juniper", r"juniper|junos|\b(?:srx|qfx|ex\d{4}|mx\d+)", r"^(?:ge|xe|et|mge)-\d+/\d+/\d+|^(?:me|em|fxp)\d$"),
    ("cisco", r"cisco|catalyst|nx-os|\bios\b|ws-c|air-ap", r"^(?:gigabitethernet|tengigabitethernet|fastethernet|gi|te|fa)\d"),
    ("huawei", r"huawei|\bvrp\b", r"^(?:xge|ge|gigabitethernet)\d+/\d+/\d+"),
    ("aruba", r"aruba|procurve|hewlett|\bhpe?\b", ""),
    ("extreme", r"extreme|exos|\bvoss\b|fabric engine", ""),
]


# prefijos de MAC (OUI) de fabricantes que a menudo no dicen su modelo por LLDP (los switches UniFi, por ejemplo)
MAC_VENDOR = {o: "ubiquiti" for o in (
    "00156D", "002722", "0418D6", "0CEA14", "18E829", "1C6A1B", "245A4C", "24A43C", "28704E", "44D9E7", "602232", "687251",
    "68D79A", "70A741", "7483C2", "74ACB9", "784558", "788A20", "802AA8", "847848", "942A6F", "9C05D6", "AC8BA9", "B4FBE4",
    "D021F9", "D8B370", "DC9FDB", "E063DA", "E43883", "F09FC2", "F492BF", "FCECDA")}


def family(n: dict) -> str:
    """Fabricante de un vecino (clave de FAMILIES)."""
    return family_why(n)[0]


def family_why(n: dict) -> tuple[str, str]:
    """Fabricante y cómo se reconoció: «platform» (lo que anuncia), «mac» (el fabricante de su MAC), «port» (el nombre de su
    puerto) o «» (no se sabe)."""
    plat = (n.get("platform") or "").lower()
    for fam, by_plat, _ in _FAM:
        if re.search(by_plat, plat):
            return fam, "platform"
    mac = re.sub(r"[^0-9A-F]", "", (n.get("mac") or "").upper())[:6]
    if mac in MAC_VENDOR:
        return MAC_VENDOR[mac], "mac"
    for fam, _, by_port in _FAM:
        if by_port and re.search(by_port, n.get("remote_port") or "", re.I):
            return fam, "port"
    return "other", ""


# tipo de equipo (icono del mapa): por el modelo o la plataforma y, si no dicen nada, por las capacidades que anuncia
ROLES = [("router", N_("Router")), ("switch", N_("Switch")), ("ap", N_("Antena / AP")), ("firewall", N_("Firewall")),
         ("unknown", N_("Desconocido"))]
_ROLE_MODEL = [
    ("firewall", r"\bsrx\d|fortigate|fortinet|\basa ?\d|palo ?alto|\bpa-\d{3}|sophos|watchguard|pfsense|opnsense|check ?point|"
                 r"\bfirewall"),
    ("ap", CPE_MODELS.pattern.replace("(?i)", "") + r"|rocket|prism|powerbeam|airfiber|\baf-?\d|\bltu|airmax|\buap|unifi ?ap|"
                                                    r"nanohd|\bcap ?(?:ac|ax|xl)|\bwap|aironet|air-ap|\bgwn76|access point|"
                                                    r"\brb9(?:1[12]|2[12]|53)|\bmantbox|\bbasebox|\bnetbox|\bqrt\b|\bnetmetal|"
                                                    r"\bwap ?ac|\bdynadish|\bgroove"),
    ("switch", r"\bcrs\d|\bcss\d|catalyst|ws-c|\bc9[23]00|\bex\d{4}|\bqfx|edge ?switch|\busw|\busl\d|\bus-?(?:8|16|24|48|xg)\b|"
               r"\bes-\d+|unifi ?switch|nexus|procurve|"
               r"\bswitch"),
    ("router", r"\bccr\d|\brb\d|\bhex\b|\bhap\b|\bchr\b|\bmx\d{2,}|\bisr\d|\basr\d|edge ?router|\ber-\d|\bl009|\brouter\b"),
]
# perfiles de OpenNCCM cuyo tipo se sabe aunque no se conozca el modelo
DTYPE_ROLE = {
    **{k: "firewall" for k in ("cisco_asa", "cisco_ftd", "fortinet", "paloalto_panos", "pfsense", "opnsense", "sophos_sfos",
                               "watchguard_fireware", "checkpoint_gaia", "juniper_screenos", "hillstone_stoneos")},
    **{k: "ap" for k in ("ubiquiti_airos", "ubiquiti_airfiber", "ubiquiti_unifi_ap", "cisco_ap", "extreme_wing")},
    **{k: "switch" for k in ("ubiquiti_edgeswitch", "ubiquiti_unifiswitch", "mikrotik_swos", "cisco_s200", "cisco_s300", "cisco_s500",
                             "aruba_aoscx", "aruba_osswitch", "hp_procurve", "extreme_exos", "dell_os6", "dell_os10",
                             "dell_powerconnect", "tplink_jetstream", "netgear_prosafe")},
    **{k: "router" for k in ("ubiquiti_edgerouter", "ubiquiti_usg", "vyos", "cisco_xr", "nokia_sros")},
}
# fabricante de los equipos de OpenNCCM, por el de su perfil
VENDOR_FAMILY = {"MikroTik": "mikrotik", "Ubiquiti": "ubiquiti", "Juniper": "juniper", "Cisco": "cisco", "Huawei": "huawei",
                 "HPE": "aruba", "HPE Aruba": "aruba", "Extreme": "extreme"}


def role_why(text: str, caps: list[str] | None = None) -> tuple[str, str]:
    """Tipo de equipo y cómo se supo: «model» (modelo o plataforma), «caps» (capacidades anunciadas) o «»."""
    t = (text or "").lower()
    for role, rx in _ROLE_MODEL:
        if re.search(rx, t):
            return role, "model"
    c = caps or []
    for cap, role in (("router", "router"), ("wlan", "ap"), ("bridge", "switch")):
        if cap in c:
            return role, "caps"
    return "unknown", ""


MAP_ROLES = ("router", "switch", "ap", "firewall")
_RADIO_IF = re.compile(r"(?i)^(?:wlan|wifi)\d")


def radio_up(ports_json: str | None) -> bool:
    """El equipo tiene una interfaz inalámbrica (wlan1, wifi1…) que no está apagada, según el último respaldo."""
    import json
    try:
        ps = (json.loads(ports_json or "{}") or {}).get("ports") or []
    except (ValueError, AttributeError):
        return False
    return any(_RADIO_IF.match(str(p.get("n") or "")) and p.get("s") != "off" for p in ps if isinstance(p, dict))


def device_role(device_type: str, model: str | None, caps: list[str] | None = None, ports: str | None = None,
                manual: str | None = None) -> tuple[str, str]:
    """Tipo de un equipo de OpenNCCM: el que puso el administrador; si no, por su modelo; si no, por su perfil; si no, por
    lo que anuncia a sus vecinos. Un MikroTik que sale como router pero tiene una wlan o wifi activa es inalámbrico."""
    if manual in MAP_ROLES:
        return manual, "manual"
    role, by = role_why(model or "")
    if role == "router" and device_type == "mikrotik_routeros" and radio_up(ports):
        return "ap", "radio"
    if by:
        return role, by
    if device_type in DTYPE_ROLE:
        return DTYPE_ROLE[device_type], "type"
    return role_why("", caps)


def norm_name(name: str) -> str:
    """Nombre comparable: sin dominio ni mayúsculas («SW-2.empresa.local» = «sw-2»)."""
    return (name or "").split(".")[0].strip().lower()


def short_port(p: str) -> str:
    """«GigabitEthernet0/1» = «Gi0/1» (CDP y LLDP nombran el mismo puerto distinto)."""
    m = re.match(r"([A-Za-z]{2})[A-Za-z\-]*\s*([\d/.:]+)$", (p or "").strip())
    return (m.group(1) + m.group(2)).lower() if m else (p or "").strip().lower()
