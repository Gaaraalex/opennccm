"""Parte de atrás del equipo: fuentes de alimentación y ventiladores, leídos en la conexión del respaldo.

Cada fabricante los da con su orden: MikroTik «/system health print» (psu1-state ok/fail, fan1-speed en RPM), Cisco
«show environment all» («FAN 1 is OK», tabla de fuentes), Juniper «show chassis environment», Huawei «display power» y
«display fan», HPE ProCurve «show system fans/power-supply», Aruba AOS-CX «show environment fan/power-supply», Arista
«show environment cooling/power», FastPath (EdgeSwitch, UniFi) «show environment», Extreme «show fans/power», FortiGate
«execute sensor list». Las salidas son muy distintas, así que se leen línea a línea: el nombre de la pieza (PSU 1, PS-2,
PWR1, PEM 0, Power Supply 1, Fan 1, Fan Tray 0 Fan 1…) y la palabra de estado de esa misma línea; en las tablas sin el
nombre en cada fila, el número de la fila bajo una cabecera de fuentes o de ventiladores.

Resultado: {"psu": [{"n", "s", "st"}], "fan": [{"n", "s", "st", "rpm"?}]} con s = "ok" | "fail" | "absent".
"""
import re

from .drivers import FASTPATH, HUAWEI, IOS, get_profile

MARK = "### chasis: "

COMMANDS = {
    "mikrotik_routeros": ["/system health print without-paging"],
    "juniper_junos": ["show chassis environment"], "juniper": ["show chassis environment"],
    "cisco_nxos": ["show environment"],
    "hp_procurve": ["show system fans", "show system power-supply"],
    "aruba_osswitch": ["show system fans", "show system power-supply"],
    "aruba_aoscx": ["show environment fan", "show environment power-supply"],
    "arista_eos": ["show environment cooling", "show environment power"],
    "extreme_exos": ["show fans", "show power"],
    "fortinet": ["execute sensor list"],
    "ubiquiti_unifiswitch": ["show environment"],
}


def commands(device_type: str) -> list[str]:
    if device_type in COMMANDS:
        return COMMANDS[device_type]
    fam = get_profile(device_type).family
    if fam == HUAWEI:
        return ["display power", "display fan"]
    if fam == FASTPATH:
        return ["show environment"]
    if fam == IOS:
        return ["show environment all", "show environment"]
    return []


def read(conn, device_type: str) -> str | None:
    """Salidas de las órdenes de fuentes y ventiladores. Nunca hace fallar el respaldo."""
    out = []
    for c in commands(device_type):
        try:
            text = conn.send_command(c, read_timeout=30)
        except Exception:  # noqa: BLE001
            continue
        out.append(MARK + c + "\n" + text)
        # «show environment all» no existe en todos los IOS: si responde, no hace falta «show environment»
        if c == "show environment all" and any(parse(MARK + c + "\n" + text, device_type).values()):
            break
    return "\n".join(out) or None


ABSENT = re.compile(r"(?i)not ?present|absent|notpresent|\bempty\b|missing|not installed|uninstalled")
FAIL = re.compile(r"(?i)fail|faulty|fault\b|not ?supply|no ?supply|\bbad\b|error|alarm=1|no input|not ?powered|nopower|"
                  r"no power|powered ?off|\bcheck\b|critical|abnormal|\bdown\b|\boff\b|\bwarning\b|not ok|stopped|stall")
OK = re.compile(r"(?i)\bok\b|good|normal|operational|powered|\bpresent\b|\bsupply\b|spinning|running|\bon\b|alarm=0|\bup\b|"
                r"active|online|yes")
PSU = re.compile(r"(?i)\b(?:psu|ps|pwr|pem|power\s?supply|powersupply|power module)\s?[-#]?\s?(\d+(?:/\d+)?[ab]?)\b")
FAN = re.compile(r"(?i)\b(fan\s?tray\s?\d+(?:\s?fan\s?\d+)?|fan\s?[-#]?\s?\d+(?:/\d+)?|sys-\d+|fan\d+)")


def _state(line: str) -> str | None:
    if ABSENT.search(line):
        return "absent"
    if FAIL.search(line):
        return "fail"
    if OK.search(line):
        return "ok"
    return None


def _norm(kind: str, raw: str) -> str:
    n = re.sub(r"\s+", " ", raw.strip())
    if kind == "psu":
        m = PSU.match(n)
        return "PSU " + m.group(1).upper() if m else n
    m = re.match(r"(?i)^fan\s?tray\s?(\d+)(?:\s?fan\s?(\d+))?$", n)
    if m:
        return f"Fan {m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    m = re.search(r"(\d+(?:/\d+)?)", n)
    return f"Fan {m.group(1)}" if m else n


def _mikrotik(text: str) -> dict:
    """RouterOS 7: «  0 psu1-state  ok» (columnas NAME VALUE TYPE); RouterOS 6: «psu1-state: ok», «fan1-speed: 6690RPM»."""
    psu, fan, volt, fstate = {}, {}, {}, None
    for l in text.splitlines():
        m = re.search(r"\b(psu\d+)-state\W+(\w+)", l)
        if m:
            psu[m.group(1)] = {"n": "PSU " + m.group(1)[3:], "s": "ok" if m.group(2) == "ok" else "fail", "st": re.sub(r"^\d+\s+", "", l.strip())}
        m = re.search(r"\b(psu\d+)-voltage\W+([\d.]+)", l)
        if m:
            volt[m.group(1)] = (float(m.group(2)), l.strip())
        m = re.search(r"\b(fan\d+)-speed\W+(\d+)", l)
        if m:
            rpm = int(m.group(2))
            fan[m.group(1)] = {"n": "Fan " + m.group(1)[3:], "s": "ok" if rpm > 0 else "fail", "st": re.sub(r"^\d+\s+", "", l.strip()), "rpm": rpm}
        m = re.search(r"\bfan-state\W+(\w+)", l)
        if m:
            fstate = {"n": "Fan", "s": "ok" if m.group(1) == "ok" else "fail", "st": re.sub(r"^\d+\s+", "", l.strip())}
    if not fan and fstate:  # sin velocidades: solo el estado general de los ventiladores
        fan["fan"] = fstate
    for k, (v, l) in volt.items():  # sin «psuN-state»: una fuente a 0 V está sin corriente
        psu.setdefault(k, {"n": "PSU " + k[3:], "s": "ok" if v > 1 else "fail", "st": l})
    return {"psu": sorted(psu.values(), key=lambda x: x["n"]), "fan": sorted(fan.values(), key=lambda x: x["n"])}


def _generic(text: str) -> dict:
    psu: dict[str, dict] = {}
    fan: dict[str, dict] = {}
    table = None    # tabla sin el nombre de la pieza en cada fila («SW PID Status», «Fan Tray Status», «Mbr/PSU»)
    slot = False    # su primera columna es el slot o la unidad: el número de la pieza es la segunda (Huawei, FastPath)
    pending = None  # pieza nombrada en una línea y con su estado en la siguiente (EXOS «PowerSupply 1 information:»)

    def put(kind: str, n: str, st: str, line: str, rpm: int | None = None):
        d = psu if kind == "psu" else fan
        item = {"n": n, "s": st, "st": line}
        if rpm is not None:
            item["rpm"] = rpm
        if n not in d or d[n]["s"] == "ok":  # si una línea dice que falla, prevalece
            d[n] = item

    for l in text.splitlines():
        s = l.strip()
        if not s or re.match(r"^[-=\s]+$", s):
            continue
        if re.search(r"(?i)\bfan ps-?\d", s):
            continue  # ventilador de la propia fuente: ya cuenta en la fuente
        st = _state(s)
        rpm = re.search(r"(\d{3,5})\s*rpm", s, re.I)
        first = s.split()[0]
        # cabeceras de tabla: nombran columnas, no piezas
        if re.search(r"(?i)status|state", s) and not re.search(r"\d", first):
            if re.search(r"(?i)^sw\s+pid|power ?supply|^ps#|mbr/psu|power module|^slot\s+powerid|^pwr", s) and not PSU.search(s):
                table, pending, slot = "psu", None, bool(re.match(r"(?i)^(slot|unit)\b", s))
                continue
            if re.search(r"(?i)^fan|mbr/fan|fan ?tray|fanid|\bfan\b", s) and not FAN.search(s):
                table, pending, slot = "fan", None, bool(re.match(r"(?i)^(slot|unit)\b", s))
                continue
        if re.search(r"(?i)^(power modules|power supplies|fans):?$", s):
            table = "psu" if s.lower().startswith("power") else "fan"
            continue
        m = re.match(r"^(\d+(?:/\d+)?[AB]?)\s+(\d+)?", s)
        named = (PSU if table == "psu" else FAN).search(s) if table else None
        if table and m and (st or rpm):
            if named:  # la fila nombra la pieza («PWR1», «Fan-2», «PS-1»)
                n = _norm(table, named.group(0))
            else:
                n = ("PSU " if table == "psu" else "Fan ") + (m.group(2) if slot and m.group(2) else m.group(1))
            put(table, n, st or ("ok" if int(rpm.group(1)) > 0 else "fail"), s, int(rpm.group(1)) if rpm else None)
            continue
        mp, mf = PSU.search(s), FAN.search(s)
        if mp and not mf:
            if re.match(r"(?i)^temp", s):
                continue
            if st:
                put("psu", _norm("psu", mp.group(0)), st, s)
            else:
                pending = ("psu", _norm("psu", mp.group(0)))
            continue
        if mf:
            if not st and rpm:
                st = "ok" if int(rpm.group(1)) > 0 else "fail"
            if st:
                put("fan", _norm("fan", mf.group(0)), st, s, int(rpm.group(1)) if rpm else None)
            else:
                pending = ("fan", _norm("fan", mf.group(0)))
            continue
        if pending and st and re.match(r"(?i)^(state|status|operational status)\b", s):
            put(pending[0], pending[1], st, s)
            pending = None
            continue
        if re.match(r"(?i)^fans?\b.*\bis\b", s) and st:  # «FAN is OK» (un solo ventilador)
            put("fan", "Fan", st, s)
    key = lambda x: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", x["n"])]  # noqa: E731
    return {"psu": sorted(psu.values(), key=key), "fan": sorted(fan.values(), key=key)}


def parse(raw: str | None, device_type: str) -> dict:
    if not raw:
        return {"psu": [], "fan": []}
    if device_type == "mikrotik_routeros":
        return _mikrotik(raw)
    return _generic(raw.replace(MARK, "\n"))
