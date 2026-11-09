"""Lo que encontró el laboratorio de GNS3 con VOSS, Cisco, Juniper y OPNsense (2026-11-09):
- Junos: la serie sale de «show chassis hardware» (no está en «show version»); un vMX sin serie no toma «VMX».
- OPNsense 26.7: el aviso de entrada llega en trozos y uno termina en «->»; no se confunde con el prompt del shell."""
import os
import re
import sys
import tempfile
import time

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import drivers, shell_drivers  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


jp = drivers.PROFILE_BY_KEY["juniper_junos"] if hasattr(drivers, "PROFILE_BY_KEY") else next(
    p for p in drivers.PROFILES if p.key == "juniper_junos")
check("Junos: el inventario también lee «show chassis hardware»", "show chassis hardware" in (jp.inventory or ""))
VER = "Hostname: R1\nModel: mx80\nJunos: 14.1R4.10\n"
HW = {"MX80": "Item             Version  Part number  Serial number     Description\n"
              "Chassis                                JN11E3A6EAFA      MX80\nMidplane         REV 08   711-031603   ABAC8475\n",
      "EX4200": "Chassis            REV 07   750-021778   BP0208111234      EX4200-48T\n",
      "SRX300": "Chassis                                CV1714AF0386      SRX300\n",
      "vMX": "Item             Version  Part number  Serial number     Description\n"
             "Chassis                                                 VMX\nMidplane\nRouting Engine     RE-VMX\n"}
want = {"MX80": "JN11E3A6EAFA", "EX4200": "BP0208111234", "SRX300": "CV1714AF0386", "vMX": None}
for k, hw in HW.items():
    inv = drivers.parse_inventory(VER + "\n\n" + hw, jp)
    check(f"Junos {k}: serie {want[k] or '(ninguna)'}", inv.get("serial") == want[k], inv.get("serial"))


class FakeChan(shell_drivers.FreeBSDFirewallSSH):
    """Solo _read_until, con trozos que llegan con pausas como por SSH."""

    def __init__(self, parts):  # noqa: D401 - sin conexión
        self.parts = list(parts)

    def read_channel(self):
        if self.parts:
            delay, txt = self.parts[0]
            if time.time() >= self.t0 + delay:
                self.parts.pop(0)
                return txt
        return ""


ch = FakeChan([(0.0, "*** O-1.internal: OPNsense 26.7 (amd64) ***\n\n LAN (vtnet0)    ->"),
               (0.5, " v4: 192.168.1.1/24\n  0) Logout   8) Shell\n\nEnter an option: ")])
ch.t0 = time.time()
got = ch._read_until(re.compile(r"(Enter an option:|[#$%>:])\s*$"), 10)
check("OPNsense: un trozo que termina en «->» no se toma como prompt (espera el menú)", shell_drivers.MENU.search(got) is not None, got[-60:])

# ---- Junos: lo que sobra se borra por el objeto entero (no deja «prefix-list X» vacía) y nunca la gestión
from app import ports, refconfig, rollback  # noqa: E402
from app.drivers import IOS  # noqa: E402

cur = ("set system host-name M-2\nset interfaces fxp0 unit 0 family inet address 10.0.0.242/24\n"
       "set policy-options prefix-list labsobra 172.31.155.0/24\nset interfaces ge-0/0/9 description x\n"
       "set interfaces ge-0/0/9 unit 0 family inet address 1.1.1.1/24")
tgt = "set system host-name M-1\nset interfaces fxp0 unit 0 family inet address 10.0.0.241/24\nset interfaces ge-0/0/9 description x"
out = rollback.build_rollback(cur, tgt, "set", "10.0.0.242")
check("Junos: borra la lista entera («delete policy-options prefix-list labsobra»)", "delete policy-options prefix-list labsobra" in out, out)
check("Junos: corta después del nombre, nunca en la palabra clave («unit 0», no «unit»)", "delete interfaces ge-0/0/9 unit 0" in out, out)
check("Junos: restaurar la copia de otro equipo no toca fxp0 (ni borra la suya ni pone la del otro)",
      not any("fxp0" in x for x in out), out)

# ---- Cisco: lo propio de cada equipo no se copia ni se borra al restaurar
c1 = ("hostname C-1\ncrypto pki trustpoint TP-self-signed-111\n enrollment selfsigned\n rsakeypair TP-self-signed-111\n"
      "crypto pki certificate chain TP-self-signed-111\n certificate self-signed 01\n  30820330 30820218\n  \tquit\n"
      "license udi pid CSR1000V sn AAA\ninterface GigabitEthernet1\n ip dhcp client client-id ascii AAA\n ip address dhcp\n")
c2 = c1.replace("111", "222").replace("AAA", "BBB").replace("C-1", "C-2") + "interface Loopback5\n description sobra\n"
out = rollback.build_rollback(c2, c1, IOS)
check("IOS: restaurar en otro no toca el certificado autofirmado, la licencia ni el client-id",
      not any(re.search(r"TP-self-signed|license udi|client-id", x) for x in out) and "no interface Loopback5" in out, out)

# ---- configuración de referencia: «no no …», VOSS «vlan delete», lo propio de Cisco
class D:
    def __init__(self, i, t, host):
        self.id, self.device_type, self.host, self.model = i, t, host, ""


ref = "hostname S-1\ninterface Loopback1\n ip address 10.1.1.1 255.255.255.0\n"
dev = "hostname S-2\ninterface Loopback1\n ip address 10.1.1.1 255.255.255.0\ninterface Loopback166\n description labref\n no ip address\n"
cmp_ = refconfig.compare(D(1, "cisco_ios", "10.0.0.1"), ref, D(2, "cisco_ios", "10.0.0.2"), dev, {})
body, _n = refconfig.commands("cisco_ios", cmp_)
check("referencia IOS: quita la interfaz que sobra sin «no no ip address» ni volver a crearla",
      body.strip() == "no interface Loopback166", body)
cmp_ = refconfig.compare(D(1, "cisco_xe", "10.0.0.1"), c1, D(2, "cisco_xe", "10.0.0.2"), c2.replace("interface Loopback5\n description sobra\n", ""), {})
check("referencia IOS-XE: el certificado, la licencia y el client-id de cada equipo no cuentan como diferencia", cmp_["n"] == 0,
      [k for _s, k in cmp_["missing"] + cmp_["extra"]])
vref = 'snmp-server name "V-1"\nvlan create 10 name "DATOS" type port-mstprstp 0\n'
vdev = 'snmp-server name "V-2"\nvlan create 10 name "DATOS" type port-mstprstp 0\nvlan create 166 name "labref" type port-mstprstp 0\n'
cmp_ = refconfig.compare(D(1, "extreme_vsp", "10.0.0.1"), vref, D(2, "extreme_vsp", "10.0.0.2"), vdev, {})
body, _n = refconfig.commands("extreme_vsp", cmp_)
check("referencia VOSS: la VLAN que sobra se quita con «vlan delete 166»", "vlan delete 166" in body and "no vlan create" not in body, body)
jref = "set system host-name J-1\nset snmp location X\n"
jdev = "set system host-name J-2\nset snmp location X\nset policy-options prefix-list labref 172.31.166.0/24\n"
cmp_ = refconfig.compare(D(1, "juniper_junos", "10.0.0.1"), jref, D(2, "juniper_junos", "10.0.0.2"), jdev, {})
body, _n = refconfig.commands("juniper_junos", cmp_)
check("referencia Junos: la lista que sobra se borra entera", body.strip() == "delete policy-options prefix-list labref", body)

# ---- puertos de un router IOS-XE (sin «show interfaces status»)
raw = ("### puertos: show interfaces status\n% Invalid input detected at '^' marker.\n"
       "### puertos: show ip interface brief\nInterface              IP-Address      OK? Method Status                Protocol\n"
       "GigabitEthernet1       10.0.0.238      YES DHCP   up                    up\n"
       "GigabitEthernet3       unassigned      YES unset  administratively down down\n")
p, live = ports.parse(raw, "cisco_xe")
check("puertos de un CSR1000v: con su enlace («show ip interface brief»)", live and [x["s"] for x in p] == ["up", "off"], p)
check("y la orden está entre las que se prueban", "show ip interface brief" in ports.commands("cisco_xe"))

# ---- respuestas de Cisco: aviso que no es rechazo, rechazo que no se veía; «write memory» dentro de una solicitud
from app import services  # noqa: E402

check("IOS: «Failed to generate persistent self-signed certificate» es un aviso, no un rechazo",
      not services.DEVICE_ERROR.search("ip http secure-server\n% Generating 1024 bit RSA keys\n[OK]\n"
                                       "Failed to generate persistent self-signed certificate.\n    Secure server will use temporary\n"))
check("IOS: «Command rejected: …» es un rechazo", bool(services.DEVICE_ERROR.search(
    'switchport mode trunk\nCommand rejected: An interface whose trunk encapsulation is "Auto" can not be configured to "trunk" mode.\n')))
check("IOS: «write memory» de una receta no se manda en el modo de configuración (OpenNCCM guarda al final)",
      all(services.SAVE_CMD.fullmatch(c) for c in ("write memory", "wr", "copy running-config startup-config"))
      and not services.SAVE_CMD.fullmatch("write erase"))
cur = "interface Loopback10\n description DATOS\n no ip address\n"
tgt = "interface Loopback10\n description DATOS\n ip address 192.168.10.1 255.255.255.0\n"
out = rollback.build_rollback(cur, tgt, IOS)
check("IOS: un «no ip address» que se reemplaza no se niega («ip address» sola)", " ip address" not in out
      and " ip address 192.168.10.1 255.255.255.0" in out, out)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
