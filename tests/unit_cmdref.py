"""Comandos de la consola y del asistente según el equipo (app/cmdref.py): todos los tipos que admite OpenNCCM tienen
comandos suyos, cambian con el modelo y la versión de firmware, y sus consultas pasan el modo solo lectura. Sin red."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import cmdref  # noqa: E402
from app import consolecore as cc  # noqa: E402
from app.drivers import HTTP_ONLY, PROFILES  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def cmds(t, model="", version=""):
    return [c for c, _d, _k in cmdref.entries(t, model, version)]


GENERIC = [c for c, _d in cc.SUGGEST["generic"]]
none, mixed, blocked, unguarded = [], [], [], []
for p in PROFILES:
    if p.key in HTTP_ONLY:  # SwitchOS: sin SSH, no tiene consola
        continue
    e = cmdref.entries(p.key)
    if p.key != "generic" and cmds(p.key) == GENERIC:
        none.append(p.key)
    if p.key != "generic" and cmdref._usable(p.backup) and p.backup not in cmds(p.key):  # los guiones largos no
        mixed.append(p.key)
    os_key = cc.os_of(p.key)
    blocked += [(p.key, c) for c, _d, k in e if k == "r" and cc.is_change(os_key, c, p.key)]
    unguarded += [(p.key, c) for c, _d, k in e if k == "x" and not cc.is_danger(os_key, c, p.key)]
check(f"los {len(PROFILES) - len(HTTP_ONLY) - 1} tipos con consola tienen comandos suyos (no la lista genérica)", not none, none)
check("cada tipo incluye el comando con que OpenNCCM lo respalda", not mixed, mixed)
check("las consultas sugeridas pasan el modo solo lectura de su equipo", not blocked, blocked[:10])
check("los comandos que cortan el servicio piden confirmación en su equipo", not unguarded, unguarded[:10])

wg = cmds("watchguard_fireware", "T40", "12.8.1.B663143")
check("WatchGuard: comandos de Fireware (Fireware CLI Reference v12.9.4), no de Cisco",
      {"show interface", "show ip route", "show feature-key", "show sysinfo", "show cluster status",
       "show vpn-status bovpn gateway", "export config to console"} <= set(wg)
      and "show running-config" not in wg and "show ip interface brief" not in wg, wg)
check("WatchGuard 12.4 o más nueva: SD-WAN; un modelo con Wi-Fi (T40-W): «show wireless»",
      "show sd-wan" in wg and "show wireless" not in wg and "show wireless" in cmds("watchguard_fireware", "T40-W", "12.8")
      and "show sd-wan" not in cmds("watchguard_fireware", "T40", "12.3.1"))
check("FortiGate: comandos de FortiOS y SD-WAN según la versión",
      "get system status" in cmds("fortinet") and "show system sdwan" in cmds("fortinet", "FG-60F", "7.2.8")
      and "show system virtual-wan-link" in cmds("fortinet", "FG-60E", "6.2.15"))
check("FortiGate: «execute reboot» pide confirmación y «get …» pasa el modo solo lectura",
      cc.is_danger("generic", "execute reboot", "fortinet") and not cc.is_change("generic", "get system status", "fortinet"))
ros7, ros6 = cmds("mikrotik_routeros", "hAP ax3", "7.16.2"), cmds("mikrotik_routeros", "RB2011UiAS-2HnD", "6.49.10")
check("RouterOS 7 con Wi-Fi: «/interface wifi»; RouterOS 6: «/interface wireless» y BGP «peer»",
      "/interface wifi print" in ros7 and "/routing bgp session print" in ros7
      and "/routing bgp peer print" in ros6 and "/interface wireless registration-table print" in ros6, (ros7[:6], ros6[:6]))
rb5009 = cmds("mikrotik_routeros", "RB5009UG+S+", "7.16")
check("RouterOS sin radio no sugiere interfaces inalámbricas; un CHR no sugiere «routerboard»",
      "/interface wireless print" not in rb5009 and "/system routerboard print" not in cmds("mikrotik_routeros", "CHR", "7.16"))
check("RouterOS con PoE (CRS328-24P): «/interface ethernet poe print»",
      "/interface ethernet poe print" in cmds("mikrotik_routeros", "CRS328-24P-4S+", "7.16") and "/interface ethernet poe print" not in rb5009)
c9k, isr = cmds("cisco_xe", "C9300-48P", "17.9.4"), cmds("cisco_xe", "ISR4331/K9", "17.6.5")
check("Catalyst 9300-48P: PoE, stack e IOS-XE", {"show power inline", "show switch", "show install summary"} <= set(c9k))
check("router ISR: NAT y sin comandos de switch", "show ip nat translations" in isr and "show vlan brief" not in isr
      and "show power inline" not in isr)
srx = cmds("juniper_junos", "srx345", "21.4R3")
check("Junos SRX: políticas y sesiones de seguridad; EX: Virtual Chassis",
      "show security policies" in srx and "show virtual-chassis" in cmds("juniper_junos", "ex4300-48p", "21.4"))
check("VyOS 1.4: «show bgp summary»; 1.3: «show ip bgp summary»",
      "show bgp summary" in cmds("vyos", "", "1.4.0") and "show ip bgp summary" in cmds("vyos", "", "1.3.6"))
check("Brocade FOS: consultas sin «show» (porterrshow) pasan el modo solo lectura",
      "porterrshow" in cmds("brocade_fos") and not cc.is_change("generic", "porterrshow", "brocade_fos")
      and cc.is_change("generic", "porterrshow", None))
check("pfSense: comandos de su shell (pfctl, netstat), no de Cisco",
      "pfctl -sr" in cmds("pfsense") and "show version" not in cmds("pfsense"))
check("un comando desconocido sigue contando como cambio en solo lectura", cc.is_change("generic", "set system foo", "fortinet"))

dev = SimpleNamespace(device_type="watchguard_fireware", model="T40", os_version="12.8.1")
prof = cc.client_profile(cc.os_of(dev.device_type), lambda s: s, dev)
check("la consola recibe los comandos de ese equipo, su nombre, modelo y versión, y el historial por tipo",
      prof["key"] == "watchguard_fireware" and prof["label"] == "WatchGuard Fireware T40 12.8.1"
      and prof["suggest"][0][0] == "show sd-wan" and any(c == "show feature-key" for c, _d in prof["suggest"]), prof["label"])
check("frecuentes: consultas del equipo y su reinicio al final, marcado como peligroso",
      len(prof["frequent"]) <= cmdref.FREQ and prof["frequent"][-1] == ["reboot", True]
      and all(not d for _c, d in prof["frequent"][:-1]), prof["frequent"])
ref = cmdref.reference("watchguard_fireware", "T40", "12.8.1")
check("referencia para el asistente IA: un comando por línea con su riesgo",
      "show ip route  — tabla de rutas (consulta)" in ref and "reboot  — reiniciar el equipo (corta el servicio)" in ref, ref[:200])

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
