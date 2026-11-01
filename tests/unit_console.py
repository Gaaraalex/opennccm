"""Reglas de la consola (qué consulta, qué cambia, qué corta el servicio, comandos prohibidos), secretos ocultos antes
de ir al asistente IA y recetas incluidas (variables y documentación)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("NCM_DATA_DIR", tempfile.mkdtemp())
from app import consolecore as cc  # noqa: E402
from app import recipes  # noqa: E402
from app.assistant import _segments, redact  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


check("familias de consola", [cc.os_of(t) for t in ("mikrotik_routeros", "cisco_ios", "cisco_xe", "huawei", "juniper_junos",
                                                     "extreme_exos", "ubiquiti_edgeswitch", "linux", "fortinet")]
      == ["ros", "ios", "ios", "vrp", "junos", "exos", "fastpath", "linux", "generic"])
CASES = [
    ("ros", "/interface print", "read"), ("ros", "/ip address print where interface=ether1", "read"),
    ("ros", '/log print where topics~"interface"', "read"), ("ros", "/export", "read"),
    ("ros", "/ip address add address=10.0.0.1/24 interface=ether2", "change"), ("ros", "/ip service disable telnet", "change"),
    ("ros", "/interface ethernet set [find default-name=ether5] disabled=yes", "change"),
    ("ros", "/system reboot", "outage"), ("ros", "/system reset-configuration no-defaults=yes", "outage"),
    ("ros", ":put [/system identity get name]", "read"), ("ros", ":foreach i in=[/interface find] do={/interface disable $i}", "change"),
    ("ios", "show ip interface brief", "read"), ("ios", "sh run | section ntp", "read"), ("ios", "ping 8.8.8.8", "read"),
    ("ios", "configure terminal", "change"), ("ios", "interface Gi0/1", "change"), ("ios", "no shutdown", "change"),
    ("ios", "reload", "outage"), ("ios", "write erase", "outage"), ("ios", "delete flash:config.text", "outage"),
    ("vrp", "display interface brief", "read"), ("vrp", "system-view", "change"), ("vrp", "reboot", "outage"),
    ("junos", "show interfaces terse", "read"), ("junos", "set cli screen-length 0", "read"),
    ("junos", "set interfaces ge-0/0/1 disable", "change"), ("junos", "request system reboot", "outage"),
    ("exos", "show ports no-refresh", "read"), ("exos", "disable telnet", "change"), ("exos", "unconfigure switch all", "outage"),
    ("generic", "show version", "read"), ("generic", "algo raro", "change"),
]
for os_key, c, want in CASES:
    check(f"{os_key}: «{c}» → {want}", cc.risk(os_key, c) == want, cc.risk(os_key, c))
check("comando prohibido por prefijo", cc.denied("reload in 5", "reload") == "reload")
check("comando prohibido por expresión regular", cc.denied("write   erase", "/write\\s+erase/") == "/write\\s+erase/")
check("lo permitido no se bloquea", cc.denied("show version", "reload\n/write\\s+erase/") is None)
check("paginado apagado con lo mismo que usa netmiko", cc.PAGING_OFF["ios"] == "terminal length 0" and
      cc.PAGING_OFF["vrp"] == "screen-length 0 temporary" and cc.PAGING_OFF["exos"] == "disable clipaging")
check("«--More--» se reconoce", cc.MORE.search("line\n --More-- ") and cc.MORE.search("  ---- More ----"))
check("prompt de configuración: Cisco y Huawei sí; el normal y RouterOS no",
      cc.in_config("ios", "R1(config-if)#") and cc.in_config("vrp", "[HUAWEI]") and not cc.in_config("ios", "R1#")
      and not cc.in_config("ros", "[admin@CACHENA] >") and not cc.in_config("vrp", "<HUAWEI>"))
prof = cc.client_profile("ros", lambda s: s)
check("frecuentes: los que cortan el servicio van marcados", ["/system reboot", True] in prof["frequent"])

cfg = """/snmp community
set [ find default=yes ] name=public
add name=privada addresses=10.0.0.0/8
/user
add name=soporte group=full password=Secreta123
/interface wireless security-profiles
set [ find default=yes ] wpa2-pre-shared-key=ClaveWifi
/ip ipsec peer
add address=1.2.3.4 secret=VpnSecreto
/ip address
add address=192.168.14.1/24 interface=ether2
snmp-server community MiComunidad RO
username admin privilege 15 secret 5 $1$abc$xyz
enable secret 5 $1$def$uvw
"""
r = redact(cfg)
for secret in ("public", "privada", "Secreta123", "ClaveWifi", "VpnSecreto", "MiComunidad", "$1$abc$xyz", "$1$def$uvw"):
    check(f"secreto oculto: {secret}", secret not in r, r)
check("lo que no es secreto se conserva", "192.168.14.1/24" in r and "name=soporte" in r and "addresses=10.0.0.0/8" in r, r)

segs = _segments("Mira:\n```\nshow ip int brief\n```\nY si hace falta:\n```cisco\nconfigure terminal\ninterface Gi0/1\n```\n```\nreload\n```", "ios")
check("respuesta del asistente: texto y comandos con su riesgo",
      [(s["k"], s.get("risk")) for s in segs] == [("text", None), ("cmd", "read"), ("text", None), ("cmd", "change"), ("cmd", "outage")], segs)

for r in recipes.BUILTIN:
    names = {v["n"] for v in r["vars"]}
    used = set()
    for s in r["steps"]:
        used |= set(__import__("re").findall(r"\{\{(\w+)\}\}", s["c"]))
    check(f"receta {r['key']}: sus variables existen", used <= names, used - names)
    check(f"receta {r['key']}: termina en «Comprobar»", r["steps"][-1].get("check") is True)
    check(f"receta {r['key']}: categoría válida", r["cat"] in recipes.CATEGORIES)
    if r["doc"]:
        check(f"receta {r['key']}: enlace a la documentación oficial", r["doc"].startswith("https://"))
from collections import Counter  # noqa: E402
n = Counter(r["os"] for r in recipes.BUILTIN)
check("al menos 10 recetas por Cisco, MikroTik, EXOS y VOSS", all(n[o] >= 10 for o in ("ios", "ros", "exos", "voss")), n)
check("claves de receta únicas", len({r["key"] for r in recipes.BUILTIN}) == len(recipes.BUILTIN))
filled = recipes.fill(recipes.BY_KEY["ros-vlan"], {"nombre": "vlan30", "vlan": "30"})
check("receta: se sustituyen las variables (y las vacías toman el valor por omisión)",
      filled[0]["c"] == "/interface vlan add name=vlan30 vlan-id=30 interface=ether2", filled[0]["c"])

from app.errors import short_error  # noqa: E402
for raw, want in [("TCP connection to device failed. Common causes of this problem are: 1. Incorrect hostname", "sin respuesta por SSH"),
                  ("Authentication to device failed.", "credencial rechazada"), ("BadHostKeyException: Host key for server", "cambió la clave SSH del equipo"),
                  ("El equipo entró en modo usuario («>») y pide contraseña para el modo privilegiado (enable)", "falta la contraseña enable"),
                  ("Pattern not detected: 'R1#' in output", "respaldo incompleto"), ("Read timed out", "tiempo agotado"), ("algo raro", "falló el respaldo")]:
    check(f"motivo corto: «{want}»", short_error(raw) == want, short_error(raw))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
