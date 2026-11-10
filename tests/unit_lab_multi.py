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
from types import SimpleNamespace  # noqa: E402

try:  # la imagen .fxi de WatchGuard conecta con un equipo armado a mano (no está en la base)
    services._release_db(SimpleNamespace(name="WG-1", host="10.0.0.254"))
    ok_rel = True
except Exception as e:  # noqa: BLE001
    ok_rel = e
check("WatchGuard .fxi: conectar con un equipo que no es de la base no falla («Class SimpleNamespace is not mapped»)",
      ok_rel is True, ok_rel)


class FwConn:
    """Un Firebox: «exit» sale de «configure» y, en el modo principal, cierra la sesión."""
    RETURN = "\n"

    def __init__(self, depth):
        self.depth, self.sent, self.logged_out = depth, [], False

    def check_config_mode(self):
        return self.depth > 0

    def write_channel(self, t):
        self.sent.append(t.strip())
        if t.strip() == "exit":
            if self.depth:
                self.depth -= 1
            else:
                self.logged_out = True

    def read_channel(self):
        return ""


for depth in (0, 2):
    fw = FwConn(depth)
    services._fireware_logout(fw)
    check(f"WatchGuard: al cerrar se sale de «configure» y se cierra la sesión (dentro de {depth} niveles)",
          fw.logged_out and fw.depth == 0 and fw.sent == ["exit"] * (depth + 1), fw.sent)


class FwChan:
    """El canal de la consola web con un Firebox: «exit» sale de cada nivel y, en el principal, el equipo corta."""

    def __init__(self, depth):
        self.depth, self.closed, self.sent = depth, False, 0

    def exit_status_ready(self):
        return self.closed

    def send(self, t):
        self.sent += 1
        if self.depth:
            self.depth -= 1
        else:
            self.closed = True

    def recv_ready(self):
        return False


from app import console  # noqa: E402

for depth in (0, 2):
    ch_ = FwChan(depth)
    console.fireware_logout(ch_)
    check(f"WatchGuard: al cerrar la consola web se cierra la sesión del Firebox (dentro de {depth} niveles de configure)",
          ch_.closed and ch_.sent == depth + 1, (ch_.closed, ch_.sent))


fp = services.fastpath_modes(["enable", "vlan database", "vlan 210", 'vlan name 210 "lab210"', "exit", "write memory"])
check("EdgeSwitch: «vlan database» se abre saliendo de «configure» (y sin el «enable» de la receta)",
      fp == ["exit", "vlan database", "vlan 210", 'vlan name 210 "lab210"', "exit", "write memory"], fp)
fp = services.fastpath_modes(["enable", "configure", "interface 0/5", "vlan pvid 10", "exit", "exit", "write memory"])
check("EdgeSwitch: el «configure» de la receta no se repite y su «exit» final no cierra la sesión",
      fp == ["interface 0/5", "vlan pvid 10", "exit", "exit", "write memory"], fp)
fp = services.fastpath_modes(["vlan database", "vlan 30", "exit", "interface 0/3", "vlan participation include 30", "exit"])
check("EdgeSwitch: después de «vlan database» se vuelve a «configure» para los puertos",
      fp == ["exit", "vlan database", "vlan 30", "exit", "configure", "interface 0/3", "vlan participation include 30", "exit"], fp)
fp = services.fastpath_modes(["enable", "configure", 'hostname "SW-1"', "exit", "write memory"])
check("EdgeSwitch: «hostname» se manda en el modo privilegiado (en «configure» da «Invalid input»)",
      fp == ["exit", 'hostname "SW-1"', "write memory"], fp)
fp = services.fastpath_modes(['hostname "SW-1"', "interface 0/4", 'description "x"', "exit"])
check("EdgeSwitch: y después vuelve a «configure» para lo demás",
      fp == ["exit", 'hostname "SW-1"', "configure", "interface 0/4", 'description "x"', "exit"], fp)
rb = ["exit", "vlan database", "vlan 40", "exit", "configure", "interface 0/2", "exit"]
check("EdgeSwitch: la restauración (que ya trae sus «exit»/«configure») no cambia", services.fastpath_modes(rb) == rb,
      services.fastpath_modes(rb))
check("EdgeSwitch: «VLAN does not exist.» es un rechazo", bool(services.DEVICE_ERROR.search("vlan 210\nVLAN does not exist.\n")))


base = ('!\nhostname "SW"\nvlan database\nvlan 10\nexit\nconfigure\nlogging host "10.0.0.9" ipv4 514 info\n'
        "snmp-server sysname \"SW\"\ninterface 0/1\nexit\nexit\n")
dirty = base.replace('logging host "10.0.0.9" ipv4 514 info\n', 'logging host "10.0.0.9" ipv4 514 info\nlogging host "10.0.0.5" ipv4 514 info\n'
                     'snmp-server community "lab-ro" ro\nlogging cli-command\n')
out = rollback.build_rollback(dirty, base, rollback.FASTPATH if hasattr(rollback, "FASTPATH") else "fastpath")
check("EdgeSwitch: volver a una versión quita el syslog que sobra por su número («logging host remove 2»)",
      "logging host remove 2" in out and not any(o.startswith("no logging host") for o in out), out)
check("EdgeSwitch: y la comunidad SNMP con «no snmp-server community lab-ro»", 'no snmp-server community "lab-ro"' in out
      and "no logging cli-command" in out, out)
out = rollback.build_rollback(base.replace("interface 0/1", 'snmp-server location "Sala"\nsnmp-server contact "noc"\ninterface 0/1'),
                              base, "fastpath")
check("EdgeSwitch: la ubicación y el contacto SNMP no se pueden borrar: no se manda nada (ni «no …» ni «\"\"»)",
      not any("location" in o or "contact" in o for o in out), out)
p_cur = base.replace("interface 0/1\nexit", "interface 0/1\nvlan participation exclude 1,10\nvlan participation include 30\nexit")
out = rollback.build_rollback(p_cur, base, "fastpath")
check("EdgeSwitch: un puerto vuelve a la VLAN 1 con «include 1» y a las demás con «auto»",
      "vlan participation include 1" in out and "vlan participation auto 10" in out and "vlan participation auto 30" in out
      and not any(o.startswith("no vlan participation") for o in out), out)
from app import scheduler  # noqa: E402

check("EdgeSwitch: el syslog de un comando de configuración cuenta como cambio",
      bool(scheduler.CHANGE_PATTERNS.search("<13> Nov  9 16:41:53 SW CMDLOGGER[emWeb]: cmd_logger_api.c(83) 3681 %% CLI:10.0.0.5:ubnt:vlan 250")))
check("y una consulta o el cambio de modo no",
      not any(scheduler.CHANGE_PATTERNS.search(f"CMDLOGGER[emWeb]: cmd_logger_api.c(83) 1 %% CLI:10.0.0.5:ubnt:{c}")
              for c in ("show running-config", "terminal length 0", "enable", "configure", "vlan database", "exit",
                        "User ubnt logged in to enable mode.")))
fp = services.fastpath_modes(["enable", "ip ssh server enable", "ip http secure-server", "no ip telnet server enable", "no ip http server"])
check("EdgeSwitch: «ip ssh server», «ip http …» e «ip telnet server» van en el modo privilegiado",
      fp == ["exit", "ip ssh server enable", "ip http secure-server", "no ip telnet server enable", "no ip http server"], fp)


check("EdgeSwitch: «Signature verification (ssh-rsa) failed» al entrar es pasajero y se reintenta",
      bool(services.TRANSIENT_SSH.search("SSHException: Signature verification (ssh-rsa) failed.")))
check("y una contraseña mala no", not services.TRANSIENT_SSH.search("AuthenticationException: Authentication failed."))


# ---- cumplimiento del EdgeSwitch: FastPath no muestra lo que viene de fábrica (configuración real de un ES-16-150W)
from app import compliance_packs  # noqa: E402

ES_CFG = ('!System Description "EdgeSwitch 16 150W, 1.9.3"\nhostname "UBNT EdgeSwitch 3"\nvlan database\nvlan 10,20\nexit\n'
          'configure\nusername "ubnt" password abc level 15 encrypted\nline ssh\nexit\nsnmp-server sysname "SW"\nexit\n')
rules = {r[0]: r for r in compliance_packs.PACKS["Ubiquiti EdgeSwitch/UniFi · Seguridad básica"][2]}


def passes(name, cfg):
    _n, kind, pat, _s, _r = rules[name]
    hit = re.search(pat, cfg, re.M) is not None
    return hit if kind == "must_contain" else not hit


check("EdgeSwitch de fábrica: el SSH (encendido, sin línea) cumple", passes("SSH habilitado", ES_CFG))
check("y apagado («no ip ssh server enable») no", not passes("SSH habilitado", ES_CFG + "no ip ssh server enable\n"))
check("EdgeSwitch de fábrica: la web por HTTP (encendida, sin línea) no cumple «Sin HTTP sin cifrar»", not passes("Sin HTTP sin cifrar", ES_CFG))
check("y con «no ip http server» cumple", passes("Sin HTTP sin cifrar", ES_CFG + "no ip http server\n"))
check("EdgeSwitch de fábrica: el SNTP (unicast con el servidor de fábrica, sin línea) cumple", passes("Sincronización horaria (SNTP)", ES_CFG))
fixes = compliance_packs.PATTERN_FIXES
check("las reglas ya instaladas con la condición vieja se corrigen a la nueva",
      all((rules[k[1]][1], rules[k[1]][2]) == v[1] for k, v in fixes.items()))


class LateConn:
    """La respuesta del comando llega después de un prompt atrasado (el de «configure terminal»)."""

    def __init__(self, first, rest):
        self.first, self.rest = first, rest

    def send_command(self, *_a, **_k):
        return self.first

    def read_until_pattern(self, pattern, read_timeout=0):
        if not re.search(pattern, self.rest):
            raise TimeoutError
        return self.rest


late = LateConn("Y-1:1(config)#", "\nvlan create 199 name labvolver type port-mstprstp 0\n\n"
                "Error: The specified VLAN ID is invalid or already in use \n\nY-1:1(config)#")
r = services._late_prompt(late, "vlan create 199 name labvolver type port-mstprstp 0", late.first)
check("VOSS: un prompt atrasado no se toma como la respuesta; el rechazo que llega después se ve",
      bool(services.DEVICE_ERROR.search(r)), r)
ok = LateConn("R1(config)#\ninterface Loopback1\nR1(config-if)#", "")
check("y una respuesta normal no espera de más", services._late_prompt(ok, "interface Loopback1", ok.first) == ok.first)
check("ni un prompt solo de un equipo que no repite el comando (se queda con lo leído)",
      services._late_prompt(LateConn("R1(config)#", "nada"), "hostname R1", "R1(config)#") == "R1(config)#")

cur = "interface Loopback10\n description DATOS\n no ip address\n"
tgt = "interface Loopback10\n description DATOS\n ip address 192.168.10.1 255.255.255.0\n"
out = rollback.build_rollback(cur, tgt, IOS)
check("IOS: un «no ip address» que se reemplaza no se niega («ip address» sola)", " ip address" not in out
      and " ip address 192.168.10.1 255.255.255.0" in out, out)

out = rollback.build_rollback("hostname S-2\naaa new-model\nusername admin privilege 15 secret 9 x\n", "hostname S-1\n", IOS)
check("IOS: restaurar no manda «no aaa new-model» (IOS no lo permite y pide confirmar)", "no aaa new-model" not in out and "no aaa session-id common" not in out, out)
from app import rollback_vendor  # noqa: E402

cmds, _n = rollback_vendor.voss('snmp-server name "V-2"\nbanner custom\nbanner "Acceso solo para personal autorizado"\n',
                                'snmp-server name "V-1"\n')
check("VOSS: el banner que sobra se quita con un solo «no banner» (no existe «no banner custom»)",
      cmds.count("no banner") == 1 and not any("banner custom" in c for c in cmds), cmds)

neg = rollback_vendor._voss_negate("radius server host 10.0.0.250 key ****** ")
check("VOSS: el RADIUS que sobra se quita sin la clave enmascarada y con «used-by»",
      neg == "no radius server host 10.0.0.250 used-by cli", neg)

cur = ('snmp-server name "V-1"\nradius server host 10.0.0.250 key ****** \nradius enable\nntp server 162.159.200.1\n'
       "interface GigabitEthernet 1/5\nname Puesto-1\nexit\n")
cmds, _n = rollback_vendor.voss(cur, 'snmp-server name "V-2"\nntp server 162.159.200.1\ninterface GigabitEthernet 1/5\nname Puesto-1\nexit\n')
check("VOSS: restaurar en otro quita el RADIUS que sobra con «used-by» y sin la clave (no es un bloque)",
      "no radius server host 10.0.0.250 used-by cli" in cmds and "no radius enable" in cmds
      and not any("key" in c for c in cmds) and not any("ntp" in c for c in cmds), cmds)

from app import topology  # noqa: E402

nb = topology.parse("Local Interface    Parent Interface    Chassis Id          Port info          System Name\n"
                    "ge-0/0/0           -                   2c:6b:f5:aa:bb:cc   ge-0/0/0           F-2\n\n"
                    "# commit confirmed will be rolled back in 5 minutes\n", "juniper_junos")
check("Junos: el aviso «# commit confirmed …» en la salida de LLDP no es un vecino", [n["remote_name"] for n in nb] == ["F-2"], nb)

# --- 2.34: pfSense y OPNsense por separado, recetas de OPNsense y WatchGuard probadas, configlets nuevos
from app import consolecore as cc  # noqa: E402
from app import recipes  # noqa: E402
from app.db import Configlet, SessionLocal, init_db  # noqa: E402

init_db()
check("pfSense y OPNsense son plataformas distintas", cc.os_of("pfsense") == "pfsense" and cc.os_of("opnsense") == "opnsense"
      and cc.OS_LABEL["pfsense"] == "pfSense" and cc.OS_LABEL["opnsense"] == "OPNsense" and "bsdfw" not in cc.OS_LABEL)
opn = {k: r for k, r in recipes.BY_KEY.items() if k.startswith("opn-")}
pfs = {k: r for k, r in recipes.BY_KEY.items() if k.startswith("pfs-")}
check("OPNsense y pfSense tienen sus 11 recetas cada uno, sin recetas compartidas",
      len(opn) == 11 and len(pfs) == 11 and not any(k.startswith("bsd-") for k in recipes.BY_KEY)
      and all(r["os"] == "opnsense" for r in opn.values()) and all(r["os"] == "pfsense" for r in pfs.values()))
cmds = lambda rs: "\n".join(s["c"] for r in rs.values() for s in r["steps"])  # noqa: E731
check("Cada uno con sus rutas: config.xml, versión y registro",
      "/conf/config.xml" in cmds(opn) and "/cf/" not in cmds(opn) and "opnsense-version" in cmds(opn)
      and "/cf/conf/config.xml" in cmds(pfs) and "opnsense" not in cmds(pfs) and "/etc/version" in cmds(pfs))
check("Las 11 recetas de OPNsense tienen el sello de OPNsense 26.7", all(recipes.verified_on(k) == "OPNsense 26.7" for k in opn))
for c in ("ifconfig", "ifconfig -a", "ifconfig vtnet0", "sockstat -4 -l", "tail -n 100 /var/log/system/latest.log",
          "ls -l /root/config-antes.xml", "pfctl -sr", "pfctl -si", "arp -an", "opnsense-version", "netstat -rn", "df -h"):
    check(f"OPNsense / pfSense: «{c}» es una consulta (la consola y Multi-equipo no lo bloquean)",
          cc.risk("opnsense", c, "opnsense") == "read" and cc.risk("pfsense", c, "pfsense") == "read")
for c, k in (("ifconfig vtnet0 down", "change"), ("cp /conf/config.xml /root/x", "change"), ("pfctl -e", "change"),
             ("sysctl net.inet.ip.forwarding=1", "change"), ("pfctl -d", "outage"), ("reboot", "outage")):
    check(f"OPNsense / pfSense: «{c}» es {k}", cc.risk("opnsense", c, "opnsense") == k, cc.risk("opnsense", c, "opnsense"))

fw = {k: "\n".join(s["c"] for s in r["steps"]) for k, r in recipes.BY_KEY.items() if k.startswith("fireware-")}
check("Fireware: nombre con «system name» en una línea (no hay submodo «system»)",
      "system name {{nombre}}" in fw["fireware-hostname"] and "\nsystem\n" not in fw["fireware-hostname"])
check("Fireware: comandos que existen en 12.8 (sin «show log», «show connection» ni «show system usage»)",
      "show log-cache tail" in fw["fireware-logs"] and fw["fireware-sessions"].strip().endswith("show connection count")
      and "show system usage" not in fw["fireware-cpu"] and "show login-user" in fw["fireware-users"])
check("Las 12 recetas de WatchGuard tienen el sello de Fireware 12.8.1",
      len(fw) == 12 and all(recipes.verified_on(k) == "Fireware 12.8.1" for k in fw))
check("Fireware: el ping de la receta termina (-c 4)", "ping -c 4 {{destino}}" in fw["fireware-ping"])
check("Fireware: la receta de NTP avisa del máximo de 3 servidores",
      any("3 servidores NTP" in s["t"] for s in recipes.BY_KEY["fireware-ntp"]["steps"]))

from app import configlets_seed  # noqa: E402
from app.drivers import PROFILES  # noqa: E402

items = configlets_seed.CONFIGLETS_234
check("50 configlets nuevos, 10 por plataforma, con nombre único",
      len(items) == 50 and len({i[0] for i in items}) == 50
      and all(sum(1 for i in items if i[2] == f) == 10 for f in {i[2] for i in items}) and len({i[2] for i in items}) == 5)
check("El filtro de cada configlet sirve a un tipo de equipo existente",
      all(any(re.search(i[2], p.key) for p in PROFILES) for i in items))
check("Variables con la sintaxis de los configlets ({{ variable }})",
      all(re.findall(r"\{\{ \w+ \}\}", i[4]) or "{{" not in i[4] for i in items) and not any(re.search(r"\{\{\w", i[4]) for i in items))
with SessionLocal() as db:
    n1 = configlets_seed.seed_new_configlets(db)
    db.delete(db.query(Configlet).filter(Configlet.name == items[0][0]).one())
    db.commit()
    n2 = configlets_seed.seed_new_configlets(db)
    left = db.query(Configlet).count()
check("Los configlets nuevos llegan una vez; uno borrado no vuelve", n1 == 50 and n2 == 0 and left == 49, (n1, n2, left))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
