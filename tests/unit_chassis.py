"""Parte de atrás: fuentes de alimentación y ventiladores con la salida de cada fabricante (app/chassis.py)."""
import sys

sys.path.insert(0, ".")
from app import chassis  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def rd(dt, cmd, text):
    r = chassis.parse(chassis.MARK + cmd + "\n" + text, dt)
    return [(x["n"], x["s"]) for x in r["psu"]], [(x["n"], x["s"]) for x in r["fan"]]


ROS7 = """Columns: NAME, VALUE, TYPE
#  NAME          VALUE  TYPE
0  fan-state     ok
1  fan1-speed    5700   RPM
2  fan2-speed    0      RPM
3  psu1-state    ok
4  psu2-state    fail
5  temperature   41     C
"""
check("MikroTik 7: PSU 2 sin corriente y ventilador 2 parado", rd("mikrotik_routeros", "/system health print", ROS7)
      == ([("PSU 1", "ok"), ("PSU 2", "fail")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("mikrotik_routeros", "", ROS7))
ROS6 = """          voltage: 24.1V
      temperature: 38C
       fan1-speed: 6690RPM
     psu1-voltage: 12.1V
     psu2-voltage: 0V
"""
check("MikroTik 6: fuente a 0 V = sin corriente", rd("mikrotik_routeros", "/system health print", ROS6)
      == ([("PSU 1", "ok"), ("PSU 2", "fail")], [("Fan 1", "ok")]), rd("mikrotik_routeros", "", ROS6))

C3750 = """FAN 1 is OK
FAN 2 is FAULTY
FAN PS-1 is OK
FAN PS-2 is NOT PRESENT
SYSTEM TEMPERATURE is OK
SW  PID                 Serial#     Status           Sys Pwr  PoE Pwr  Watts
--  ------------------  ----------  ---------------  -------  -------  -----
1A  C3KX-PWR-350WAC     DTN1234567  OK               Good     Good     350/0
1B  Not Present
"""
check("Cisco 3750-X: ventiladores y tabla de fuentes", rd("cisco_ios", "show environment all", C3750)
      == ([("PSU 1A", "ok"), ("PSU 1B", "absent")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("cisco_ios", "", C3750))

JUN = """Class Item                           Status     Measurement
Power PEM 0                            OK
      PEM 1                            Absent
Temp  PEM 0                            OK         40 degrees C / 104 degrees F
Fans  Fan Tray 0 Fan 1                 OK         Spinning at normal speed
      Fan Tray 0 Fan 2                 Failed
"""
check("Juniper: PEM y bandeja de ventiladores", rd("juniper_junos", "show chassis environment", JUN)
      == ([("PSU 0", "ok"), ("PSU 1", "absent")], [("Fan 0.1", "ok"), ("Fan 0.2", "fail")]), rd("juniper_junos", "", JUN))

HW = """Slot  PowerID  Online   Mode   State      Power(W)
--------------------------------------------------------
0     PWR1     Present  AC     Supply     150.00
0     PWR2     Present  AC     NotSupply  150.00
------------------------------------------------
Slot  FanID   Online    Status    Speed   Mode     Airflow
------------------------------------------------
0     1       Present   Normal    50%     Auto     Side-to-Back
0     2       Present   Abnormal  0%      Auto     Side-to-Back
"""
check("Huawei: PWR2 sin suministro, ventilador 2 anormal", rd("huawei", "display power", HW)[0]
      == [("PSU 1", "ok"), ("PSU 2", "fail")], rd("huawei", "", HW))

PC = """
 Power Supply Status:

  PS#   Model     Serial      State           AC/DC  + V        Wattage   Max
  ----- --------- ----------- --------------- ----------------- --------- ------
  1     J9739A    IN30G4D009  Powered         AC 120V/240V        25      1050
  2                           Not Present     -- ---------        0         0

Fan Information
  Num  | State        | Failures
-------+--------------+----------
Sys-1  | Fan OK       | 0
Sys-2  | Fan Failed   | 1
"""
check("ProCurve: fuente 2 ausente y ventilador Sys-2 fallando", rd("hp_procurve", "show system power-supply", PC)
      == ([("PSU 1", "ok"), ("PSU 2", "absent")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("hp_procurve", "", PC))

FP = """Temp (C)....................................... 39
Fans:
Unit     Fan Description           Type      Speed     Duty level  State
----     --- --------------        --------- --------- ----------- -------------
1        1   Fan-1                 Fixed     4294      50%         Operational
1        2   Fan-2                 Fixed     0         50%         Failed
Power Modules:
Unit     Power supply Description  Type      State
----     ---- ------------------   --------- -------------
1        1    PS-1                 Fixed     Operational
"""
check("EdgeSwitch / FastPath: Fan-1, Fan-2 y PS-1", rd("ubiquiti_edgeswitch", "show environment", FP)
      == ([("PSU 1", "ok")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("ubiquiti_edgeswitch", "", FP))

FG = """ 1 +3.3V            alarm=0  value=3.3013  threshold_status=0
 7 PS1 VIN          alarm=0  value=120  threshold_status=0
 8 PS2 VIN          alarm=1  value=0  threshold_status=0x1
11 FAN1             alarm=0  value=4500  threshold_status=0
12 FAN2             alarm=1  value=0  threshold_status=0x1
"""
check("FortiGate: alarmas de PS2 y FAN2", rd("fortinet", "execute sensor list", FG)
      == ([("PSU 1", "ok"), ("PSU 2", "fail")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("fortinet", "", FG))

EX = """PowerSupply 1 information:
 State:             Powered On
PowerSupply 2 information:
 State:             Empty
FanTray information:
 State:             Operational
 Fan-1:             Operational at 3500 RPM
 Fan-2:             Failed
"""
check("EXOS: estado en la línea siguiente", rd("extreme_exos", "show power", EX)
      == ([("PSU 1", "ok"), ("PSU 2", "absent")], [("Fan 1", "ok"), ("Fan 2", "fail")]), rd("extreme_exos", "", EX))

CX = """Fan information
---------------------------------------------------------------------------
Mbr/Fan       Product  Serial Number  Speed   Direction      Status  RPM
              Name
---------------------------------------------------------------------------
1/1           N/A      N/A            normal  left-to-right  ok      8200
1/2           N/A      N/A            normal  left-to-right  fault   0
          Product  Serial                PSU                       Wattage
Mbr/PSU   Number   Number                Status                    Maximum
--------------------------------------------------------------------------
1/1       JL085A   CN71GZK0BQ            OK                        250
1/2       N/A      N/A                   Absent                    0
"""
check("AOS-CX: tablas de ventiladores y fuentes", rd("aruba_aoscx", "show environment fan", CX)
      == ([("PSU 1/1", "ok"), ("PSU 1/2", "absent")], [("Fan 1/1", "ok"), ("Fan 1/2", "fail")]), rd("aruba_aoscx", "", CX))

check("órdenes por plataforma", chassis.commands("cisco_ios")[0] == "show environment all"
      and chassis.commands("huawei") == ["display power", "display fan"] and chassis.commands("linux") == [])
check("sin datos: vacío", chassis.parse(None, "cisco_ios") == {"psu": [], "fan": []})

print("TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
