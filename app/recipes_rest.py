"""Recetas de las plataformas que no tenían (firewalls, SD-WAN, transporte, OLT, routers japoneses y otros).

Los comandos salen de la referencia de comandos de cada fabricante y de los controladores de netmiko (modo de
configuración y comando para guardar de cada uno). Ninguna se probó en un equipo: todas salen «Sin verificar».
Usa las mismas tareas que el generador (app.recipes_gen) para que la vista «Por tarea» las junte.
"""
from __future__ import annotations

from .i18n import N_
from .recipes_gen import family

NOTE_SYNTAX = N_("La sintaxis puede cambiar entre versiones: si un comando da error, escriba «?» para ver la exacta en su equipo.")


def ports(default_on: str, default_off: str | None = None) -> dict:
    """Variables de las tareas de puerto con el nombre de puerto típico de la plataforma."""
    off = default_off or default_on
    return {"desc": [("puerto", N_("Puerto"), default_on), ("desc", N_("Descripción"), "Uplink")],
            "shutdown": [("puerto", N_("Puerto"), off)],
            "access": [("puerto", N_("Puerto"), default_on), ("vlan", N_("VLAN"), "10")]}


REST = []

# Sophos Firewall (SFOS): consola del equipo (menú 4). La configuración se hace en la interfaz web.
REST += family("sfos", ("Sophos", "sfos", "Sophos Firewall (SFOS)"), {
    "version": ("system diagnostics show version-info", ""),
    "cpu": ("system diagnostics show cpu", ""),
    "disk": ("system diagnostics show disk", ""),
    "arp": ("system diagnostics utilities arp", ""),
    "time": ("show date", ""),
    "ha": ("system ha show details", ""),
    "ping": ("ping {{destino}}", ""),
    "trace": ("traceroute {{destino}}", ""),
    "dnsq": ("dnslookup host {{host}}", ""),
    "capture": ("tcpdump 'host {{destino}}'", ""),
}, note=N_("Comandos de la consola del equipo (menú 4, «Device Console»). El resto de la configuración se hace en la interfaz web."))

# Cisco SD-WAN vEdge (Viptela): CLI de tipo ConfD, los cambios se aplican con «commit».
REST += family("viptela", ("Cisco", "viptela", "Cisco SD-WAN vEdge"), {
    "hostname": ("config\nsystem host-name {{nombre}}\ncommit and-quit", "show running-config system host-name"),
    "user": ("config\nsystem aaa user {{usuario}} password {{clave}} group netadmin\ncommit and-quit", "show running-config system aaa"),
    "syslog": ("config\nsystem logging server {{opennccm}}\ncommit and-quit", "show running-config system logging"),
    "ntp": ("config\nsystem ntp server {{ntp}}\ncommit and-quit", "show ntp associations"),
    "desc": ("config\nvpn 0 interface {{puerto}} description \"{{desc}}\"\ncommit and-quit", "show interface description"),
    "shutdown": ("config\nvpn 0 interface {{puerto}} shutdown\ncommit and-quit", "show interface {{puerto}}"),
    "route": ("config\nvpn 0 ip route {{red}} {{salto}}\ncommit and-quit", "show ip routes vpn 0"),
    "ports": ("show interface", ""), "version": ("show version", ""), "routes": ("show ip routes", ""),
    "arp": ("show arp", ""), "config": ("show running-config", ""), "ping": ("ping {{destino}}", ""),
}, note=N_("Si el equipo lo administra vManage con una plantilla, haga el cambio en la plantilla: un cambio local se pierde en el próximo envío."),
   vars_override=ports("ge0/0", "ge0/3"))

# Brocade Fabric OS (switches Fibre Channel): los cambios se guardan solos, salvo la zonificación (cfgsave).
REST += family("bfos", ("Brocade", "bfos", "Brocade Fabric OS"), {
    "hostname": ("switchname \"{{nombre}}\"", "switchname"),
    "user": ("userconfig --add {{usuario}} -r admin", "userconfig --show {{usuario}}"),
    "syslog": ("syslogadmin --set -ip {{opennccm}}", "syslogadmin --show -ip"),
    "ntp": ("tsclockserver \"{{ntp}}\"", "tsclockserver"),
    "desc": ("portname {{puerto}} -n \"{{desc}}\"", "portname {{puerto}}"),
    "shutdown": ("portdisable {{puerto}}", "portshow {{puerto}}"),
    "ports": ("switchshow", ""), "version": ("version", ""), "logs": ("errdump", ""), "config": ("configshow", ""),
    "time": ("date", ""), "ping": ("ping {{destino}}", ""),
}, vars_override={"user": [("usuario", N_("Usuario"), "admin2")], **ports("1", "8")},
   note=N_("Fabric OS guarda los cambios solo. «userconfig --add» pide la contraseña nueva al ejecutarse."))

# Extreme WiNG (Motorola/Zebra): la configuración del equipo va en «self» y «commit write memory» aplica y guarda.
REST += family("wing", ("Extreme", "wing", "Extreme WiNG"), {
    "hostname": ("configure terminal\nself\nhostname {{nombre}}\nexit", "show running-config | include hostname"),
    "user": ("configure terminal\nmanagement-policy default\nuser {{usuario}} password 0 {{clave}} role superuser access all\nexit",
             "show running-config | include user"),
    "snmp": ("configure terminal\nmanagement-policy default\nsnmp-server community 0 {{comunidad}} ro\nexit",
             "show running-config | include snmp"),
    "syslog": ("configure terminal\nself\nlogging on\nlogging host {{opennccm}}\nexit", "show running-config | include logging"),
    "ntp": ("configure terminal\nself\nntp server {{ntp}}\nexit", "show ntp associations"),
    "ports": ("show interface brief", ""), "version": ("show version", ""), "logs": ("show logging", ""),
    "lldp": ("show lldp neighbors", ""), "config": ("show running-config", ""), "ping": ("ping {{destino}}", ""),
    "save": ("commit write memory", ""),
}, save="commit write memory", note=N_("Si el equipo toma la configuración de un perfil, el cambio en «self» la reemplaza solo en este equipo."))

# ADVA FSP 150CC (F2 y F3, sistema AOS)
REST += family("adva", ("ADVA", "adva", "ADVA FSP 150CC"), {
    "ports": ("show ports", ""), "version": ("show version", ""), "config": ("show running-config", ""),
    "alarms": ("show alarms", ""), "lldp": ("show lldp neighbors", ""), "routes": ("show route", ""),
    "logs": ("show log", ""), "time": ("show time", ""), "users": ("show users", ""),
    "ping": ("ping {{destino}}", ""), "trace": ("traceroute {{destino}}", ""),
}, note=NOTE_SYNTAX)

# Ciena SAOS 6 y 8
REST += family("saos", ("Ciena", "saos", "Ciena SAOS 6/8"), {
    "hostname": ("system set host-name {{nombre}}", "system show host-name"),
    "user": ("user create user {{usuario}} access-level super password {{clave}}", "user show"),
    "syslog": ("syslog create collector {{opennccm}}", "syslog show collector"),
    "ntp": ("ntp client add server {{ntp}}", "ntp client show"),
    "vlan": ("vlan create vlan {{vlan}} name {{nombre}}", "vlan show vlan {{vlan}}"),
    "access": ("vlan add vlan {{vlan}} port {{puerto}}", "vlan show vlan {{vlan}}"),
    "desc": ("port set port {{puerto}} description \"{{desc}}\"", "port show port {{puerto}}"),
    "shutdown": ("port disable port {{puerto}}", "port show port {{puerto}}"),
    "ports": ("port show", ""), "version": ("software show", ""), "lldp": ("lldp show neighbors", ""),
    "alarms": ("alarm show", ""), "config": ("configuration show", ""), "ping": ("ping {{destino}}", ""),
    "save": ("configuration save", ""),
}, save="configuration save", vars_override=ports("1", "8"))

# Ciena SAOS 10 (CLI de tipo ConfD/YANG, con «commit»)
REST += family("saos10", ("Ciena", "saos10", "Ciena SAOS 10"), {
    "hostname": ("config\nsystem config hostname {{nombre}}\ncommit\nend", "show running-config system"),
    "ntp": ("config\nsystem ntp server {{ntp}}\ncommit\nend", "show ntp"),
    "desc": ("config\ninterfaces interface {{puerto}} description \"{{desc}}\"\ncommit\nend", "show interfaces"),
    "ports": ("show interfaces", ""), "version": ("show software", ""), "config": ("show running-config", ""),
    "alarms": ("show alarms", ""), "lldp": ("show lldp neighbors", ""), "logs": ("show logging", ""),
    "routes": ("show ip route", ""), "ping": ("ping {{destino}}", ""),
}, note=NOTE_SYNTAX, vars_override=ports("1"))

# Calix E-Series (EXA): CLI de tipo ConfD
REST += family("calixexa", ("Calix", "calixexa", "Calix E-Series (EXA)"), {
    "hostname": ("configure\nhostname {{nombre}}\nend", "show running-config hostname"),
    "ntp": ("configure\nntp server 1 {{ntp}}\nend", "show ntp"),
    "syslog": ("configure\nlogging host {{opennccm}}\nend", "show running-config logging"),
    "desc": ("configure\ninterface ethernet {{puerto}}\ndescription \"{{desc}}\"\nend", "show interface ethernet {{puerto}}"),
    "shutdown": ("configure\ninterface ethernet {{puerto}}\nshutdown\nend", "show interface ethernet {{puerto}}"),
    "ports": ("show interface ethernet status", ""), "version": ("show version", ""), "config": ("show running-config", ""),
    "alarms": ("show alarm active", ""), "lldp": ("show lldp neighbor", ""), "ping": ("ping {{destino}}", ""),
    "save": ("copy running-config startup-config", ""),
}, save="copy running-config startup-config", note=NOTE_SYNTAX, vars_override=ports("1/1/x1", "1/1/x8"))

# Ericsson IPOS (Redback/SmartEdge): los cambios se aplican con «commit».
REST += family("ipos", ("Ericsson", "ipos", "Ericsson IPOS"), {
    "hostname": ("configure\nsystem hostname {{nombre}}\ncommit\nend", "show configuration system"),
    "syslog": ("configure\nlogging syslog {{opennccm}}\ncommit\nend", "show configuration logging"),
    "ntp": ("configure\nntp\nserver {{ntp}}\ncommit\nend", "show ntp associations"),
    "route": ("configure\ncontext local\nip route {{red}} {{salto}}\ncommit\nend", "show ip route static"),
    "ports": ("show port", ""), "version": ("show version", ""), "routes": ("show ip route", ""),
    "arp": ("show ip arp", ""), "logs": ("show log", ""), "config": ("show configuration", ""),
    "users": ("show users", ""), "ping": ("ping {{destino}}", ""), "save": ("save configuration", ""),
}, note=NOTE_SYNTAX)

# Ericsson MINI-LINK 6352/63xx y 6600: «config» entra en configuración y «write» guarda.
REST += family("mltn", ("Ericsson", "mltn", "Ericsson MINI-LINK"), {
    "hostname": ("config\nhostname {{nombre}}\nexit", "show running-config | include hostname"),
    "syslog": ("config\nrsyslog enable\nrsyslog server 1 {{opennccm}} 514\nexit", "show running-config | include rsyslog"),
    "ntp": ("config\nntp server {{ntp}}\nexit", "show ntp"),
    "ports": ("show interface status", ""), "version": ("show version", ""), "config": ("show running-config", ""),
    "alarms": ("show alarms", ""), "lldp": ("show lldp neighbors", ""), "logs": ("show log", ""),
    "routes": ("show ip route", ""), "ping": ("ping {{destino}}", ""), "save": ("write", ""),
}, save="write", note=NOTE_SYNTAX)

# Coriant 8600 (sin modo de configuración en el controlador: solo consultas)
REST += family("coriant", ("Coriant", "coriant", "Coriant 8600"), {
    "ports": ("show interface", ""), "version": ("show version", ""), "config": ("show running-config", ""),
    "routes": ("show ip route", ""), "arp": ("show arp", ""), "alarms": ("show alarm", ""), "logs": ("show log", ""),
    "lldp": ("show lldp neighbors", ""), "time": ("show clock", ""), "users": ("show users", ""),
    "ping": ("ping {{destino}}", ""),
}, note=NOTE_SYNTAX)

# Nokia ISAM (OLT 7302/7330/7360): «configure» y «admin save» guarda.
REST += family("isam", ("Nokia", "isam", "Nokia ISAM"), {
    "hostname": ("configure system id {{nombre}}", "info configure system"),
    "ntp": ("configure system sntp server-ip-addr {{ntp}}\nconfigure system sntp enable", "show system sntp"),
    "syslog": ("configure system syslog destination opennccm type udp:{{opennccm}}:514", "info configure system syslog"),
    "version": ("show software-mngt oswp", ""), "alarms": ("show alarm current table", ""),
    "config": ("info configure flat", ""), "ports": ("show equipment slot", ""), "time": ("show system time", ""),
    "users": ("show session", ""), "ping": ("ping {{destino}}", ""), "save": ("admin save", ""),
}, save="admin save", note=NOTE_SYNTAX)

# D-Link DES/DGS/DXS (CLI clásica de D-Link)
REST += family("dlink", ("D-Link", "dlink", "D-Link DES/DGS/DXS"), {
    "hostname": ("config snmp system_name {{nombre}}", "show switch"),
    "user": ("create account admin {{usuario}}", "show account"),
    "ssh": ("enable ssh\ndisable telnet", "show ssh server"),
    "snmp": ("create snmp community {{comunidad}} view CommunityView read_only", "show snmp community"),
    "syslog": ("create syslog host 1 ipaddress {{opennccm}} state enable\nenable syslog", "show syslog host"),
    "ntp": ("config sntp primary {{ntp}}\nconfig time_zone operator + hour 0\nenable sntp", "show sntp"),
    "vlan": ("create vlan {{nombre}} tag {{vlan}}", "show vlan"),
    "access": ("config vlan default delete {{puerto}}\nconfig vlan vlanid {{vlan}} add untagged {{puerto}}", "show vlan"),
    "desc": ("config ports {{puerto}} description {{desc}}", "show ports {{puerto}} description"),
    "shutdown": ("config ports {{puerto}} state disable", "show ports {{puerto}}"),
    "route": ("create iproute {{red}} {{salto}}", "show iproute"),
    "ports": ("show ports", ""), "mac": ("show fdb", ""), "version": ("show switch", ""), "logs": ("show log", ""),
    "ping": ("ping {{destino}}", ""), "save": ("save", ""),
}, save="save", vars_override={**ports("1:1", "1:8"), "user": [("usuario", N_("Usuario"), "admin2")],
                               "route": [("red", N_("Red de destino"), "0.0.0.0/0"), ("salto", N_("Próximo salto"), "192.168.1.254")]},
   note=N_("«create account» pide la contraseña al ejecutarse. En los modelos con puertos de una sola unidad, el puerto es «1» en vez de «1:1»."))

# Belden Hirschmann HiOS: «enable» y «configure»; «save» graba en la memoria del equipo.
REST += family("hios", ("Hirschmann", "hios", "Hirschmann HiOS"), {
    "hostname": ("enable\nconfigure\nsystem name {{nombre}}\nexit", "show system info"),
    "syslog": ("enable\nconfigure\nlogging host add 1 addr {{opennccm}}\nlogging syslog operation\nexit", "show logging host"),
    "ntp": ("enable\nconfigure\nsntp client server add 1 {{ntp}}\nsntp client operation\nexit", "show sntp client server"),
    "vlan": ("enable\nvlan database\nvlan add {{vlan}}\nname {{vlan}} {{nombre}}\nexit", "show vlan brief"),
    "access": ("enable\nconfigure\ninterface {{puerto}}\nvlan participation include {{vlan}}\nvlan tagging {{vlan}} disable\nvlan pvid {{vlan}}\nexit\nexit",
               "show vlan port"),
    "desc": ("enable\nconfigure\ninterface {{puerto}}\nname \"{{desc}}\"\nexit\nexit", "show port all"),
    "shutdown": ("enable\nconfigure\ninterface {{puerto}}\nshutdown\nexit\nexit", "show port all"),
    "ports": ("show port all", ""), "mac": ("show mac-addr-table", ""), "version": ("show system info", ""),
    "logs": ("show logging buffered", ""), "lldp": ("show lldp remote-data", ""), "ping": ("ping {{destino}}", ""),
    "save": ("save", ""),
}, save="save", vars_override=ports("1/1", "1/8"))

# Yamaha RTX: «administrator» da permisos de cambio y «save» graba.
REST += family("yamaha", ("Yamaha", "yamaha", "Yamaha RTX"), {
    "hostname": ("console prompt {{nombre}}", "show config | grep console"),
    "user": ("login user {{usuario}} {{clave}}\nuser attribute {{usuario}} administrator=on", "show config | grep user"),
    "syslog": ("syslog host {{opennccm}}\nsyslog notice on", "show config | grep syslog"),
    "ntp": ("ntpdate {{ntp}}\nschedule at 1 */* 03:00 * ntpdate {{ntp}}", "show environment"),
    "dns": ("dns server {{dns}}", "show config | grep dns"),
    "route": ("ip route {{red}} gateway {{salto}}", "show ip route"),
    "desc": ("description {{puerto}} \"{{desc}}\"", "show config | grep description"),
    "ports": ("show status lan1", ""), "version": ("show environment", ""), "routes": ("show ip route", ""),
    "arp": ("show arp", ""), "logs": ("show log", ""), "ping": ("ping {{destino}}", ""), "save": ("save", ""),
}, save="save", note=N_("Primero escriba «administrator» y la contraseña de administrador: los comandos de cambio la necesitan."),
   vars_override={"desc": [("puerto", N_("Interfaz"), "lan1"), ("desc", N_("Descripción"), "Uplink")],
                  "route": [("red", N_("Red de destino"), "default"), ("salto", N_("Próximo salto"), "192.168.1.254")]})

# IIJ SEIL
REST += family("seil", ("IIJ", "seil", "IIJ SEIL"), {
    "hostname": ("hostname {{nombre}}", "show config | grep hostname"),
    "syslog": ("syslog remote on\nsyslog add {{opennccm}}", "show config | grep syslog"),
    "ntp": ("ntp enable\nntp server add {{ntp}}", "show status ntp"),
    "dns": ("resolver enable\nresolver address add {{dns}}", "show config | grep resolver"),
    "route": ("route add {{red}} {{salto}}", "show status route"),
    "ports": ("show status interface", ""), "version": ("show system", ""), "routes": ("show status route", ""),
    "arp": ("show arp", ""), "logs": ("show log", ""), "ping": ("ping {{destino}}", ""), "save": ("save-to flashrom", ""),
}, save="save-to flashrom", note=NOTE_SYNTAX)

# Fujitsu Si-R: «admin» y «configure»; «commit» aplica y guarda.
REST += family("sir", ("Fujitsu", "sir", "Fujitsu Si-R"), {
    "hostname": ("admin\nconfigure\nsysname {{nombre}}\nend", "show candidate-config | grep sysname"),
    "syslog": ("admin\nconfigure\nsyslog server 0 address {{opennccm}}\nend", "show candidate-config | grep syslog"),
    "ntp": ("admin\nconfigure\ntime auto server {{ntp}}\nend", "show candidate-config | grep time"),
    "dns": ("admin\nconfigure\nresolver address {{dns}}\nend", "show candidate-config | grep resolver"),
    "ports": ("show ether", ""), "version": ("show version", ""), "routes": ("show ip route", ""),
    "arp": ("show arp", ""), "logs": ("show logging syslog", ""), "config": ("show candidate-config", ""),
    "ping": ("ping {{destino}}", ""), "save": ("admin\ncommit", ""),
}, save="commit", note=NOTE_SYNTAX)

# Eltex ESR: «configure», y los cambios se aplican con «commit» y se confirman con «confirm».
REST += family("esr", ("Eltex", "esr", "Eltex ESR"), {
    "hostname": ("configure\nhostname {{nombre}}\nend", "show running-config | include hostname"),
    "user": ("configure\nusername {{usuario}}\npassword {{clave}}\nprivilege 15\nexit\nend", "show running-config | include username"),
    "syslog": ("configure\nsyslog host opennccm\nremote-address {{opennccm}}\nexit\nend", "show running-config | include syslog"),
    "ntp": ("configure\nntp enable\nntp server {{ntp}}\nexit\nend", "show ntp peers"),
    "dns": ("configure\ndomain name-server {{dns}}\nend", "show running-config | include name-server"),
    "desc": ("configure\ninterface {{puerto}}\ndescription \"{{desc}}\"\nexit\nend", "show interfaces description"),
    "shutdown": ("configure\ninterface {{puerto}}\nshutdown\nexit\nend", "show interfaces status"),
    "route": ("configure\nip route {{red}} {{salto}}\nend", "show ip route static"),
    "ports": ("show interfaces status", ""), "version": ("show version", ""), "routes": ("show ip route", ""),
    "arp": ("show arp", ""), "logs": ("show syslog", ""), "ping": ("ping {{destino}}", ""),
    "save": ("commit\nconfirm", ""),
}, save="commit\nconfirm", note=N_("Después de «commit» hay unos minutos para escribir «confirm»: si no, el equipo vuelve a la configuración anterior."),
   vars_override=ports("gigabitethernet 1/0/1", "gigabitethernet 1/0/8"))

# Ekinops 360 (transporte óptico): solo consultas
REST += family("ekinops", ("Ekinops", "ekinops", "Ekinops 360"), {
    "ports": ("show interface", ""), "version": ("show version", ""), "config": ("show running-config", ""),
    "alarms": ("show alarms", ""), "logs": ("show log", ""), "time": ("show clock", ""), "users": ("show users", ""),
    "routes": ("show ip route", ""), "arp": ("show arp", ""), "lldp": ("show lldp neighbors", ""),
    "ping": ("ping {{destino}}", ""),
}, note=NOTE_SYNTAX)

# Teldat (CIT): «p 4» entra en configuración, «save yes» guarda; «p 3» es la consola de monitorización.
REST += family("teldat", ("Teldat", "teldat", "Teldat"), {
    "hostname": ("p 4\nset hostname {{nombre}}\nexit", "p 4\nshow config"),
    "syslog": ("p 4\nevent\nsyslog-server {{opennccm}}\nexit\nexit", "p 4\nshow config"),
    "ntp": ("p 4\nfeature sntp\nprotocol unicast\nserver {{ntp}}\nexit\nexit", "p 4\nshow config"),
    "route": ("p 4\nprotocol ip\nroute {{red}} {{salto}} 1\nexit\nexit", "p 3\nprotocol ip\nlist routes"),
    "version": ("p 3\nsystem\nshow version", ""), "config": ("p 4\nshow config", ""),
    "routes": ("p 3\nprotocol ip\nlist routes", ""), "logs": ("p 2", ""), "ports": ("p 3\nconfiguration", ""),
    "ping": ("p 3\nprotocol ip\nping {{destino}}", ""), "save": ("p 4\nsave yes", ""),
}, save="save yes", note=N_("Teldat trabaja por procesos: «p 4» es la configuración, «p 3» la monitorización y «p 2» los eventos. «Ctrl+P» vuelve al inicio."),
   vars_override={"route": [("red", N_("Red y máscara"), "0.0.0.0 0.0.0.0"), ("salto", N_("Próximo salto"), "192.168.1.254")]})

# Digi TransPort: comandos de una línea; «config 0 save» graba.
REST += family("digi", ("Digi", "digi", "Digi TransPort"), {
    "hostname": ("cmd 0 hostname {{nombre}}", "config c show"),
    "user": ("user 1 name {{usuario}}\nuser 1 password {{clave}}\nuser 1 access 0", "config c show"),
    "syslog": ("log 0 syslog_ip {{opennccm}}", "config c show"),
    "ntp": ("sntp 0 server {{ntp}}", "config c show"),
    "route": ("route 0 ipaddr {{red}}\nroute 0 mask 0.0.0.0\nroute 0 gateway {{salto}}", "route"),
    "version": ("ati5", ""), "config": ("config c show", ""), "routes": ("route", ""), "arp": ("arp", ""),
    "logs": ("type eventlog.txt", ""), "ping": ("ping {{destino}}", ""), "save": ("config 0 save", ""),
}, save="config 0 save", note=NOTE_SYNTAX,
   vars_override={"route": [("red", N_("Red de destino"), "0.0.0.0"), ("salto", N_("Próximo salto"), "192.168.1.254")]})

# Siemens Garderos GRS: «configuration terminal», «commit» y «write startup-configuration» guarda.
REST += family("garderos", ("Siemens", "garderos", "Siemens Garderos GRS"), {
    "hostname": ("configuration terminal\nsystem hostname {{nombre}}\ncommit\nexit", "show configuration running"),
    "ntp": ("configuration terminal\nsystem ntp server {{ntp}}\ncommit\nexit", "show configuration running"),
    "syslog": ("configuration terminal\nsystem syslog server {{opennccm}}\ncommit\nexit", "show configuration running"),
    "version": ("show version", ""), "config": ("show configuration running", ""), "ports": ("show interface", ""),
    "routes": ("show ip route", ""), "arp": ("show arp", ""), "logs": ("show log", ""), "ping": ("ping {{destino}}", ""),
    "save": ("write startup-configuration", ""),
}, save="write startup-configuration", note=NOTE_SYNTAX)

# AudioCodes Mediant (6.6 y 7.2): «enable», «configure system|network|voip»; «write» guarda.
REST += family("audiocodes", ("AudioCodes", "audiocodes", "AudioCodes Mediant"), {
    "hostname": ("configure system\nsnmp settings\nsys-name {{nombre}}\nactivate\nexit\nexit", "show running-config system"),
    "syslog": ("configure troubleshoot\nsyslog\nsyslog on\nsyslog-ip {{opennccm}}\nactivate\nexit\nexit", "show running-config troubleshoot"),
    "ntp": ("configure system\nntp\nprimary-server {{ntp}}\nactivate\nexit\nexit", "show system ntp-status"),
    "version": ("show system version", ""), "config": ("show running-config", ""),
    "ports": ("show network interface", ""), "routes": ("show network route", ""), "arp": ("show network arp", ""),
    "time": ("show system clock", ""), "users": ("show users", ""), "ping": ("ping {{destino}}", ""),
    "save": ("write", ""),
}, save="write", note=N_("Entre con «enable» antes de configurar. En la versión 6.6 algunos menús tienen otro nombre: escriba «?» para verlos."))

# Juniper NetScreen / ScreenOS: «set» cambia, «get» consulta y «save» graba.
REST += family("screenos", ("Juniper", "screenos", "Juniper ScreenOS"), {
    "hostname": ("set hostname {{nombre}}", "get hostname"),
    "user": ("set admin user {{usuario}} password {{clave}} privilege all", "get admin user"),
    "ssh": ("set ssh enable\nunset interface ethernet0/0 manage telnet", "get ssh"),
    "snmp": ("set snmp community {{comunidad}} read-only", "get snmp community"),
    "syslog": ("set syslog config {{opennccm}} facilities local0 local0\nset syslog enable", "get syslog"),
    "ntp": ("set clock ntp\nset ntp server {{ntp}}", "get ntp"),
    "dns": ("set dns host dns1 {{dns}}", "get dns host settings"),
    "route": ("set route {{red}} interface ethernet0/0 gateway {{salto}}", "get route"),
    "ports": ("get interface", ""), "version": ("get system", ""), "routes": ("get route", ""), "arp": ("get arp", ""),
    "logs": ("get log event", ""), "sessions": ("get session", ""), "ping": ("ping {{destino}}", ""), "save": ("save", ""),
}, save="save", note=N_("Cambie «ethernet0/0» por la interfaz de su equipo donde corresponda."))

# Hillstone StoneOS: «configure»; «save all» guarda.
REST += family("stoneos", ("Hillstone", "stoneos", "Hillstone StoneOS"), {
    "hostname": ("configure\nhostname {{nombre}}\nend", "show configuration | include hostname"),
    "user": ("configure\nadmin user {{usuario}}\npassword {{clave}}\nprivilege RXW\nexit\nend", "show admin user"),
    "syslog": ("configure\nlogging syslog {{opennccm}} udp 514 type event\nlogging event to syslog\nend", "show logging syslog"),
    "ntp": ("configure\nntp enable\nntp server {{ntp}}\nend", "show ntp status"),
    "route": ("configure\nip vrouter trust-vr\nip route {{red}} {{salto}}\nexit\nend", "show ip route"),
    "ports": ("show interface", ""), "version": ("show version", ""), "routes": ("show ip route", ""),
    "arp": ("show arp", ""), "logs": ("show logging event", ""), "sessions": ("show session generic", ""),
    "ping": ("ping {{destino}}", ""), "save": ("save all\ny", ""),
}, save="save all\ny", note=NOTE_SYNTAX)

# WatchGuard Fireware: la CLI sirve sobre todo para consultas; los cambios se suelen hacer en Fireware Web UI.
REST += family("fireware", ("WatchGuard", "fireware", "WatchGuard Fireware"), {
    "hostname": ("configure\nsystem\nname {{nombre}}\nexit\nexit", "show sysinfo"),
    "ntp": ("configure\nntp enable\nntp server ip {{ntp}}\nexit", "show ntp"),
    "version": ("show sysinfo", ""), "ports": ("show interface", ""), "routes": ("show route", ""),
    "arp": ("show arp", ""), "logs": ("show log", ""), "sessions": ("show connection", ""),
    "users": ("show users", ""), "cpu": ("show system usage", ""), "ping": ("ping {{destino}}", ""),
    "trace": ("traceroute {{destino}}", ""),
}, note=N_("Fireware guarda los cambios de la CLI al momento. La mayoría de la configuración se hace en Fireware Web UI o WatchGuard Cloud."))

# Prisma SD-WAN (CloudGenix ION): la configuración viene del controlador en la nube; en el equipo solo se consulta.
REST += family("ion", ("Palo Alto", "ion", "Prisma SD-WAN ION"), {
    "version": ("dump software status", ""), "ports": ("dump interface status", ""), "routes": ("dump routing summary", ""),
    "arp": ("dump interface arp", ""), "config": ("dump config", ""), "logs": ("dump log", ""),
    "time": ("dump system time", ""), "sessions": ("dump flow summary", ""), "ping": ("ping {{destino}}", ""),
    "trace": ("traceroute {{destino}}", ""), "dnsq": ("dump dns {{host}}", ""),
}, note=N_("La configuración de un ION se hace en el controlador de Prisma SD-WAN; en el equipo solo hay comandos de consulta."))
