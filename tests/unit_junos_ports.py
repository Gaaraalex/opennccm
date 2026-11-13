"""Frente de un QFX5100-48S-6Q (13.2X51-D25.3): Junos lista primero los ge- y después los xe-, y solo los puertos con
módulo (Xcvr 0-19, 43-47 y 53; el 16 con un SFP-LX10 de 1 G sale como «ge-0/0/16»). El panel salía con el 16 primero y
como cobre, y saltaba del 19 al 43."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import ports as P  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


have = list(range(0, 16)) + list(range(17, 20)) + list(range(43, 48))
lines = ["Interface               Admin Link Proto    Local                 Remote", "gr-0/0/0                up    up",
         "ge-0/0/16               up    up", "ge-0/0/16.0             up    up   eth-switch"]
lines += [f"xe-0/0/{n:<17}up    {'down' if n == 46 else 'up'}" for n in have]
lines += ["et-0/0/53               up    up", "em0                     up    up",
          "vlan.10                 up    up   inet     10.10.0.1/24"]
TERSE = "\n".join(lines) + "\n"

ports, status = P.parse(P.MARK + "show interfaces terse\n" + TERSE, "juniper_junos", "", "qfx5100-48s-6q")
names = [p["n"] for p in ports]
num = [int(n.rsplit("/", 1)[1]) for n in names]
check("en el orden del frente: 0, 1, …, 15, 16, 17 …", num == sorted(num) and num[:18] == list(range(18)), names[:20])
check("el 16 (SFP de 1 G en una jaula SFP+) es fibra, no cobre",
      next(p for p in ports if p["n"] == "ge-0/0/16")["k"] == "sfp" and all(p["k"] == "sfp" for p in ports))
check("los 48 SFP+ y los 6 QSFP+ del modelo, sin saltos", num == list(range(54)), num)
empty = [p["n"] for p in ports if p["s"] == "empty"]
check("los que no tienen módulo (20…42, 48…52) salen «sin módulo»",
      empty == [f"xe-0/0/{n}" for n in range(20, 43)] + [f"et-0/0/{n}" for n in range(48, 53)], empty)
check("los que tienen módulo conservan su estado", next(p for p in ports if p["n"] == "xe-0/0/46")["s"] == "down"
      and next(p for p in ports if p["n"] == "et-0/0/53")["s"] == "up")

# EX de cobre (ge-0/0/0…47) con uplinks en el PIC 1: los ge siguen siendo cobre y no se inventan puertos
ex = "\n".join([f"ge-0/0/{n:<17}up    up" for n in (0, 1, 5)] + ["xe-0/1/0                up    up",
                                                               "xe-0/1/3                up    down"]) + "\n"
ports, _ = P.parse(P.MARK + "show interfaces terse\n" + ex, "juniper_junos", "", "ex4300-48t")
kinds = {p["n"]: p["k"] for p in ports}
check("EX: los ge del PIC 0 son cobre y no se rellenan", [n for n in kinds if n.startswith("ge-")] == ["ge-0/0/0", "ge-0/0/1", "ge-0/0/5"]
      and all(kinds[n] == "cu" for n in kinds if n.startswith("ge-")), kinds)
check("EX: el PIC de uplinks se completa (xe-0/1/0…3)", [n for n in kinds if n.startswith("xe-")]
      == ["xe-0/1/0", "xe-0/1/1", "xe-0/1/2", "xe-0/1/3"], kinds)

lay = P.layout("qfx5100-48s-6q", [])
check("QFX5100-48S: dos filas de a pares en bloques de 12", lay["rows"] == 2 and lay["sfp"] == "pairs" and lay["sgrp"] == 6, lay)
lay = P.layout("otro-switch-de-fibra", [{"n": f"xe-0/0/{n}", "k": "sfp"} for n in range(24)])
check("switch de jaulas sin modelo en el catálogo: también en dos filas", lay["rows"] == 2 and lay["sfp"] == "pairs", lay)
lay = P.layout("hEX", [{"n": f"ether{n}", "k": "cu"} for n in range(5)] + [{"n": "sfp1", "k": "sfp"}])
check("un equipo chico sigue en una fila", lay["rows"] == 1, lay)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
