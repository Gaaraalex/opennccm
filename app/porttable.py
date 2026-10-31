"""Lectores de la tabla de puertos de cada fabricante (app/ports.py los usa al respaldar).

La mayoría de los equipos dan el estado de sus puertos en una tabla con cabecera: «show interfaces status» (Cisco y
clones, Arista, Dell, TP-Link…), «show interfaces status all» (Broadcom FastPath: Ubiquiti EdgeSwitch y UniFi),
«display interface brief» (Huawei, H3C, HPE Comware), «show interfaces brief» (HPE ProCurve / Aruba OS-Switch),
«show interface brief» (Aruba AOS-CX), «show ports no-refresh» (Extreme EXOS), «show port» (Nokia SR OS),
«show port summary» (Cisco WLC). Se leen con un único lector de tablas que encuentra las columnas por su cabecera
(o por la línea de guiones que la subraya) y reconoce los valores de estado de cada fabricante.

Los que no usan tabla tienen su propio lector: EdgeOS/VyOS («show interfaces», columna S/L), FortiGate («get system
interface physical»), PAN-OS («show interface all»), Check Point Gaia («show interfaces all»), Linux y equipos con
Linux embebido (airOS, airFiber, UniFi AP: /sys/class/net) y FreeBSD (pfSense, OPNsense: «ifconfig -a»).
"""
import re

# interfaces que no son puertos físicos del frente
VIRTUAL = re.compile(
    r"(?i)^(vlan|vl\d|vlanif|vlan-interface|lo\d*$|loopback|inloop|null|nu\d|tunnel|tu\d|port-channel|po\d|"
    r"bridge-aggregation|route-aggregation|eth-trunk|lag|ae\d|irb|bvi|dialer|virtual|vt\d|nve|cpu|trk|switch\d|"
    r"br\d|br-|bridge|bond|vif|tap|tun|docker|veth|wg\d|ppp|pppoe|imq|ifb|gre|sit|ip6tnl|enc\d|pflog|pfsync|ovpn|"
    r"lagg|carp|ath\d|wifi\d|wlan|ra\d|mon\.|teql|erspan|vxlan|macsec|dummy|ipsec|ssl\.|fortilink|npu\d|"
    r"internal-|modem|mesh|any$|all$|mgmt-|vme|em\d+\.|fxp|lsi|jsrv|pime|pimd|pp0|gr-|ip-|lt-|mt-|sp-|vtep|"
    r"vcp-|bme|pfe-|pfh-|dsc|demux|esi|fti|ipip|lc-|cbp|rbeb|ms-|ls-|vsp|sxe|"
    r"stack|stk|ten-?gigabitethernet\d+/\d+/\d+\.\d+)")
SFP_NAME = re.compile(r"(?i)^(q?sfp|combo|xe-|et-|xle-|ce-|te\d|tengig|tw|twentyfive|fo\d|forty|hu\d|hundred|"
                      r"xgigabit|xge|10ge|25ge|40ge|100ge|x\d|xg\d)")
SFP_TYPE = re.compile(r"(?i)sfp|fib(er|re)|base-?[lszecbr][xr4]|not ?present|gbic|xfp|qsfp|10g|25g|40g|100g|cx4|da\d|"
                      r"twinax|lr|sr\b|lx|sx")
LINK_OFF = re.compile(r"(?i)^(disabled?|adm|admin(istratively)? ?down|\*down|shutdown|down \(admin\))(\b|$)")
NOT_PRESENT = re.compile(r"(?i)not ?present|sfp ?absent|no ?transceiver|\bnp\b|absent|empty")


def is_virtual(name: str) -> bool:
    return bool(VIRTUAL.match(name or "")) or "." in (name or "") and not re.match(r"^\d+\.\d+$", name)


def speed(v: str) -> str:
    """«1000 Full», «a-1000», «1000Mbps», «1G(a)», «10GigFD», «1000FDx», «10G», «1Gbps» -> «1 Gbps» / «100 Mbps»."""
    v = (v or "").strip().lower().removeprefix("a-")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(g|m|k)?", v)
    if not m or re.match(r"^(auto|ukn|unknown|n/?a|-+)$", v):
        return ""
    n = float(m.group(1))
    unit = m.group(2) or ("g" if re.search(r"\d\s*gig|\dg", v) else "m")
    mbps = n * 1000 if unit == "g" else n / 1000 if unit == "k" else n
    if mbps <= 0:
        return ""
    return f"{mbps / 1000:g} Gbps" if mbps >= 1000 else f"{mbps:g} Mbps"


# --------------------------------------------------------------------------- lector genérico de tablas
_DASHES = re.compile(r"^\s*[-=+ |]+\s*$")
_PORTISH = re.compile(r"(?i)^\s*(port|interface|intf|ifname|pr|name|port\s*id)\b")
_STATEWORD = re.compile(r"(?i)\b(status|state|link|phy|enabled|admin|oper|protocol|connected)\b")


def _spans_from_dashes(line: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"[-=]+", line)]


def _spans_from_gutters(lines: list[str]) -> list[tuple[int, int]]:
    """Columnas por los «canales» en blanco que atraviesan la cabecera y todas las filas: así «Enabled Status» (un
    espacio en la cabecera) son dos columnas si los valores de debajo no pisan ese espacio, y «VLAN Name» es una sola si
    «Default» lo ocupa."""
    width = max(len(l) for l in lines)
    blank = [all(i >= len(l) or l[i] in " |" for l in lines) for i in range(width)]
    spans, start = [], None
    for i, b in enumerate(blank + [True]):
        if not b and start is None:
            start = i
        elif b and start is not None:
            spans.append((start, i))
            start = None
    return [(s, spans[k + 1][0] if k + 1 < len(spans) else 10 ** 6) for k, (s, _e) in enumerate(spans)]


def tables(text: str) -> list[tuple[list[str], list[list[str]], list[str]]]:
    """Todas las tablas de la salida: [(cabeceras, filas de celdas, líneas originales)]."""
    lines = [l.rstrip() for l in (text or "").replace("\t", "    ").splitlines()]
    out, i = [], 0
    while i < len(lines):
        l = lines[i]
        if not (_PORTISH.match(l) and _STATEWORD.search(l)):
            i += 1
            continue
        head = [l]
        j = i + 1
        # segunda línea de cabecera (EXOS «# String …», AOS-CX «VLAN (Mb/s)», Nokia «Id State»)
        if j < len(lines) and lines[j].strip() and not _DASHES.match(lines[j]) and not re.match(r"^\s*\S*\d", lines[j]):
            head.append(lines[j])
            j += 1
        if i > 0 and lines[i - 1].strip() and not _DASHES.match(lines[i - 1]) and len(lines[i - 1]) - len(lines[i - 1].lstrip()) > 4:
            head.insert(0, lines[i - 1])  # cabecera en dos líneas con la primera desplazada (FastPath, WLC, ProCurve)
        dash = None
        if j < len(lines) and _DASHES.match(lines[j]) and len(_spans_from_dashes(lines[j])) >= 3:
            dash = lines[j]
        while j < len(lines) and _DASHES.match(lines[j]):
            j += 1
        rows = []
        while j < len(lines):
            r = lines[j]
            if not r.strip():
                if rows:
                    break
                j += 1
                continue
            if _DASHES.match(r):
                if rows:
                    break
                j += 1
                continue
            if _PORTISH.match(r) and _STATEWORD.search(r):
                break
            rows.append(r)
            j += 1
        i = j
        if not rows:
            continue
        if dash is not None:
            spans = _spans_from_dashes(dash)
            spans = [(s, spans[k + 1][0] if k + 1 < len(spans) else 10 ** 6) for k, (s, _e) in enumerate(spans)]
        else:
            spans = _spans_from_gutters(head + rows)
        names = [" ".join(h[s:e].strip(" |") for h in head if h[s:e].strip(" |")).strip() for s, e in spans]
        cells = [[r[s:e].strip(" |") if s < len(r) else "" for s, e in spans] for r in rows]
        out.append((names, cells, [r.strip() for r in rows]))
    return out


def _col(names: list[str], *pats: str, exclude: str | None = None) -> int | None:
    for p in pats:
        for k, n in enumerate(names):
            if k and re.search(p, n, re.I) and not (exclude and re.search(exclude, n, re.I)):
                return k
    return None


UP = re.compile(r"(?i)^(up|connected|connect|active|a|link ?up|yes|u|on|forwarding|forw|running|link-ok|online)\b")
OFF = re.compile(r"(?i)^(disabled?|adm|admin(istratively)? ?down|\*down|off|no|d|down|shutdown|disable|dis|lockout|"
                 r"down \(admin\))\b")


def from_table(text: str, model_n: int | None = None) -> list[dict]:
    ports = []
    for names, rows, raw in tables(text):
        link = _col(names, r"^link( state| status)?$", r"link state|link status|oper", r"^phy", r"^status$",
                    r"status", r"^state$", r"^link\b", exclude=r"admin|trap|port state")
        admin = _col(names, r"admin|enabled|^port state", exclude=r"physical|vlan")
        spd = _col(names, r"^speed|physical status|speed \(mb/s\)|^rate", r"^mode$", exclude=r"cfg|config|admin|physical mode")
        typ = _col(names, r"sfp ?type|transceiver|media|xfp", r"^type", exclude=r"port type|stp")
        desc = _col(names, r"^name$|descr|display string|^alias", exclude=r"vlan")
        vlan = _col(names, r"^vlan$|pvid|native vlan|^vlan\b")
        if link is None and admin is None:
            continue
        for cells, line in zip(rows, raw):
            name = cells[0].split()[0] if cells[0].split() else ""
            if not name or not re.search(r"\d", name) or is_virtual(name):
                continue
            lv = cells[link].strip() if link is not None and link < len(cells) else ""
            av = cells[admin].strip() if admin is not None and admin < len(cells) else ""
            tv = cells[typ].strip() if typ is not None and typ < len(cells) else ""
            if OFF.match(av) or LINK_OFF.match(lv) or re.search(r"(?i)err-?disabled", lv):
                s = "off" if not re.search(r"(?i)err", lv) else "down"
            elif NOT_PRESENT.search(lv) or NOT_PRESENT.search(tv):
                s = "empty"
            elif UP.match(lv) or (link is None and UP.match(av)):
                s = "up"
            else:
                s = "down"
            sfp = bool(SFP_NAME.match(name) or (tv and SFP_TYPE.search(tv) and not re.search(r"(?i)copper|base-?t\b|\dt$", tv)))
            if not sfp and model_n and re.match(r"^\d+/(\d+)$|^\d+/\d+/(\d+)$", name):
                idx = int(re.findall(r"\d+", name)[-1])
                sfp = idx > model_n  # «US-24»: puertos 25 y 26 son SFP
            vv = cells[vlan].strip() if vlan is not None and vlan < len(cells) else ""
            ports.append({"n": name, "k": "sfp" if sfp else "cu", "s": s,
                          "sp": speed(cells[spd]) if spd is not None and spd < len(cells) and s == "up" else "",
                          "d": cells[desc].strip() if desc is not None and desc < len(cells) and cells[desc].strip() not in ("--", "-") else "",
                          "net": "trunk" if vv.lower() in ("trunk", "trk", "t") else (f"VLAN {vv}" if vv.isdigit() else ""),
                          "mod": tv if sfp and s != "empty" and len(tv) > 2 and not re.match(r"(?i)^(unknown|fiber|n/?a|--)$", tv) else "",
                          "st": line})
        if ports:
            break
    return ports


# --------------------------------------------------------------------------- EdgeOS / VyOS / USG: «show interfaces»
_VYOS = re.compile(r"^(eth\d+)\s+(\S+)\s+([uDA])/([uD])\s*(.*)$")


def vyos(text: str) -> list[dict]:
    ports = []
    for l in text.splitlines():
        m = _VYOS.match(l.strip())
        if not m:
            continue
        name, ip, s_, l_, desc = m.groups()
        s = "off" if s_ == "A" else ("up" if l_ == "u" else "down")
        ports.append({"n": name, "k": "cu", "s": s, "sp": "", "d": desc.strip(), "net": "" if ip == "-" else ip,
                      "mod": "", "st": l.strip()})
    return ports


# --------------------------------------------------------------------------- FortiGate «get system interface physical»
def fortigate(text: str) -> list[dict]:
    ports, cur = [], None
    for l in text.splitlines():
        m = re.match(r"^\s*==\[(\S+)\]\s*$", l)
        if m:
            cur = {"n": m.group(1), "k": "sfp" if re.match(r"(?i)^(x\d|sfp)", m.group(1)) else "cu",
                   "s": "down", "sp": "", "d": "", "net": "", "mod": "", "st": ""}
            ports.append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^\s*(\w[\w-]*):\s*(.*?)\s*$", l)
        if not kv:
            continue
        k, v = kv.groups()
        if k == "status":
            cur["s"] = "up" if v.lower() == "up" else "down"
            cur["st"] = f"status: {v}"
        elif k == "speed":
            cur["sp"] = speed(v)
            if re.match(r"^\s*(10000|25000|40000|100000)", v) or re.match(r"(?i)^x\d", cur["n"]):
                cur["k"] = "sfp"
        elif k == "ip" and not v.startswith("0.0.0.0"):
            t = v.split()
            if len(t) == 2:
                ln = sum(bin(int(x)).count("1") for x in t[1].split(".") if x.isdigit())
                cur["net"] = f"{t[0]}/{ln}"
    for p in ports:
        if p["s"] != "up":
            p["sp"] = ""
    return [p for p in ports if not is_virtual(p["n"])]


# --------------------------------------------------------------------------- PAN-OS «show interface all»
def panos(text: str) -> list[dict]:
    ports = []
    for l in text.splitlines():
        m = re.match(r"^(ethernet\d+/\d+)\s+\d+\s+(\S+?)/(\S+?)/(\S+)", l.strip())
        if not m:
            continue
        name, sp, _dup, st = m.groups()
        s = "up" if st.startswith("up") else ("off" if "power-down" in st or "admin" in st else "down")
        ports.append({"n": name, "k": "sfp" if sp.startswith(("10000", "25000", "40000")) else "cu", "s": s,
                      "sp": speed(sp) if s == "up" else "", "d": "", "net": "", "mod": "", "st": l.strip()})
    return ports


# --------------------------------------------------------------------------- Check Point Gaia «show interfaces all»
def gaia(text: str) -> list[dict]:
    ports, cur = [], None
    for l in text.splitlines():
        m = re.match(r"^Interface\s+(\S+)", l)
        if m:
            cur = {"n": m.group(1), "k": "cu", "s": "down", "sp": "", "d": "", "net": "", "mod": "", "st": "", "_eth": False}
            ports.append(cur)
            continue
        if cur is None:
            continue
        t = l.strip().split(None, 1)
        if len(t) < 2:
            continue
        k, v = t
        if k == "type":
            cur["_eth"] = v.strip() == "ethernet"
        elif k == "state" and v.strip() == "off":
            cur["s"] = "off"
        elif k == "link-state" and cur["s"] != "off":
            cur["s"] = "up" if "up" in v else "down"
            cur["st"] = l.strip()
        elif k == "link-speed" and "N/A" not in v:
            cur["sp"] = speed(v)
        elif k == "comments":
            cur["d"] = v.strip()
        elif k == "ipv4-address" and "Not" not in v:
            cur["net"] = v.strip()
    out = []
    for p in ports:
        if p.pop("_eth") and not is_virtual(p["n"]):
            if p["s"] != "up":
                p["sp"] = ""
            out.append(p)
    return out


# --------------------------------------------------------------------------- Linux (y airOS, airFiber, UniFi AP…)
# solo las interfaces con «device» en /sys son placas físicas; flags 0x1 = administrativamente arriba
LINUX_CMD = ('for i in /sys/class/net/*; do [ -e "$i/device" ] || continue; n=${i##*/}; '
             'echo "@@ $n $(cat $i/operstate 2>/dev/null) $(cat $i/carrier 2>/dev/null) $(cat $i/speed 2>/dev/null) '
             '$(cat $i/flags 2>/dev/null)"; done')


def linux(text: str) -> list[dict]:
    ports = []
    for l in text.splitlines():
        t = l.split()
        if len(t) < 2 or t[0] != "@@" or "@@ $n" in l:
            continue
        name, rest = t[1], t[2:]
        if is_virtual(name):
            continue
        oper = rest[0] if rest else ""
        flags = next((x for x in rest if x.startswith("0x")), "")
        nums = [x for x in rest if re.match(r"^-?\d+$", x)]
        carrier = nums[0] if nums else ""
        sp = nums[1] if len(nums) > 1 else ""
        admin_up = bool(int(flags, 16) & 1) if flags else True
        s = "off" if not admin_up else ("up" if oper == "up" or carrier == "1" else "down")
        ports.append({"n": name, "k": "sfp" if re.match(r"(?i)^(sfp|xe|10g)", name) or sp in ("10000", "25000", "40000") else "cu",
                      "s": s, "sp": speed(sp) if s == "up" and sp and not sp.startswith("-") else "", "d": "", "net": "",
                      "mod": "", "st": f"{name}: operstate={oper or '?'} carrier={carrier or '?'}"})
    return sorted(ports, key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p["n"])])


# --------------------------------------------------------------------------- FreeBSD (pfSense, OPNsense): «ifconfig -a»
def bsd(text: str) -> list[dict]:
    ports, cur = [], None
    for l in text.splitlines():
        m = re.match(r"^([a-z]+\d+): flags=\w+<([^>]*)>", l)
        if m:
            cur = {"n": m.group(1), "k": "cu", "s": "down", "sp": "", "d": "", "net": "", "mod": "", "st": "",
                   "_up": "UP" in m.group(2).split(","), "_eth": False}
            ports.append(cur)
            continue
        if cur is None:
            continue
        s = l.strip()
        if s.startswith("description:"):
            cur["d"] = s.split(":", 1)[1].strip()
        elif s.startswith("inet ") and not cur["net"]:
            t = s.split()
            cur["net"] = t[1] + (f"/{bin(int(t[3], 16)).count('1')}" if len(t) > 3 and t[2] == "netmask" and t[3].startswith("0x") else "")
        elif s.startswith("media: Ethernet"):
            cur["_eth"] = True
            m = re.search(r"\((\d+)(G?)base-?(\w+)", s)
            if m:
                cur["sp"] = speed(m.group(1) + ("g" if m.group(2) else ""))
                if re.match(r"(?i)[SLCE]R|[SL]X|SFI|Twinax|CR", m.group(3)):
                    cur["k"] = "sfp"
        elif s.startswith("status:"):
            cur["s"] = "up" if "active" in s else "down"
            cur["st"] = s
    out = []
    for p in ports:
        up, eth = p.pop("_up"), p.pop("_eth")
        if not eth or is_virtual(p["n"]):
            continue
        if not up:
            p["s"] = "off"
        if p["s"] != "up":
            p["sp"] = ""
        out.append(p)
    return out
