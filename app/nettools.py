"""Herramientas de Operar › Consola con parámetros: «8.8.8.8 -t», «-c 100 -s 1400 10.0.0.1», «ejemplo.com MX @1.1.1.1»…

Lo que escribe el usuario NUNCA llega tal cual al sistema: se lee con una lista de opciones permitidas por herramienta
(las de Windows y las de Linux que se usan a diario), se validan los valores y se arma la orden del servidor. Así no se
puede inyectar nada (ni «; rm», ni opciones peligrosas como la inundación «-f» de Linux).

En las opciones que significan cosas distintas en Windows y en Linux manda la de Windows, que es la que la gente
escribe de memoria (ping -t continuo, -n cuántos, -l tamaño, -f no fragmentar, -w milisegundos); las de Linux sin
conflicto también valen (-c, -s, -W, -M do, --ttl).
"""
from __future__ import annotations

import math
import platform
import re
import shlex
import shutil

from .i18n import N_, _, _f

TARGET = re.compile(r"^(?!-)[A-Za-z0-9.:\-_]{1,253}$")
IFACE = re.compile(r"^[\w.:\-]{1,40}$")
DNS_TYPES = ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "SRV", "CAA", "ANY")
MAX_CONTINUOUS = 600  # segundos que dura como mucho un ping continuo (-t)


class BadArgs(ValueError):
    pass


def _num(v: str | None, lo: float, hi: float, opt: str, integer: bool = True) -> float:
    try:
        n = int(v) if integer else float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        raise BadArgs(_f("La opción {0} necesita un número.", opt)) from None
    if not lo <= n <= hi:
        raise BadArgs(_f("La opción {0} va de {1} a {2}.", opt, _fmt(lo), _fmt(hi)))
    return n


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text or "")
    except ValueError:
        raise BadArgs(_("Hay unas comillas sin cerrar.")) from None


def _split(text: str, spec: dict[str, bool], extra_pos: int = 0) -> tuple[list[str], dict[str, str | None]]:
    """Separa los argumentos sueltos (destino…) de las opciones. `spec`: opción → si lleva valor."""
    toks, pos, opts, i = _tokens(text), [], {}, 0
    while i < len(toks):
        t = toks[i]
        if t.startswith("-") and not re.match(r"^-\d", t):
            key = t.lower() if t.lower() in spec else t
            if key not in spec:
                raise BadArgs(_f("Opción no permitida: {0}. Use las de la ayuda de abajo.", t))
            if spec[key]:
                if i + 1 >= len(toks):
                    raise BadArgs(_f("La opción {0} necesita un valor.", t))
                opts[key] = toks[i + 1]
                i += 2
                continue
            opts[key] = None
        else:
            pos.append(t)
        i += 1
    if not pos:
        raise BadArgs(_("Escriba una IP o un nombre."))
    if len(pos) > 1 + extra_pos:
        raise BadArgs(_f("Sobra «{0}»: escriba un solo destino.", pos[1 + extra_pos]))
    if not TARGET.match(pos[0]):
        raise BadArgs(_("Escriba una IP o un nombre válido."))
    return pos, opts


def win() -> bool:
    return platform.system() == "Windows"


def _line_buffered(args: list[str]) -> list[str]:
    """En Linux, traceroute y compañía guardan la salida si no van a una terminal: stdbuf la suelta línea a línea."""
    return (["stdbuf", "-oL"] + args) if not win() and shutil.which("stdbuf") else args


# --------------------------------------------------------------------------- ping
PING = {"-t": False, "-n": True, "-c": True, "-l": True, "-s": True, "-i": True, "-w": True, "-W": True, "-f": False,
        "-M": True, "--ttl": True, "-4": False, "-6": False, "-I": True, "-D": False}


def ping(text: str) -> dict:
    (target, *_rest), o = _split(text, PING)
    p: dict = {"target": target, "count": 4, "continuous": "-t" in o}
    for k in ("-n", "-c"):
        if k in o:
            p["count"] = int(_num(o[k], 1, 10000, k))
            p["continuous"] = False
    for k in ("-l", "-s"):
        if k in o:
            p["size"] = int(_num(o[k], 0, 65500, k))
    if "-i" in o:
        p["interval"] = _num(o["-i"], 0.2, 60, "-i", integer=False)
    if "-w" in o:
        p["timeout"] = max(1, math.ceil(_num(o["-w"], 1, 60000, "-w") / 1000))  # Windows: milisegundos
    if "-W" in o:
        p["timeout"] = _num(o["-W"], 1, 60, "-W", integer=False)
    if "--ttl" in o:
        p["ttl"] = int(_num(o["--ttl"], 1, 255, "--ttl"))
    if "-f" in o or o.get("-M") == "do":
        p["df"] = True
    elif "-M" in o and o["-M"] not in ("do", "dont", "want"):
        raise BadArgs(_("La opción -M admite do, dont o want."))
    if "-I" in o:
        if not IFACE.match(o["-I"] or ""):
            raise BadArgs(_("Interfaz o dirección de origen no válida."))
        p["iface"] = o["-I"]
    p["ipv"] = "6" if "-6" in o else "4" if "-4" in o else ""
    p["ts"] = "-D" in o
    if p["continuous"]:
        p.pop("count")
    return p


def ping_args(p: dict) -> tuple[list[str], int]:
    if win():
        a = ["ping"] + (["-" + p["ipv"]] if p["ipv"] else []) + (["-t"] if p.get("continuous") else ["-n", str(p["count"])])
        if "size" in p:
            a += ["-l", str(p["size"])]
        if "timeout" in p:
            a += ["-w", str(int(p["timeout"] * 1000))]
        if "ttl" in p:
            a += ["-i", str(p["ttl"])]
        if p.get("df"):
            a += ["-f"]
        if "iface" in p:
            a += ["-S", p["iface"]]
    else:
        a = ["ping"] + (["-" + p["ipv"]] if p["ipv"] else []) + ([] if p.get("continuous") else ["-c", str(p["count"])])
        a += ["-W", _fmt(p.get("timeout", 2))]
        if "size" in p:
            a += ["-s", str(p["size"])]
        if "interval" in p:
            a += ["-i", _fmt(p["interval"])]
        if "ttl" in p:
            a += ["-t", str(p["ttl"])]
        if p.get("df"):
            a += ["-M", "do"]
        if "iface" in p:
            a += ["-I", p["iface"]]
        if p.get("ts"):
            a += ["-D"]
    a.append(p["target"])
    limit = MAX_CONTINUOUS if p.get("continuous") else min(MAX_CONTINUOUS, int(p["count"] * (p.get("interval", 1) + 0.2) + p.get("timeout", 2) + 10))
    return a, limit


# --------------------------------------------------------------------------- traceroute
TRACE = {"-m": True, "-h": True, "-q": True, "-w": True, "-f": True, "-p": True, "-I": False, "-T": False, "-U": False,
         "-4": False, "-6": False, "-n": False, "-d": False, "-r": False}


def traceroute(text: str) -> dict:
    (target, *_rest), o = _split(text, TRACE)
    t: dict = {"target": target, "max": 20, "queries": 1, "wait": 1.0, "names": "-r" in o}
    for k in ("-m", "-h"):
        if k in o:
            t["max"] = int(_num(o[k], 1, 64, k))
    if "-q" in o:
        t["queries"] = int(_num(o["-q"], 1, 5, "-q"))
    if "-w" in o:
        w = _num(o["-w"], 0.1, 60000, "-w", integer=False)
        t["wait"] = min(10.0, w / 1000 if w > 10 else w)  # Windows lo da en milisegundos; Linux en segundos
    if "-f" in o:
        t["first"] = int(_num(o["-f"], 1, 64, "-f"))
    if "-p" in o:
        t["port"] = int(_num(o["-p"], 1, 65535, "-p"))
    t["method"] = "icmp" if "-I" in o else "tcp" if "-T" in o else "udp"
    t["ipv"] = "6" if "-6" in o else "4" if "-4" in o else ""
    return t


def trace_args(t: dict) -> tuple[list[str], list[str], int]:
    """La orden, la de reserva (tracepath, si no está traceroute) y el tiempo máximo."""
    limit = min(300, int(t["max"] * t["queries"] * t["wait"] + 20))
    if win():
        a = ["tracert"] + (["-" + t["ipv"]] if t["ipv"] else []) + ([] if t["names"] else ["-d"])
        return a + ["-h", str(t["max"]), "-w", str(int(t["wait"] * 1000)), t["target"]], [], limit
    a = ["traceroute"] + (["-" + t["ipv"]] if t["ipv"] else []) + ([] if t["names"] else ["-n"])
    a += {"icmp": ["-I"], "tcp": ["-T"], "udp": []}[t["method"]]
    a += ["-w", _fmt(t["wait"]), "-q", str(t["queries"]), "-m", str(t["max"]), "-N", "4"]
    if "first" in t:
        a += ["-f", str(t["first"])]
    if "port" in t:
        a += ["-p", str(t["port"])]
    fb = ["tracepath"] + (["-n"] if not t["names"] else []) + ["-m", str(t["max"])] + (["-p", str(t["port"])] if "port" in t else [])
    return a + [t["target"]], fb + [t["target"]], limit


# --------------------------------------------------------------------------- puerto TCP
TCP = {"-p": True, "-c": True, "-n": True, "-w": True, "-t": False}


def tcp(text: str, port_field: str | None = None) -> dict:
    text = re.sub(r"^(\S+?):(\d{1,5})(?=\s|$)", r"\1 \2", (text or "").strip())  # «host:443»
    pos, o = _split(text, TCP, extra_pos=1)
    port = pos[1] if len(pos) > 1 else o.get("-p") or port_field or "22"
    c: dict = {"target": pos[0], "port": int(_num(port, 1, 65535, _("puerto"))), "count": 1, "timeout": 5.0,
               "continuous": "-t" in o}
    for k in ("-c", "-n"):
        if k in o:
            c["count"] = int(_num(o[k], 1, 100, k))
    if "-w" in o:
        w = _num(o["-w"], 0.1, 60000, "-w", integer=False)
        c["timeout"] = min(10.0, w / 1000 if w > 10 else w)
    return c


# --------------------------------------------------------------------------- DNS
DNS = {"-x": False, "-t": True, "-type": True, "--type": True}


def dns(text: str) -> dict:
    toks = _tokens(text)
    server = next((t[1:] for t in toks if t.startswith("@")), "")
    toks = [t for t in toks if not t.startswith("@")]
    if server and not TARGET.match(server):
        raise BadArgs(_("Servidor DNS no válido."))
    rtype = ""
    rest = []
    for t in toks:  # «nombre MX» o «-type=MX»
        m = re.match(r"^-{1,2}(?:type|t)=(\w+)$", t, re.I)
        if m:
            rtype = m.group(1)
        elif t.upper() in DNS_TYPES and rest:
            rtype = t
        else:
            rest.append(t)
    pos, o = _split(shlex.join(rest), DNS)
    rtype = (o.get("-t") or o.get("-type") or o.get("--type") or rtype or "").upper()
    if rtype and rtype not in DNS_TYPES:
        raise BadArgs(_f("Tipo de registro no admitido: {0}. Use {1}.", rtype, ", ".join(DNS_TYPES)))
    return {"target": pos[0], "type": "PTR" if "-x" in o else rtype, "server": server, "reverse": "-x" in o}


def dns_args(d: dict) -> list[str] | None:
    """Con «dig» (o «nslookup») si hay tipo o servidor; None para resolver con el sistema."""
    if not d["type"] and not d["server"]:
        return None
    if shutil.which("dig"):
        a = ["dig", "+noall", "+answer", "+comments", "+stats", "+time=3", "+tries=1"]
        a += (["@" + d["server"]] if d["server"] else [])
        return a + (["-x", d["target"]] if d["reverse"] else [d["target"], d["type"] or "A"])
    if shutil.which("nslookup"):
        return ["nslookup", "-type=" + (d["type"] or "A"), d["target"]] + ([d["server"]] if d["server"] else [])
    return []


# botones de parámetros de Herramientas (consola): (lo que se agrega después de la IP o el nombre, qué hace). Tocar uno lo
# agrega; tocarlo otra vez lo quita. El valor de ejemplo queda elegido para cambiarlo escribiendo encima.
PARAMS = {
    "ping": [("-t", N_("continuo, hasta Detener")), ("-n 10", N_("cuántos paquetes")), ("-l 1400", N_("tamaño en bytes")),
             ("-f", N_("sin fragmentar (prueba de MTU)")), ("-w 1000", N_("espera de cada respuesta, en ms")),
             ("-i 0.2", N_("intervalo entre paquetes, en s")), ("--ttl 5", N_("TTL")),
             ("-I ether1", N_("interfaz o dirección de origen")), ("-4", N_("solo IPv4")), ("-6", N_("solo IPv6"))],
    "traceroute": [("-h 30", N_("saltos como máximo")), ("-q 3", N_("consultas por salto")), ("-w 2", N_("espera por salto, en s")),
                   ("-I", N_("con ICMP")), ("-T", N_("con TCP")), ("-p 443", N_("puerto (con -T)")), ("-f 3", N_("primer salto")),
                   ("-r", N_("con nombres de los saltos")), ("-4", N_("solo IPv4")), ("-6", N_("solo IPv6"))],
    "tcp": [("-c 5", N_("cuántos intentos")), ("-t", N_("continuo, hasta Detener")),
            ("-w 2", N_("espera de cada intento, en s"))],
    "dns": [("A", N_("dirección IPv4")), ("AAAA", N_("dirección IPv6")), ("MX", N_("servidores de correo")), ("TXT", N_("textos (SPF…)")),
            ("NS", N_("servidores de nombres")), ("CNAME", N_("alias")), ("@1.1.1.1", N_("preguntar a este servidor DNS")),
            ("-x", N_("inversa: el nombre de una IP"))],
}
