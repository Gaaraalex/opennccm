"""Frente de una OLT ZTE ZXA10 (C300/C320, V2.1.0) leído de su configuración: tarjetas («add-card»), puertos PON con
cuántas ONU tienen y uplinks gei/xgei. Antes la ficha no mostraba ningún puerto. Sin red."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import ports as P  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CFG = """config-version 2.1
!
add-rack rackno 1 racktype IEC19
add-shelf rackno 1 shelfno 1 shelftype IEC_SHELF
add-card rackno 1 shelfno 1 slotno 3 GTGH
add-card rackno 1 shelfno 1 slotno 19 HUVQ
!
interface gpon-olt_1/3/2
  no shutdown
  linktrap disable
  onu 1 type ZTE-F660 sn ZTEG00000001
  onu 2 type ZTE-F660 sn ZTEG00000002
!
interface gpon-olt_1/3/1
  no shutdown
  onu 1 type ZTE-F600 sn ZTEG00000003
!
interface gpon-olt_1/3/3
  shutdown
!
interface gei_1/10/1
  no shutdown
  hybrid-attribute fiber
  speed 1000
!
interface gei_1/10/2
  no shutdown
!
interface xgei_1/19/1
  no shutdown
  speed 10000
!
interface gpon-onu_1/3/1:1
  name CLIENTE
  description zona
!
"""
ps, status = P.parse(None, "zte_olt", CFG, "C320")
names = [p["n"] for p in ps]
check("los puertos físicos, en orden de ranura y puerto (sin las ONU)",
      names == ["gpon-olt_1/3/1", "gpon-olt_1/3/2", "gpon-olt_1/3/3", "gei_1/10/1", "gei_1/10/2", "xgei_1/19/1"], names)
by = {p["n"]: p for p in ps}
check("cada PON dice cuántas ONU tiene", by["gpon-olt_1/3/1"]["d"] == "1 ONU" and by["gpon-olt_1/3/2"]["d"] == "2 ONU",
      (by["gpon-olt_1/3/1"]["d"], by["gpon-olt_1/3/2"]["d"]))
check("apagado y habilitado salen de la configuración", by["gpon-olt_1/3/3"]["s"] == "off" and by["gpon-olt_1/3/1"]["s"] == "on"
      and status is False)
check("la tarjeta de cada ranura", by["gpon-olt_1/3/1"]["card"] == "GTGH" and by["xgei_1/19/1"]["card"] == "HUVQ"
      and by["gei_1/10/1"]["card"] == "")
check("gei con «hybrid-attribute fiber» es fibra; sin eso, cobre",
      by["gei_1/10/1"]["k"] == "sfp" and by["gei_1/10/2"]["k"] == "cu" and by["xgei_1/19/1"]["k"] == "sfp")
check("velocidad del uplink", by["xgei_1/19/1"]["sp"] == "10G" and by["gei_1/10/1"]["sp"] == "1G")
check("nada de los datos de los clientes (nombre o descripción de la ONU)", not any("CLIENTE" in str(p) or "zona" in str(p) for p in ps))
lay = P.layout("C320", ps)
check("frente por ranuras, de a dos", lay.get("slots") is True and lay["rows"] == 2 and lay["sfp"] == "pairs", lay)
check("otro equipo sin «add-card» no se toma por OLT", P._zte_olt_config("interface gei_1/1/1\n no shutdown\n") == [])

# ---- estado de enlace: ONU en línea por PON y estado de los uplinks, leídos en la conexión del respaldo
ONU = """OnuIndex   Admin State  OMCC State  O7 State   Phase State  Channel
--------------------------------------------------------------------------
1/3/1:1    enable       enable      operation  working      1(GPON)
1/3/2:1    enable       disable     unknown    LOS          1(GPON)
1/3/2:2    enable       disable     unknown    DyingGasp    1(GPON)
ONU Number: 3/3
"""
IFS = {"show interface gei_1/10/1": "gei_1/10/1 is up, line protocol is up\n  Description is UPLINK-CLIENTE-X\n  Duplex full, Speed 1000Mbps\n",
       "show interface gei_1/10/2": "gei_1/10/2 is up, line protocol is down\n",
       "show interface xgei_1/19/1": "xgei_1/19/1 is administratively down, line protocol is down\n"}


class Conn:
    def __init__(self):
        self.sent = []

    def send_command(self, cmd, read_timeout=0):
        self.sent.append(cmd)
        if cmd == P.ZTE_ONU_CMD:
            return ONU
        return IFS.get(cmd, "%Error 20203: Invalid input\n")


c = Conn()
raw = P.read(c, "zte_olt", CFG)
check("pide el estado de las ONU y cada uplink de la configuración (no los PON uno por uno)",
      c.sent == [P.ZTE_ONU_CMD, "show interface gei_1/10/1", "show interface gei_1/10/2", "show interface xgei_1/19/1"], c.sent)
check("de las ONU solo guarda la cuenta por PON", "1/3/2 dyinggasp=1 los=1" in raw and "OMCC" not in raw, raw)
check("de los uplinks no guarda la descripción", "UPLINK-CLIENTE-X" not in raw, raw)
ps, status = P.parse(raw, "zte_olt", CFG, "C320")
by = {p["n"]: p for p in ps}
check("con lo leído hay estado de enlace", status is True)
check("PON con una ONU en línea: activo", by["gpon-olt_1/3/1"]["s"] == "up" and by["gpon-olt_1/3/1"]["d"] == "1 de 1 ONU en línea",
      by["gpon-olt_1/3/1"])
check("PON con sus ONU caídas: sin enlace, y por qué", by["gpon-olt_1/3/2"]["s"] == "down" and by["gpon-olt_1/3/2"]["d"] == "0 de 2 ONU en línea"
      and by["gpon-olt_1/3/2"]["st"] == "DyingGasp 1 · LOS 1", by["gpon-olt_1/3/2"])
check("PON apagado en la configuración sigue apagado", by["gpon-olt_1/3/3"]["s"] == "off")
check("uplink con enlace y su velocidad", by["gei_1/10/1"]["s"] == "up" and by["gei_1/10/1"]["sp"] == "1G", by["gei_1/10/1"])
check("uplink sin enlace", by["gei_1/10/2"]["s"] == "down" and by["gei_1/10/2"]["sp"] == "")
check("uplink apagado por el equipo", by["xgei_1/19/1"]["s"] == "off")
c2 = Conn()
c2.send_command = lambda cmd, read_timeout=0: "%Error 20203: Invalid input detected\n"
raw2 = P.read(c2, "zte_olt", CFG)
ps2, st2 = P.parse(raw2, "zte_olt", CFG, "C320")
check("si la OLT no tiene esos comandos queda como antes (de la configuración, sin estado)",
      raw2 is None and st2 is False and {p["n"]: p["s"] for p in ps2}["gpon-olt_1/3/1"] == "on")
check("el formato sin «O7 State» (otras versiones) también se lee",
      P.zte_onu_counts("1/2/1:1  enable  enable  working  1(GPON)\n1/2/1:2  enable  disable  OffLine  1(GPON)\n")
      == {"1/2/1": {"working": 1, "offline": 1}})

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
