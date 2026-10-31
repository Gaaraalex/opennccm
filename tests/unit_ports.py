"""Puertos de la ficha del equipo (app/ports.py): estado leído en el respaldo por plataforma y datos de la configuración."""
import sys

sys.path.insert(0, ".")
from app import ports  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


M = ports.MARK
# MikroTik hEX S: 5 de cobre y 1 SFP; ether5 deshabilitado, ether4 sin enlace
ROS = (M + "/interface ethernet print terse\n"
       " 0 R  name=ether1 default-name=ether1 mtu=1500 mac-address=48:A9:8A:00:00:01 comment=\"WAN \\C2\\B7 proveedor\"\n"
       " 1 RS name=ether2 default-name=ether2 mtu=1500 mac-address=48:A9:8A:00:00:02 comment=\"LAN oficina\"\n"
       " 2 RS name=ether3 default-name=ether3 mtu=1500\n"
       " 3  S name=ether4 default-name=ether4 mtu=1500\n"
       " 4 X  name=ether5 default-name=ether5 mtu=1500 comment=\"Sin uso\"\n"
       " 5 R  name=sfp1 default-name=sfp1 mtu=1500\n"
       + M + '/interface ethernet monitor "ether1" once\n                      name: ether1\n                    status: link-ok\n'
       "          auto-negotiation: done\n                      rate: 1Gbps\n               full-duplex: yes\n"
       + M + '/interface ethernet monitor "ether2" once\n    name: ether2\n  status: link-ok\n    rate: 100Mbps\n'
       + M + '/interface ethernet monitor "ether4" once\n    name: ether4\n  status: no-link\n'
       + M + '/interface ethernet monitor "sfp1" once\n    name: sfp1\n  status: link-ok\n    rate: 1Gbps\n'
       "  sfp-module-present: yes\n  sfp-vendor-name: FS\n  sfp-vendor-part-number: SFP-GE-LX\n  sfp-wavelength: 1310nm\n")
CFG = ("/interface ethernet\nset [ find default-name=ether1 ] comment=\"WAN\"\nset [ find default-name=ether5 ] disabled=yes\n"
       "/interface bridge port add bridge=bridge interface=ether2\n"
       "/interface bridge port add bridge=bridge interface=ether3 pvid=10\n/ip address add address=200.75.40.18/30 interface=ether1\n")
p, st = ports.parse(ROS, "mikrotik_routeros", CFG)
by = {x["n"]: x for x in p}
check("MikroTik: 6 puertos en orden físico", [x["n"] for x in p] == ["ether1", "ether2", "ether3", "ether4", "ether5", "sfp1"], p)
check("MikroTik: con estado de enlace", st)
check("MikroTik: activo con velocidad", by["ether1"]["s"] == "up" and by["ether1"]["sp"] == "1 Gbps", by["ether1"])
check("MikroTik: comentario decodificado", by["ether1"]["d"] == "WAN · proveedor", by["ether1"]["d"])
check("MikroTik: sin monitor, la bandera R dice que hay enlace", by["ether3"]["s"] == "up")
check("MikroTik: sin enlace", by["ether4"]["s"] == "down")
check("MikroTik: deshabilitado = apagado", by["ether5"]["s"] == "off")
check("MikroTik: SFP con módulo", by["sfp1"]["k"] == "sfp" and by["sfp1"]["mod"] == "FS · SFP-GE-LX · 1310 nm", by["sfp1"])
ports.enrich(p, "mikrotik_routeros", CFG, [{"local_port": "sfp1", "remote_name": "SW-NEGOCIOS", "remote_port": "sfp-sfpplus9"}])
check("MikroTik: dirección de la configuración", by["ether1"]["net"] == "200.75.40.18/30", by["ether1"]["net"])
check("MikroTik: puerto en un bridge, con su etiqueta", by["ether2"]["net"] == "bridge" and by["ether2"]["nk"] == "bridge", by["ether2"])
check("MikroTik: puerto con IP, etiqueta «Dirección»", by["ether1"]["nk"] == "ip")
check("MikroTik: bridge con VLAN (pvid)", by["ether3"]["net"] == "bridge · VLAN 10", by["ether3"]["net"])
check("MikroTik: vecino del mapa", by["sfp1"]["nb"] == "SW-NEGOCIOS · sfp-sfpplus9", by["sfp1"]["nb"])
check("MikroTik: línea de la configuración que lo apaga", any("disabled=yes" in l for l in by["ether5"]["cfg"]), by["ether5"]["cfg"])
empty = ROS.replace("sfp-module-present: yes", "sfp-module-present: no").replace("  status: link-ok\n    rate: 1Gbps\n  sfp", "  status: no-link\n  sfp")
check("MikroTik: jaula SFP vacía", {x["n"]: x for x in ports.parse(empty, "mikrotik_routeros")[0]}["sfp1"]["s"] == "empty")

IOS = (M + "show interfaces status\n"
       "Port      Name               Status       Vlan       Duplex  Speed Type\n"
       "Gi1/0/1   Uplink core        connected    trunk      a-full a-1000 10/100/1000BaseTX\n"
       "Gi1/0/2                      notconnect   10           auto   auto 10/100/1000BaseTX\n"
       "Gi1/0/3   Reservado          disabled     1            auto   auto 10/100/1000BaseTX\n"
       "Te1/1/1                      notconnect   1            full    10G Not Present\n"
       "Te1/1/2   A SW-2             connected    trunk        full    10G SFP-10GBase-LR\n"
       "Po1                          connected    trunk      a-full a-1000\n")
p, st = ports.parse(IOS, "cisco_ios", "interface GigabitEthernet1/0/3\n description Reservado\n shutdown\n!\n"
                    "interface GigabitEthernet1/0/2\n switchport access vlan 10\n")
by = {x["n"]: x for x in p}
check("IOS: solo puertos físicos", list(by) == ["Gi1/0/1", "Gi1/0/2", "Gi1/0/3", "Te1/1/1", "Te1/1/2"], list(by))
check("IOS: activo, troncal y 1 Gbps", by["Gi1/0/1"]["s"] == "up" and by["Gi1/0/1"]["sp"] == "1 Gbps" and by["Gi1/0/1"]["d"] == "Uplink core")
check("IOS: apagado", by["Gi1/0/3"]["s"] == "off")
check("IOS: SFP sin módulo", by["Te1/1/1"]["s"] == "empty" and by["Te1/1/1"]["k"] == "sfp")
check("IOS: SFP con módulo a 10 Gbps", by["Te1/1/2"]["s"] == "up" and by["Te1/1/2"]["sp"] == "10 Gbps" and by["Te1/1/2"]["mod"])
ports.enrich(p, "cisco_ios", "interface GigabitEthernet1/0/3\n description Reservado\n shutdown\n!\n", [])
check("IOS: bloque de la configuración por nombre corto", " shutdown" in by["Gi1/0/3"]["cfg"], by["Gi1/0/3"]["cfg"])

ASA = (M + "show interface ip brief\nInterface                  IP-Address      OK? Method Status                Protocol\n"
       "GigabitEthernet0/0         200.1.1.2       YES CONFIG up                    up\n"
       "GigabitEthernet0/1         unassigned      YES unset  administratively down down\n"
       "GigabitEthernet0/1.10      10.0.10.1       YES CONFIG up                    up\n"
       "Management0/0              unassigned      YES unset  down                  down\n")
p, _ = ports.parse(ASA, "cisco_ftd")
by = {x["n"]: x for x in p}
check("ASA/FTD: estados y sin subinterfaces", [x["n"] for x in p] == ["GigabitEthernet0/0", "GigabitEthernet0/1", "Management0/0"]
      and by["GigabitEthernet0/0"]["s"] == "up" and by["GigabitEthernet0/1"]["s"] == "off" and by["Management0/0"]["s"] == "down", p)

JUN = (M + "show interfaces terse\nInterface               Admin Link Proto    Local                 Remote\n"
       "ge-0/0/0                up    up\nge-0/0/0.0              up    up   inet     192.168.1.1/24\n"
       "ge-0/0/1                down  down\nxe-0/1/0                up    down\nlo0                     up    up\n")
p, _ = ports.parse(JUN, "juniper_junos")
by = {x["n"]: x for x in p}
check("Junos: físicos, apagado, fibra y dirección", list(by) == ["ge-0/0/0", "ge-0/0/1", "xe-0/1/0"] and by["ge-0/0/1"]["s"] == "off"
      and by["xe-0/1/0"]["k"] == "sfp" and by["ge-0/0/0"]["net"] == "192.168.1.1/24", p)

p, st = ports.parse(None, "arista_eos", "interface Ethernet1\n shutdown\n!\ninterface Ethernet2\n!\ninterface Vlan10\n!")
check("Sin estado de enlace: apagado o habilitado según la configuración",
      not st and [(x["n"], x["s"]) for x in p] == [("Ethernet1", "off"), ("Ethernet2", "on")], p)
check("Plataforma sin bloques de interfaz: sin puertos", ports.parse(None, "fortinet", "config system interface")[0] == [])

print("TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
