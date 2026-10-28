"""Reversión propia de MikroTik RouterOS, Extreme EXOS y Extreme VOSS (rollback_vendor) y plan por plataforma
(rollback_plan): qué se revierte por comandos, en qué orden, sin tocar la gestión, y qué se restaura a mano."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.drivers import get_profile  # noqa: E402
from app.rollback import rollback_plan, rollback_supported  # noqa: E402
from app.rollback_vendor import exos, routeros, voss  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


ROS_TGT = """# 2026-10-20 10:00:00 by RouterOS 7.16
/interface bridge add name=bridge1
/interface ethernet set [ find default-name=ether1 ] comment=WAN
/interface bridge port add bridge=bridge1 interface=ether2
/interface bridge port add bridge=bridge1 interface=ether3
/ip address add address=192.168.88.1/24 interface=bridge1 network=192.168.88.0
/ip address add address=10.0.0.2/24 interface=ether1 network=10.0.0.0
/ip firewall filter add action=accept chain=input comment="defconf: established" connection-state=established,related
/ip firewall filter add action=drop chain=input comment="bloquear telnet" dst-port=23 protocol=tcp
/ip firewall filter add action=drop chain=input in-interface=ether1
/ip route add dst-address=0.0.0.0/0 gateway=10.0.0.1
/system identity set name=R1
"""
ROS_CUR = """# 2026-10-26 10:00:00 by RouterOS 7.16
/interface bridge add name=bridge1
/interface vlan add interface=bridge1 name=vlan20 vlan-id=20
/interface ethernet set [ find default-name=ether1 ] comment="WAN nuevo"
/interface bridge port add bridge=bridge1 interface=ether2
/interface bridge port add bridge=bridge1 interface=ether3
/ip address add address=192.168.88.1/24 interface=bridge1 network=192.168.88.0
/ip address add address=10.0.0.2/24 interface=ether1 network=10.0.0.0
/ip address add address=192.168.20.1/24 interface=vlan20 network=192.168.20.0
/ip firewall filter add action=accept chain=input comment="defconf: established" connection-state=established,related
/ip firewall filter add action=drop chain=input in-interface=ether1
/ip firewall nat add action=masquerade chain=srcnat out-interface=ether1
/ip route add dst-address=0.0.0.0/0 gateway=10.0.0.1
/system identity set name=R1-cambiado
/ip dns set servers=1.1.1.1
"""
EXOS_TGT = '''configure snmp sysName "SW-EXOS"
create vlan "Datos"
configure vlan Datos tag 20
create vlan "Gestion"
configure vlan Gestion tag 99
configure vlan Default delete ports 1-8
configure vlan Datos add ports 1-4 untagged
configure vlan Datos add ports 25 tagged
configure vlan Gestion add ports 25 tagged
configure vlan Gestion ipaddress 10.99.0.5 255.255.255.0
configure iproute add default 10.99.0.1
enable sntp-client
'''
EXOS_CUR = '''configure snmp sysName "SW-EXOS-2"
create vlan "Datos"
configure vlan Datos tag 20
create vlan "Voz"
configure vlan Voz tag 30
create vlan "Gestion"
configure vlan Gestion tag 99
configure vlan Default delete ports 1-8
configure vlan Datos add ports 1-3 untagged
configure vlan Voz add ports 4 untagged
configure vlan Voz add ports 25 tagged
configure vlan Datos add ports 25 tagged
configure vlan Gestion add ports 25 tagged
configure vlan Gestion ipaddress 10.99.0.5 255.255.255.0
configure vlan Voz ipaddress 10.30.0.1 255.255.255.0
configure iproute add default 10.99.0.1
disable sntp-client
'''
VOSS_TGT = '''config terminal
snmp-server name "VSP-Core"
vlan create 10 name "Datos" type port-mstprstp 0
vlan members add 10 1/1-1/4 portmember
vlan i-sid 10 10010
interface GigabitEthernet 1/1
encapsulation dot1q
no shutdown
exit
interface Vlan 10
ip address 10.10.0.1 255.255.255.0 0
exit
mgmt vlan
ip address 10.99.0.6/24
exit
ip route 0.0.0.0 0.0.0.0 10.99.0.1 weight 1
end
'''
VOSS_CUR = '''config terminal
snmp-server name "VSP-Core-X"
vlan create 10 name "Datos" type port-mstprstp 0
vlan create 20 name "Voz" type port-mstprstp 0
vlan members add 10 1/1-1/3 portmember
vlan members add 20 1/4 portmember
vlan i-sid 10 10010
vlan i-sid 20 10020
interface GigabitEthernet 1/1
encapsulation dot1q
shutdown
exit
interface GigabitEthernet 1/5
name "temporal"
exit
interface Vlan 10
ip address 10.10.0.1 255.255.255.0 0
exit
interface Vlan 20
ip address 10.20.0.1 255.255.255.0 0
exit
mgmt vlan
ip address 10.99.0.6/24
exit
ip route 0.0.0.0 0.0.0.0 10.99.0.1 weight 1
end
'''

# MikroTik RouterOS
cmds, notes = routeros(ROS_CUR, ROS_TGT, mgmt="10.0.0.2")
check("RouterOS: set con su selector (no cambia todas las interfaces)",
      "/interface ethernet set [ find default-name=ether1 ] comment=WAN" in cmds)
check("RouterOS: la regla borrada vuelve a su sitio (place-before)",
      any(c.startswith('/ip firewall filter add action=drop chain=input comment="bloquear telnet"')
          and 'place-before=[ find where action="drop" and chain="input" and in-interface="ether1" ]' in c for c in cmds))
check("RouterOS: lo añadido se borra por su clave, con «find where» y comillas (lo único que RouterOS encuentra), en orden inverso",
      cmds[-3:] == ['/ip firewall nat remove [ find where action="masquerade" and chain="srcnat" and out-interface="ether1" ]',
                    '/ip address remove [ find where address="192.168.20.1/24" ]', '/interface vlan remove [ find where name="vlan20" ]'], cmds)
check("RouterOS: identidad", "/system identity set name=R1" in cmds)
check("RouterOS: lo que no se puede volver al valor por omisión se avisa (servidores DNS)", any("servers" in n for n in notes), notes)
check("RouterOS: nunca re-aplica la configuración entera", not any(c.startswith("/interface bridge add") for c in cmds))
# alguien movió la IP de gestión a otra interfaz: devolverla a la original cortaría la conexión a mitad
c2, n2 = routeros(ROS_CUR.replace("address=10.0.0.2/24 interface=ether1", "address=10.0.0.2/24 interface=bridge1"),
                  ROS_TGT, mgmt="10.0.0.2")
check("RouterOS: no toca la dirección de gestión de OpenNCCM (y lo avisa)",
      not any("10.0.0.2" in c for c in c2) and any("gestiona" in n for n in n2), (c2, n2))
check("RouterOS: sin cambios, sin comandos", routeros(ROS_TGT, ROS_TGT) == ([], []))
c5, n5 = routeros(ROS_TGT + "/system keymat-provider add name=default" + chr(10), ROS_TGT)
check("RouterOS: no borra lo que RouterOS crea por sí mismo (name=default), lo avisa", c5 == [] and n5, (c5, n5))
# RouterOS 7.25 con terminal estrecho parte las líneas largas de /export con «\» y sangría en la siguiente
BS = chr(92)
wrapped = ROS_TGT.replace("dst-port=23 protocol=tcp", "dst-port=23 protoc" + BS + chr(10) + "    ol=tcp")
check("RouterOS: las líneas partidas con barra invertida se leen como una sola (sin cambios falsos)", routeros(wrapped, ROS_TGT) == ([], []),
      routeros(wrapped, ROS_TGT))

# Extreme EXOS
cmds, notes = exos(EXOS_CUR, EXOS_TGT, mgmt="10.99.0.5")
check("EXOS: borra la VLAN que sobra antes de mover sus puertos (un puerto sin etiqueta solo va en una VLAN)",
      cmds.index('delete vlan "Voz"') < cmds.index("configure vlan Datos add ports 1-4 untagged"), cmds)
check("EXOS: sin comandos repetidos", len(cmds) == len(set(cmds)), cmds)
check("EXOS: nombre SNMP y «enable sntp-client»", 'configure snmp sysName "SW-EXOS"' in cmds and "enable sntp-client" in cmds)
check("EXOS: no toca la VLAN de gestión", not any("Gestion" in c for c in cmds), cmds)
check("EXOS: lo de la VLAN borrada se va con ella", not any(c.startswith(("configure vlan Voz", "unconfigure vlan Voz")) for c in cmds), cmds)
c3, _n = exos(EXOS_TGT + "configure iproute add 10.50.0.0/16 10.99.0.254\n", EXOS_TGT)
check("EXOS: rutas añadidas se borran", c3 == ["configure iproute delete 10.50.0.0/16 10.99.0.254"], c3)
c4, n4 = exos(EXOS_TGT + "configure stpd s0 mode dot1w\n", EXOS_TGT)
check("EXOS: lo que no sabe deshacer lo avisa en vez de inventar un comando", c4 == [] and n4, (c4, n4))

# Extreme VOSS / Fabric Engine
cmds, notes = voss(VOSS_CUR, VOSS_TGT, mgmt="10.99.0.6")
check("VOSS: borra la VLAN que sobra (con su i-sid, puertos e interfaz IP) antes de añadir puertos",
      cmds.index("vlan delete 20") < cmds.index("vlan members add 10 1/1-1/4 portmember")
      and not any(c in ("no vlan i-sid 20", "vlan members remove 20 1/4", "no interface Vlan 20") for c in cmds), cmds)
check("VOSS: puerto apagado vuelve a encenderse una sola vez", cmds.count("no shutdown") == 1, cmds)
check("VOSS: los puertos no se borran, vuelven a su valor", "interface GigabitEthernet 1/5" in cmds and 'no name "temporal"' in cmds)
check("VOSS: no toca la gestión", not any("10.99.0" in c or c.startswith("mgmt") for c in cmds), cmds)

# plan por plataforma
for key in ("cisco_ios", "ubiquiti_edgeswitch", "juniper_junos", "huawei", "mikrotik_routeros", "extreme_exos", "extreme_vsp"):
    check(f"{key}: reversión por comandos", rollback_supported(get_profile(key)))
for key in ("watchguard_fireware", "ubiquiti_unifiswitch", "fortinet"):
    cmds, notes = rollback_plan("a\n", "b\n", get_profile(key))
    check(f"{key}: sin comandos a ciegas, con instrucciones para restaurar a mano",
          cmds is None and notes and not rollback_supported(get_profile(key)), notes)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
