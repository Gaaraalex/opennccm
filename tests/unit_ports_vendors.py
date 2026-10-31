"""Puertos de la ficha en todas las plataformas: salidas de estado con el formato de cada fabricante (app/porttable.py)."""
import sys

sys.path.insert(0, ".")
from app import ports, porttable  # noqa: E402

fails = []
M = ports.MARK


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def states(dt, cmd, text, config="", model=None):
    p, st = ports.parse(M + cmd + "\n" + text, dt, config, model)
    return {x["n"]: x for x in p}, st


# Ubiquiti EdgeSwitch / UniFi (FastPath): «show interfaces status all»
ES = """
                                    Link    Physical    Physical    Media       Flow
Port       Name                     State   Mode        Status      Type        Control     VLAN
---------  -----------------------  ------  ----------  ----------  ----------  ----------  ----------
0/1        Servidor                 Up      Auto        1000 Full   Copper      Inactive    1
0/2                                 Down    Auto                    Copper      Inactive    1
0/3                                 Down    Auto                    Unknown     Inactive    10
0/25       Uplink                   Up      Auto        1000 Full   Fiber       Inactive    Trunk
0/26                                Down    Auto                    Unknown     Inactive    1
3/1                                 Down
3/2                                 Down
4/1                                 Up
"""
CFG = "interface 0/3\nshutdown\nexit\ninterface 0/1\ndescription 'Servidor'\nexit\n"
by, st = states("ubiquiti_edgeswitch", "show interfaces status all", ES, CFG, "ES-24-250W")
check("EdgeSwitch: solo los puertos 0/N del frente", list(by) == ["0/1", "0/2", "0/3", "0/25", "0/26"], list(by))
check("EdgeSwitch: activo con velocidad y descripción", st and by["0/1"]["s"] == "up" and by["0/1"]["sp"] == "1 Gbps"
      and by["0/1"]["d"] == "Servidor", by["0/1"])
check("EdgeSwitch: apagado según la configuración (shutdown)", by["0/3"]["s"] == "off" and by["0/2"]["s"] == "down")
check("EdgeSwitch: fibra por el tipo de medio y por el modelo (ES-24: 25 y 26 son SFP)",
      by["0/25"]["k"] == "sfp" and by["0/26"]["k"] == "sfp" and by["0/1"]["k"] == "cu", [(n, x["k"]) for n, x in by.items()])
check("EdgeSwitch: troncal", by["0/25"]["net"] == "trunk")
by, _ = states("ubiquiti_unifiswitch", "show interfaces status all", ES, "", "US-24-250W")
check("UniFi (FastPath): mismos puertos", len(by) == 5 and by["0/1"]["s"] == "up")
check("EdgeSwitch: comandos de su familia", ports.commands("ubiquiti_edgeswitch")[0] == "show interfaces status all")

# EdgeSwitch con chip Realtek (ES-18X: «interface gi1», CLI tipo Cisco Small Business)
RTK_CFG = """SYSTEM CONFIG FILE ::= BEGIN
! System Description: RTK ES-18X Switch
system name " VNT-SW-TW"
interface gi1
 switchport hybrid allowed vlan add 40-42 tagged
 description "S-1"
!
interface gi2
 shutdown
 description "S-2"
!
interface gi3
!
"""
RTK = """
                                             Flow Link          Back   Mdix
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled Off
gi2      1G-Copper      --      --     --     --  Down            --     --
gi3      1G-Copper      --      --     --     --  Down (nc)       --     --
gi17     1G-Fiber     Full    1000  Enabled  Off  Up          Disabled Off
"""
by, st = states("ubiquiti_edgeswitch", "show interfaces status", RTK, RTK_CFG)
check("EdgeSwitch Realtek: tabla «show interfaces status»", st and [(k, by[k]["s"]) for k in by]
      == [("gi1", "up"), ("gi2", "off"), ("gi3", "down"), ("gi17", "up")] and by["gi17"]["k"] == "sfp"
      and by["gi1"]["sp"] == "1 Gbps", by)
check("EdgeSwitch Realtek: también prueba «show interfaces status»", "show interfaces status" in ports.commands("ubiquiti_edgeswitch"))
p, st = ports.parse(M + "show interfaces status all\n% Invalid input detected", "ubiquiti_edgeswitch", RTK_CFG)
check("EdgeSwitch Realtek sin tabla: puertos gi de la configuración", not st and [(x["n"], x["s"]) for x in p]
      == [("gi1", "on"), ("gi2", "off"), ("gi3", "on")], p)
from app.drivers import get_profile, parse_inventory  # noqa: E402
inv = parse_inventory("Loader Version   : 1.3.5\nLoader Date      : Jan 17 2025\nFirmware Version : 1.11.1\n",
                      get_profile("ubiquiti_edgeswitch"))
check("EdgeSwitch Realtek: el firmware es 1.11.1, no la versión del cargador", inv["os_version"] == "1.11.1", inv)

# Huawei VRP «display interface brief»
HW = """PHY: Physical
*down: administratively down
^down: standby
(l): loopback
(s): spoofing
(b): BFD down
(e): ETHOAM down
(d): Dampening Suppressed
InUti/OutUti: input utility/output utility
Interface                   PHY   Protocol  InUti OutUti   inErrors  outErrors
Eth-Trunk1                  up    up           0%     0%          0          0
GigabitEthernet0/0/1        up    up        0.01%  0.01%          0          0
GigabitEthernet0/0/2        down  down         0%     0%          0          0
GigabitEthernet0/0/3        *down down         0%     0%          0          0
XGigabitEthernet0/0/1       up    up           0%     0%          0          0
NULL0                       up    up(s)        0%     0%          0          0
Vlanif1                     up    up           --     --          0          0
"""
by, st = states("huawei", "display interface brief", HW)
check("Huawei: físicos, sin Eth-Trunk/NULL/Vlanif", list(by) == ["GigabitEthernet0/0/1", "GigabitEthernet0/0/2",
                                                                  "GigabitEthernet0/0/3", "XGigabitEthernet0/0/1"], list(by))
check("Huawei: up, down, *down = apagado y XGE = fibra", [by[k]["s"] for k in by] == ["up", "down", "off", "up"]
      and by["XGigabitEthernet0/0/1"]["k"] == "sfp")

# H3C / HPE Comware «display interface brief»
H3C = """Brief information on interfaces in route mode:
Link: ADM - administratively down; Stby - standby
Protocol: (s) - spoofing
Interface            Link Protocol Primary IP      Description
InLoop0              UP   UP(s)    --
NULL0                UP   UP(s)    --
Vlan1                UP   UP       192.168.0.1

Brief information on interfaces in bridge mode:
Link: ADM - administratively down; Stby - standby
Speed: (a) - auto
Duplex: (a)/A - auto; H - half; F - full
Type: A - access; T - trunk; H - hybrid
Interface            Link Speed   Duplex Type PVID Description
GE1/0/1              UP   1G(a)   F(a)   A    1    Servidor
GE1/0/2              DOWN auto    A      A    1
GE1/0/3              ADM  auto    A      A    1
XGE1/0/49            UP   10G(a)  F(a)   T    1
"""
by, _ = states("hp_comware", "display interface brief", H3C)
check("H3C: tabla de puertos en modo bridge", list(by) == ["GE1/0/1", "GE1/0/2", "GE1/0/3", "XGE1/0/49"], list(by))
check("H3C: UP/DOWN/ADM, velocidad y fibra XGE", [by[k]["s"] for k in by] == ["up", "down", "off", "up"]
      and by["GE1/0/1"]["sp"] == "1 Gbps" and by["XGE1/0/49"]["sp"] == "10 Gbps" and by["XGE1/0/49"]["k"] == "sfp", by)

# HPE ProCurve / Aruba OS-Switch «show interfaces brief»
PC = """
 Status and Counters - Port Status

                  | Intrusion                           MDI   Flow  Bcast
  Port  Type      | Alert     Enabled Status Mode       Mode  Ctrl  Limit
  ----- --------- + --------- ------- ------ ---------- ----- ----- ------
  1     100/1000T | No        Yes     Up     1000FDx    MDIX  off   0
  2     100/1000T | No        Yes     Down   1000FDx    Auto  off   0
  3     100/1000T | No        No      Down   1000FDx    Auto  off   0
  25    SFP+SR    | No        Yes     Up     10GigFD    NA    off   0
  26              | No        Yes     Down   .          .     off   0
"""
by, _ = states("hp_procurve", "show interfaces brief", PC)
check("ProCurve: Enabled=No es apagado, SFP+ por el tipo", [by[k]["s"] for k in ("1", "2", "3", "25")] == ["up", "down", "off", "up"]
      and by["25"]["k"] == "sfp" and by["1"]["sp"] == "1 Gbps" and by["25"]["sp"] == "10 Gbps", by)

# Aruba AOS-CX «show interface brief»
CX = """
--------------------------------------------------------------------------------------------------------
Port      Native  Mode   Type           Enabled Status  Reason                  Speed   Description
          VLAN                                                                  (Mb/s)
--------------------------------------------------------------------------------------------------------
1/1/1     1       access 1GbT           yes     up                              1000    Servidor
1/1/2     1       access 1GbT           yes     down    Waiting for link        --      --
1/1/3     1       access 1GbT           no      down    Administratively down   --      --
1/1/49    --      routed SFP+DA3        yes     up                              10000   --
"""
by, _ = states("aruba_aoscx", "show interface brief", CX)
check("AOS-CX: estados, velocidad, descripción y SFP+", [by[k]["s"] for k in by] == ["up", "down", "off", "up"]
      and by["1/1/1"]["d"] == "Servidor" and by["1/1/49"]["k"] == "sfp" and by["1/1/49"]["sp"] == "10 Gbps", by)

# Extreme EXOS «show ports no-refresh»
EX = """Port Summary
Port     Display              VLAN Name          Port  Link  Speed  Duplex
#        String               (or # VLANs)       State State Actual Actual
==================================================================================
1        Servidor             Default            E     A     1000   FULL
2                             Default            E     R
3                             Default            D     R
4                             Default            E     NP
==================================================================================
   Port State: D-Disabled, E-Enabled, F-Disabled due to link-flap detection
   Link State: A-Active, R-Ready, NP-Port Not Present, L-Loopback
"""
by, _ = states("extreme_exos", "show ports no-refresh", EX)
check("EXOS: E/D y A/R/NP", [by[k]["s"] for k in ("1", "2", "3", "4")] == ["up", "down", "off", "empty"]
      and by["1"]["d"] == "Servidor" and by["1"]["sp"] == "1 Gbps", by)

# Cisco NX-OS «show interface status» (con guiones)
NX = """
--------------------------------------------------------------------------------
Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
mgmt0         --                 connected routed    full    1000    --
Eth1/1        uplink             connected trunk     full    10G     10Gbase-SR
Eth1/2        --                 notconnec 1         auto    auto    10Gbase-SR
Eth1/3        --                 disabled  1         auto    auto    SFP-H10GB-C
Eth1/4        --                 sfpAbsent 1         auto    auto    --
Po1           --                 connected trunk     full    10G     --
Vlan1         --                 down      routed    auto    auto    --
"""
by, _ = states("cisco_nxos", "show interface status", NX)
check("NX-OS: estados, fibra, sin Po/Vlan", [by[k]["s"] for k in ("Eth1/1", "Eth1/2", "Eth1/3", "Eth1/4")] == ["up", "down", "off", "empty"]
      and by["Eth1/1"]["k"] == "sfp" and "Po1" not in by and "Vlan1" not in by, by)

# Arista / Dell / TP-Link: «show interfaces status» genérico
AR = """Port       Name   Status       Vlan     Duplex Speed  Type            Flags Encapsulation
Et1        srv    connected    1        full   1G     1000BASE-T
Et2               notconnect   1        auto   auto   1000BASE-T
Et3               disabled     1        auto   auto   1000BASE-T
Et49              connected    trunk    full   10G    10GBASE-SR
Ma1               connected    routed   a-full a-1G   10/100/1000
"""
by, _ = states("arista_eos", "show interfaces status", AR)
check("Arista: estados y SFP por el tipo", [by[k]["s"] for k in ("Et1", "Et2", "Et3", "Et49")] == ["up", "down", "off", "up"]
      and by["Et49"]["k"] == "sfp" and by["Et1"]["k"] == "cu", by)

# EdgeRouter / VyOS «show interfaces»
VY = """Codes: S - State, L - Link, u - Up, D - Down, A - Admin Down
Interface    IP Address                        S/L  Description
---------    ----------                        ---  -----------
eth0         200.1.1.2/30                      u/u  WAN
eth1         192.168.1.1/24                    u/D  LAN
eth2         -                                 A/D
eth1.10      10.0.10.1/24                      u/u
lo           127.0.0.1/8                       u/u
switch0      -                                 u/u
"""
by, _ = states("ubiquiti_edgerouter", "show interfaces", VY)
check("EdgeRouter: eth físicas con S/L", [(k, by[k]["s"]) for k in by] == [("eth0", "up"), ("eth1", "down"), ("eth2", "off")]
      and by["eth0"]["net"] == "200.1.1.2/30" and by["eth0"]["d"] == "WAN", by)

# FortiGate «get system interface physical»
FG = """== [onboard]
        ==[wan1]
                mode: dhcp
                ip: 200.1.1.2 255.255.255.252
                ipv6: ::/0
                status: up
                speed: 1000Mbps (Duplex: full)
        ==[dmz]
                mode: static
                ip: 10.10.10.1 255.255.255.0
                status: down
                speed: n/a
        ==[x1]
                mode: static
                ip: 0.0.0.0 0.0.0.0
                status: up
                speed: 10000Mbps (Duplex: full)
"""
by, _ = states("fortinet", "get system interface physical", FG)
check("FortiGate: estado, velocidad, IP y x1 de fibra", [by[k]["s"] for k in by] == ["up", "down", "up"]
      and by["wan1"]["sp"] == "1 Gbps" and by["wan1"]["net"] == "200.1.1.2/30" and by["x1"]["k"] == "sfp", by)

# Palo Alto «show interface all»
PA = """total configured hardware interfaces: 3

name                    id    speed/duplex/state            mac address
--------------------------------------------------------------------------------
ethernet1/1             16    1000/full/up                  00:1b:17:00:01:10
ethernet1/2             17    ukn/ukn/down(autoneg)         00:1b:17:00:01:11
ethernet1/3             18    10000/full/up                 00:1b:17:00:01:12
"""
by, _ = states("paloalto_panos", "show interface all", PA)
check("PAN-OS: estado y velocidad", [by[k]["s"] for k in by] == ["up", "down", "up"] and by["ethernet1/3"]["sp"] == "10 Gbps", by)

# Linux (y airOS / UniFi AP): /sys/class/net
LX = """@@ eth0 up 1 1000 0x1003
@@ eth1 down 0 -1 0x1003
@@ eth2 down  -1 0x1002
@@ wlan0 up 1  0x1003
"""
by, _ = states("ubuntu_server", ports.commands("ubuntu_server")[0], LX)
check("Linux: arriba, sin enlace y apagado (flags)", [(k, by[k]["s"]) for k in by] == [("eth0", "up"), ("eth1", "down"), ("eth2", "off")]
      and by["eth0"]["sp"] == "1 Gbps", by)
check("airOS usa el mismo lector", ports.commands("ubiquiti_airos") == ports.commands("linux"))

# pfSense / OPNsense «ifconfig -a»
BSD = """igb0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
	description: WAN
	options=e527bb<RXCSUM,TXCSUM>
	inet 200.1.1.2 netmask 0xfffffffc broadcast 200.1.1.3
	media: Ethernet autoselect (1000baseT <full-duplex>)
	status: active
igb1: flags=8802<BROADCAST,SIMPLEX,MULTICAST> metric 0 mtu 1500
	media: Ethernet autoselect
	status: no carrier
ix0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
	media: Ethernet autoselect (10Gbase-SR <full-duplex>)
	status: active
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> metric 0 mtu 16384
enc0: flags=0<> metric 0 mtu 1536
"""
by, _ = states("pfsense", "ifconfig -a", BSD)
check("pfSense: activo, apagado (sin UP), IP y fibra", [(k, by[k]["s"]) for k in by] == [("igb0", "up"), ("igb1", "off"), ("ix0", "up")]
      and by["igb0"]["net"] == "200.1.1.2/30" and by["igb0"]["d"] == "WAN" and by["ix0"]["k"] == "sfp" and by["ix0"]["sp"] == "10 Gbps", by)

# Check Point Gaia «show interfaces all»
GA = """Interface eth1
    state on
    type ethernet
    link-state link up
    link-speed 1000M/full
    comments Externa
    ipv4-address 200.1.1.2/30
Interface eth2
    state off
    type ethernet
    link-state link down
Interface lo
    state on
    type loopback
"""
by, _ = states("checkpoint_gaia", "show interfaces all", GA)
check("Gaia: estado, velocidad y descripción", [(k, by[k]["s"]) for k in by] == [("eth1", "up"), ("eth2", "off")]
      and by["eth1"]["sp"] == "1 Gbps" and by["eth1"]["d"] == "Externa", by)

# Nokia SR OS «show port»
NK = """===============================================================================
Ports on Slot 1
===============================================================================
Port          Admin Link Port    Cfg  Oper LAG/ Port Port Port   C/QS/S/XFP/
Id            State      State   MTU  MTU  Bndl Mode Encp Type   MDIMDX
-------------------------------------------------------------------------------
1/1/1         Up    Yes  Up      9212 9212    - netw null xcme   GIGE-LX  10KM
1/1/2         Up    No   Down    9212 9212    - netw null xcme   GIGE-LX  10KM
1/1/3         Down  No   Down    9212 9212    - netw null xcme
"""
by, _ = states("nokia_sros", "show port", NK)
check("Nokia SR OS: admin y enlace", [by[k]["s"] for k in ("1/1/1", "1/1/2", "1/1/3")] == ["up", "down", "off"], by)

# Cisco WLC «show port summary»
WLC = """
           STP   Admin   Physical   Physical   Link   Link
Pr  Type   Stat  Mode    Mode       Status     Status Trap     POE    SFPType
--  ----   ----  ------- ---------- ---------- ------ -------  ------ ----------
1   Normal Forw  Enable  Auto       1000 Full  Up     Enable   N/A    1000BaseTX
2   Normal Disa  Enable  Auto       Auto       Down   Enable   N/A    Not Present
3   Normal Disa  Disable Auto       Auto       Down   Enable   N/A    1000BaseTX
"""
by, _ = states("cisco_wlc", "show port summary", WLC)
check("WLC: estado, sin módulo y apagado", [by[k]["s"] for k in ("1", "2", "3")] == ["up", "empty", "off"], by)

# sin tabla de estado: la configuración (FastPath, Junos/VyOS en «set»)
by, st = states("ubiquiti_edgeswitch", "show interfaces status all", "% Invalid input", CFG)
check("EdgeSwitch sin tabla: puertos de la configuración", not st and by["0/3"]["s"] == "off" and by["0/1"]["s"] == "on"
      and by["0/1"]["d"] == "Servidor", by)
p, st = ports.parse(None, "vyos", "set interfaces ethernet eth0 address dhcp\nset interfaces ethernet eth1 disable\n"
                    "set interfaces ethernet eth1 description 'LAN'")
check("VyOS sin tabla: eth0 habilitada, eth1 apagada", not st and [(x["n"], x["s"]) for x in p] == [("eth0", "on"), ("eth1", "off")], p)
check("todas las plataformas tienen una orden de estado", all(ports.commands(k) for k in
      ["cisco_ios", "huawei", "hp_comware", "ubiquiti_edgeswitch", "vyos", "fortinet", "linux", "pfsense", "zyxel_os", "dlink_ds"]))
check("modelo -> puertos de cobre", (ports.model_ports("US-24-250W"), ports.model_ports("WS-C2960X-48FPD-L"),
                                     ports.model_ports("ES-8-150W"), ports.model_ports("hEX S")) == (24, 48, 8, None))
check("velocidades", [porttable.speed(x) for x in ("1000 Full", "a-1000", "1000Mbps", "1G(a)", "10GigFD", "1000FDx", "auto", "ukn")]
      == ["1 Gbps", "1 Gbps", "1 Gbps", "1 Gbps", "10 Gbps", "1 Gbps", "", ""])

print("TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
