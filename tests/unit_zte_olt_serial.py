"""Serie de una OLT ZTE ZXA10 C320 (V2.1.0): no está en «show system-group» ni en «show card»; está en el detalle de
la tarjeta de control («show card slotno 4» → «Serial-Number : …»). El inventario la busca en «show card». Sin red."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import drivers as D  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


GROUP = """System Description: C320 Version V2.1.0 Software, Copyright (c) by ZTE Corporation Compiled
System ObjectId: .1.3.6.1.4.1.3902.1082.1001.320.2.1
System name:  ZXAN
"""
CARD = """Rack Shelf Slot CfgType RealType Port  HardVer SoftVer         Status
-------------------------------------------------------------------------------
1    1     2    GTGH    GTGHG    16    V1.0.0  V2.1.0          INSERVICE
1    1     3    PRAM    PRAM     3     V1.0.0  V1.01           INSERVICE
1    1     4    SMXA    SMXA     3     V1.0.0  V2.1.0          INSERVICE
"""
SLOT = """Config-Type   : SMXA      Status        : INSERVICE
Port-Number   : 3
Real-Type     : SMXA      Serial-Number : 713547900809
Phy-Mem-Size  : 2048MB    Main-CPU      : PowerPC Processor
BootROM-VER   : V4.0.12    2016-12-06 17:06:07
Software-VER  : V2.1.0    2017-01-17 01:04:45
"""
p = D.get_profile("zte_olt")
first = GROUP + "\n\n" + CARD
check("pide el detalle de la tarjeta de control (ranura 4 en esta C320)", D.inventory_followup(p, first) == "show card slotno 4",
      D.inventory_followup(p, first))
c300 = CARD.replace("    4    SMXA    SMXA ", "    10   SCXN    SCXN ")
check("en una C300 la control va en la 10", D.inventory_followup(p, c300) == "show card slotno 10", D.inventory_followup(p, c300))
inv = D.parse_inventory(first + "\n\n" + SLOT, p)
check("serie, modelo y versión", inv == {"serial": "713547900809", "model": "C320", "os_version": "V2.1.0"}, inv)
check("sin el detalle no inventa una serie", D.parse_inventory(first, p)["serial"] is None, D.parse_inventory(first, p))
check("los demás perfiles no piden nada más", D.inventory_followup(D.get_profile("cisco_ios"), first) is None)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
