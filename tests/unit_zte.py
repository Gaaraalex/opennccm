"""ZTE: OLT ZXA10 (C300/C320 y TITAN) y switches/routers ZXR10 con salidas reales publicadas."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import cmdref, vendorlogos  # noqa: E402
from app.drivers import get_profile, parse_inventory, telnet_supported  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


C320 = """GPON#show system-group

System Description: C320 Version V2.1.0 Software

Started before: 6 days, 6 hours, 24 minutes

System name:  GPON

GPON#show card

Rack Shelf Slot CfgType RealType Port  HardVer SoftVer         Status
-------------------------------------------------------------------------------
1    1     2    GTGH    GTGHG    16    V1.0.0  V2.1.0          INSERVICE
1    1     3    PRAM    PRAM     3     V1.0.0  V1.01           INSERVICE
1    1     4    SMXA    SMXA     3     V1.0.0  V2.1.0          INSERVICE
"""
inv = parse_inventory(C320, get_profile("zte_olt"))
check("OLT C320: modelo y versión de «show system-group»", inv["model"] == "C320" and inv["os_version"] == "V2.1.0", inv)
inv = parse_inventory(C320.replace("C320 Version V2.1.0", "C300 Version V2.0.1"), get_profile("zte_olt"))
check("OLT C300", inv["model"] == "C300" and inv["os_version"] == "V2.0.1", inv)
p = get_profile("zte_olt")
check("OLT: respaldo «show running-config», hasta 15 min y por SSH o Telnet", p.backup == "show running-config"
      and p.backup_timeout >= 600 and telnet_supported("zte_olt") and telnet_supported("zte_olt_titan"))
Z1 = """ZXR10 Router Operating System Software, ZTE Corporation
ZXR10 ROS Version V4.08.23
ZXR10_5952 Software, Version ZXR10 5900 V2.8.23.A.12, RELEASE SOFTWARE
System serial: 12345678"""
inv = parse_inventory(Z1, get_profile("zte_zxros"))
check("ZXR10 5952: versión, modelo y serie", inv == {"os_version": "V2.8.23.A.12", "model": "ZXR10_5952", "serial": "12345678"}, inv)
inv = parse_inventory("ZXR10 5960-56TM-HC Software, Version: V3.02.20.R1, Release software", get_profile("zte_zxros"))
check("ZXR10 5960", inv["model"] == "ZXR10 5960-56TM-HC" and inv["os_version"] == "V3.02.20.R1", inv)
cmds = [c for c, _d, _k in cmdref.TYPES["zte_olt"]]
check("comandos de la OLT en la consola (ONU, potencia, tarjetas)", "show card" in cmds and any("show gpon onu state" in c for c in cmds)
      and any("show pon power attenuation" in c for c in cmds))
check("logo de ZTE en los perfiles nuevos", vendorlogos.key("zte_olt") == "zte" and vendorlogos.key("zte_olt_titan") == "zte")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
