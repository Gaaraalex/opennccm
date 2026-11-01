"""Herramientas de la consola con parámetros (app/nettools.py): opciones permitidas, valores validados y nada de inyección."""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("NCM_DATA_DIR", tempfile.mkdtemp())
from app import nettools as nt  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def bad(fn, text):
    try:
        fn(text)
    except nt.BadArgs:
        return True
    return False


with mock.patch.object(nt, "win", lambda: False):
    a, limit = nt.ping_args(nt.ping("8.8.8.8 -t"))
    check("ping -t: continuo, sin -c y con tope de tiempo", "-c" not in a and a[-1] == "8.8.8.8" and limit == nt.MAX_CONTINUOUS, a)
    a, _l = nt.ping_args(nt.ping("-n 10 -l 1400 -f 10.0.0.1"))
    check("ping estilo Windows → Linux (-c 10 -s 1400 -M do)", a[:3] == ["ping", "-c", "10"] and "-s" in a and a[a.index("-s") + 1] == "1400"
          and a[a.index("-M") + 1] == "do", a)
    a, _l = nt.ping_args(nt.ping("10.0.0.1 -c 3 -i 0.2 --ttl 5 -W 1"))
    check("ping estilo Linux (-c, -i, --ttl → -t, -W)", a[a.index("-c") + 1] == "3" and a[a.index("-i") + 1] == "0.2"
          and a[a.index("-t") + 1] == "5" and a[a.index("-W") + 1] == "1", a)
    a, _l = nt.ping_args(nt.ping("10.0.0.1 -w 1500"))
    check("ping -w en milisegundos (Windows) → -W 2 s", a[a.index("-W") + 1] == "2", a)
    a, _f2, _l = nt.trace_args(nt.traceroute("8.8.8.8 -h 30 -q 3 -I"))
    check("traceroute -h 30 -q 3 -I", a[a.index("-m") + 1] == "30" and a[a.index("-q") + 1] == "3" and "-I" in a and a[-1] == "8.8.8.8", a)
    a, fb, _l = nt.trace_args(nt.traceroute("ejemplo.com -T -p 443 -w 2000"))
    check("traceroute TCP al 443 y -w 2000 ms → 2 s", "-T" in a and a[a.index("-p") + 1] == "443" and a[a.index("-w") + 1] == "2"
          and fb[0] == "tracepath", a)
with mock.patch.object(nt, "win", lambda: True):
    a, _l = nt.ping_args(nt.ping("8.8.8.8 -t -l 1000"))
    check("en un servidor Windows, ping -t sigue siendo continuo", a == ["ping", "-t", "-l", "1000", "8.8.8.8"], a)

c = nt.tcp("10.0.0.1:443 -c 3")
check("tcp host:puerto y -c", (c["target"], c["port"], c["count"]) == ("10.0.0.1", 443, 3), c)
c = nt.tcp("10.0.0.1 8291", "22")
check("tcp host puerto (manda sobre la casilla)", c["port"] == 8291, c)
check("tcp sin puerto usa la casilla", nt.tcp("10.0.0.1", "161")["port"] == 161)
d = nt.dns("ejemplo.com MX @1.1.1.1")
check("dns: tipo y servidor", (d["target"], d["type"], d["server"]) == ("ejemplo.com", "MX", "1.1.1.1"), d)
check("dns -x es inverso", nt.dns("-x 8.8.8.8")["reverse"] and nt.dns("-type=TXT ejemplo.com")["type"] == "TXT")
check("dns sin tipo ni servidor: lo resuelve el sistema", nt.dns_args(nt.dns("ejemplo.com")) is None)

for text in ("8.8.8.8; rm -rf /", "8.8.8.8 && reboot", "$(reboot)", "8.8.8.8 -f -i 0", "-c 5", "8.8.8.8 -n 0", "8.8.8.8 -n 99999",
             "8.8.8.8 -I 'eth0;ls'", "8.8.8.8 --flood", "8.8.8.8 -p ff", "8.8.8.8 1.1.1.1", "8.8.8.8 -i 0.01", "'unclosed"):
    check(f"ping rechaza «{text}»", bad(nt.ping, text))
for text in ("8.8.8.8 -m 500", "8.8.8.8 -g 1.1.1.1", "8.8.8.8 -z 1", "x|y"):
    check(f"traceroute rechaza «{text}»", bad(nt.traceroute, text))
check("dns rechaza tipos raros", bad(nt.dns, "ejemplo.com AXFR") and bad(nt.dns, "ejemplo.com @;ls"))
check("tcp rechaza puerto fuera de rango", bad(nt.tcp, "10.0.0.1 70000"))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
