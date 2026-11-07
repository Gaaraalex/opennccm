"""Recetas estándar generadas por familia de CLI: cada familia da sus comandos (según la referencia de comandos del
fabricante) y los textos son comunes a todas, con el nombre de la plataforma («Cambiar el nombre del equipo ({0})»).

Formato de cada tarea: (comando, comando de comprobación). Los comandos usan las variables de la tarea ({{nombre}},
{{usuario}}…). «save» es el comando para guardar (None: la plataforma guarda sola o con commit) y «note», un aviso que se
agrega como primer paso de cada receta de la familia.
"""
from __future__ import annotations

from .i18n import N_
from .recipes_more import R

# tarea: (nombre de la receta, introducción, categoría, variables, texto del paso, comprueba)
TASKS = {
    "hostname": (N_("Cambiar el nombre del equipo ({0})"), N_("Cambia el nombre que aparece en el prompt y en el inventario."), "sistema",
                 [("nombre", N_("Nombre nuevo"), "SW-01")], N_("Poner el nombre.")),
    "user": (N_("Crear un usuario administrador ({0})"), N_("Usuario local con permisos de administración."), "seguridad",
             [("usuario", N_("Usuario"), "admin2"), ("clave", N_("Contraseña"), "")], N_("Crear el usuario.")),
    "ssh": (N_("Dejar la administración solo por SSH ({0})"), N_("Administración solo por canales cifrados."), "seguridad", [],
            N_("Activar SSH y apagar Telnet. Si entra por Telnet, compruebe antes que entra por SSH.")),
    "snmp": (N_("Habilitar SNMP de solo lectura ({0})"), N_("Comunidad de solo lectura para la gestión."), "servicios",
             [("comunidad", N_("Comunidad"), "")], N_("Comunidad de solo lectura.")),
    "syslog": (N_("Enviar el syslog a OpenNCCM ({0})"),
               N_("Con el syslog, OpenNCCM ve cada cambio de configuración al momento y respalda el equipo solo."), "servicios",
               [("opennccm", N_("IP del servidor OpenNCCM"), "10.0.0.10")], N_("Servidor syslog.")),
    "ntp": (N_("Configurar NTP ({0})"), N_("Hora correcta en los registros y los respaldos."), "servicios",
            [("ntp", N_("Servidor NTP (IP)"), "10.0.0.1")], N_("Servidor NTP.")),
    "dns": (N_("Poner los servidores DNS del equipo ({0})"), N_("Servidores DNS que usa el propio equipo (actualizaciones, NTP por nombre)."),
            "servicios", [("dns", N_("DNS"), "1.1.1.1")], N_("Servidor DNS.")),
    "vlan": (N_("Crear una VLAN ({0})"), N_("Crea la VLAN y le pone nombre."), "interfaces",
             [("vlan", N_("VLAN"), "10"), ("nombre", N_("Nombre"), "DATOS")], N_("Crear la VLAN y ponerle nombre.")),
    "access": (N_("Poner un puerto de acceso en una VLAN ({0})"), N_("El puerto pasa a modo acceso en esa VLAN."), "interfaces",
               [("puerto", N_("Puerto"), "1/1"), ("vlan", N_("VLAN"), "10")], N_("Modo acceso y VLAN.")),
    "desc": (N_("Ponerle descripción a un puerto ({0})"), N_("La descripción sale en el mapa, en la ficha y en la lista de puertos."),
             "interfaces", [("puerto", N_("Puerto"), "1/1"), ("desc", N_("Descripción"), "Uplink")], N_("Poner la descripción.")),
    "shutdown": (N_("Apagar un puerto ({0})"), N_("Apaga un puerto sin uso."), "interfaces", [("puerto", N_("Puerto"), "1/8")],
                 N_("Apagar el puerto.")),
    "route": (N_("Agregar una ruta estática ({0})"), N_("Ruta a una red por un próximo salto."), "interfaces",
              [("red", N_("Red de destino"), "0.0.0.0/0"), ("salto", N_("Próximo salto"), "192.168.1.254")], N_("Agregar la ruta.")),
    "ports": (N_("Ver el estado de los puertos ({0})"), N_("Enlace, velocidad y descripción de cada puerto."), "diagnostico", [],
              N_("Estado de los puertos.")),
    "mac": (N_("Buscar en qué puerto está una MAC ({0})"), N_("La tabla de direcciones MAC del equipo."), "diagnostico", [], N_("Tabla MAC.")),
    "version": (N_("Ver la versión y el modelo ({0})"), N_("Firmware, modelo y serie, sin cambiar nada."), "diagnostico", [], N_("Versión.")),
    "routes": (N_("Ver la tabla de rutas ({0})"), N_("Rutas del equipo."), "diagnostico", [], N_("Rutas.")),
    "logs": (N_("Ver el registro del equipo ({0})"), N_("Mensajes recientes del equipo."), "diagnostico", [], N_("Registro.")),
    "ping": (N_("Probar el alcance a una dirección ({0})"), N_("Ping desde el equipo."), "diagnostico",
             [("destino", N_("IP a probar"), "8.8.8.8")], N_("Ping.")),
    "save": (N_("Guardar la configuración ({0})"), N_("Graba la configuración para que no se pierda al reiniciar."), "sistema", [],
             N_("Guardar.")),
    # consultas comunes a muchas plataformas (no cambian nada)
    "config": (N_("Ver la configuración en ejecución ({0})"), N_("La configuración completa que está usando el equipo ahora."),
               "diagnostico", [], N_("Configuración en ejecución.")),
    "arp": (N_("Ver la tabla ARP ({0})"), N_("Qué IP corresponde a cada MAC en las redes del equipo."), "diagnostico", [],
            N_("Tabla ARP.")),
    "lldp": (N_("Ver los vecinos LLDP ({0})"), N_("Los equipos conectados a cada puerto, según LLDP."), "diagnostico", [],
             N_("Vecinos LLDP.")),
    "cpu": (N_("Ver el uso de CPU y memoria ({0})"), N_("Carga del procesador y memoria libre, sin cambiar nada."), "diagnostico", [],
            N_("CPU y memoria.")),
    "alarms": (N_("Ver las alarmas activas ({0})"), N_("Alarmas que el equipo tiene abiertas ahora."), "diagnostico", [],
               N_("Alarmas activas.")),
    "trace": (N_("Rastrear la ruta a una dirección ({0})"), N_("Los saltos por los que pasa el tráfico hasta esa dirección."),
              "diagnostico", [("destino", N_("IP a probar"), "8.8.8.8")], N_("Rastreo de ruta.")),
    "time": (N_("Ver la fecha y la hora ({0})"), N_("La hora del equipo: si está mal, los registros y los respaldos salen con otra hora."),
             "diagnostico", [], N_("Fecha y hora.")),
    "ha": (N_("Ver el estado de alta disponibilidad ({0})"), N_("Qué equipo del par está activo y si están sincronizados."),
           "diagnostico", [], N_("Alta disponibilidad.")),
    "sessions": (N_("Ver las sesiones activas ({0})"), N_("Conexiones que el equipo tiene abiertas ahora."), "diagnostico", [],
                 N_("Sesiones activas.")),
    "users": (N_("Ver quién está conectado al equipo ({0})"), N_("Usuarios con una sesión de administración abierta."),
              "diagnostico", [], N_("Usuarios conectados.")),
    "disk": (N_("Ver el espacio en disco ({0})"), N_("Espacio libre en el almacenamiento del equipo."), "diagnostico", [],
             N_("Espacio en disco.")),
    "dnsq": (N_("Probar la resolución de un nombre ({0})"), N_("Comprueba que el equipo resuelve nombres con sus DNS."),
             "diagnostico", [("host", N_("Nombre a resolver"), "opennccm.com")], N_("Consulta DNS.")),
    "capture": (N_("Capturar el tráfico de una dirección ({0})"), N_("Muestra los paquetes de esa dirección que pasan por el equipo. Ctrl+C para parar."),
                "diagnostico", [("destino", N_("IP a capturar"), "10.0.0.20")], N_("Captura.")),
}
DIAG = {"ports", "mac", "version", "routes", "logs", "ping", "config", "arp", "lldp", "cpu", "alarms", "trace", "time", "ha",
        "sessions", "users", "disk", "dnsq", "capture"}


def family(key: str, plat: tuple, tasks: dict, save: str | None = None, note: str | None = None, doc: str = "",
           extra: list | None = None, vars_override: dict | None = None) -> list[dict]:
    """Recetas de una familia: una por tarea de «tasks» ({tarea: (comando, comprobación)})."""
    out = []
    for t, (cmd, chk) in tasks.items():
        name, intro, cat, vars_, step = TASKS[t]
        vars_ = (vars_override or {}).get(t, vars_)
        steps = []
        if note:
            steps.append((note, "", "w"))
        steps.append((step, cmd, "c" if (t in DIAG or t == "save") and not chk else ("w" if t in ("shutdown", "access", "ssh") else "")))
        if save and t not in DIAG and t != "save":
            steps.append((N_("Guardar."), save, "w"))
        if chk:
            steps.append((N_("Comprobar."), chk, "c"))
        r = R(f"{key}-{t}", plat, cat, name, intro, vars_, steps, doc)
        r["name_arg"] = plat[2]
        out.append(r)
    return out + (extra or [])


IOSLIKE = ("Varios", "ioslike", "CLI tipo Cisco")
NOTE_IOSLIKE = N_("Sintaxis tipo Cisco: si un comando da error en su equipo, escriba «?» para ver la sintaxis exacta.")
GEN = []
GEN += family("ioslike", IOSLIKE, {
    "hostname": ("configure terminal\nhostname {{nombre}}\nend", "show running-config | include hostname"),
    "user": ("configure terminal\nusername {{usuario}} privilege 15 password {{clave}}\nend", "show running-config | include username"),
    "snmp": ("configure terminal\nsnmp-server community {{comunidad}} ro\nend", "show running-config | include snmp-server"),
    "syslog": ("configure terminal\nlogging host {{opennccm}}\nend", "show running-config | include logging"),
    "ntp": ("configure terminal\nntp server {{ntp}}\nend", "show running-config | include ntp"),
    "vlan": ("configure terminal\nvlan {{vlan}}\nname {{nombre}}\nend", "show vlan"),
    "desc": ("configure terminal\ninterface {{puerto}}\ndescription {{desc}}\nend", "show running-config interface {{puerto}}"),
    "shutdown": ("configure terminal\ninterface {{puerto}}\nshutdown\nend", "show running-config interface {{puerto}}"),
    "ports": ("show interfaces", ""), "mac": ("show mac address-table", ""), "version": ("show version", ""),
    "ping": ("ping {{destino}}", ""), "save": ("write memory", "show startup-config | include hostname"),
}, save="write memory", note=NOTE_IOSLIKE)

# Dell EMC OS10
GEN += family("os10", ("Dell", "dellos10", "Dell OS10"), {
    "hostname": ("configure terminal\nhostname {{nombre}}\nend", "show running-configuration | grep hostname"),
    "user": ("configure terminal\nusername {{usuario}} password {{clave}} role sysadmin\nend", "show running-configuration users"),
    "snmp": ("configure terminal\nsnmp-server community {{comunidad}} ro\nend", "show running-configuration snmp"),
    "syslog": ("configure terminal\nlogging server {{opennccm}}\nend", "show running-configuration logging"),
    "ntp": ("configure terminal\nntp server {{ntp}}\nend", "show ntp status"),
    "vlan": ("configure terminal\ninterface vlan{{vlan}}\ndescription {{nombre}}\nend", "show vlan"),
    "desc": ("configure terminal\ninterface {{puerto}}\ndescription {{desc}}\nend", "show interface status"),
    "shutdown": ("configure terminal\ninterface {{puerto}}\nshutdown\nend", "show interface status"),
    "ports": ("show interface status", ""), "mac": ("show mac address-table", ""), "version": ("show version", ""),
    "save": ("write memory", "show startup-configuration | grep hostname"),
}, save="write memory", doc="https://www.dell.com/support/home/en-us/product-support/product/smartfabric-os10/docs",
   vars_override={"desc": [("puerto", N_("Puerto"), "ethernet1/1/1"), ("desc", N_("Descripción"), "Uplink")],
                  "shutdown": [("puerto", N_("Puerto"), "ethernet1/1/8")]})

# SONiC (Dell Enterprise SONiC y Edge-Core): la CLI de Linux de SONiC («config» y «show»)
GEN += family("sonic", ("SONiC", "sonic", "SONiC"), {
    "hostname": ("sudo config hostname {{nombre}}", "show runningconfiguration all | grep -i hostname"),
    "snmp": ("sudo config snmp community add {{comunidad}} RO", "show runningconfiguration snmp"),
    "syslog": ("sudo config syslog add {{opennccm}}", "show runningconfiguration syslog"),
    "ntp": ("sudo config ntp add {{ntp}}", "show ntp"),
    "vlan": ("sudo config vlan add {{vlan}}", "show vlan brief"),
    "access": ("sudo config vlan member add -u {{vlan}} {{puerto}}", "show vlan brief"),
    "desc": ("sudo config interface description {{puerto}} \"{{desc}}\"", "show interfaces description"),
    "shutdown": ("sudo config interface shutdown {{puerto}}", "show interfaces status {{puerto}}"),
    "ports": ("show interfaces status", ""), "mac": ("show mac", ""), "version": ("show version", ""),
    "logs": ("show logging | tail -n 100", ""), "save": ("sudo config save -y", ""),
}, save="sudo config save -y", doc="https://github.com/sonic-net/SONiC/blob/master/doc/Command-Reference.md",
   vars_override={k: [("puerto", N_("Puerto"), "Ethernet0")] + v[3][1:] for k, v in TASKS.items() if k in ("desc", "shutdown")}
   | {"access": [("puerto", N_("Puerto"), "Ethernet0"), ("vlan", N_("VLAN"), "10")]})

# Nokia SR OS (CLI clásica; en MD-CLI anteponga «//» para usarla)
GEN += family("sros", ("Nokia", "sros", "Nokia SR OS"), {
    "hostname": ("configure system name \"{{nombre}}\"", "show system information"),
    "snmp": ("configure system security snmp community \"{{comunidad}}\" r version v2c", "show system security snmp"),
    "ntp": ("configure system time ntp server {{ntp}}\nconfigure system time ntp no shutdown", "show system ntp"),
    "desc": ("configure port {{puerto}} description \"{{desc}}\"", "show port description"),
    "shutdown": ("configure port {{puerto}} shutdown", "show port {{puerto}}"),
    "route": ("configure router static-route-entry {{red}} next-hop {{salto}} no shutdown", "show router route-table protocol static"),
    "ports": ("show port", ""), "version": ("show version", ""), "routes": ("show router route-table", ""),
    "logs": ("show log log-id 99", ""), "ping": ("ping {{destino}}", ""), "save": ("admin save", ""),
}, save="admin save", note=N_("Comandos de la CLI clásica. En MD-CLI, escriba «//» antes para usarla."),
   doc="https://documentation.nokia.com/sr/", vars_override={"desc": [("puerto", N_("Puerto"), "1/1/1"), ("desc", N_("Descripción"), "Uplink")],
                                                              "shutdown": [("puerto", N_("Puerto"), "1/1/8")]})

# Nokia SR Linux
GEN += family("srl", ("Nokia", "srl", "Nokia SR Linux"), {
    "hostname": ("enter candidate\nset / system name host-name {{nombre}}\ncommit now", "info from running / system name"),
    "ntp": ("enter candidate\nset / system ntp admin-state enable network-instance mgmt server {{ntp}}\ncommit now", "info from state / system ntp"),
    "syslog": ("enter candidate\nset / system logging remote-server {{opennccm}} network-instance mgmt\ncommit now", "info from running / system logging"),
    "desc": ("enter candidate\nset / interface {{puerto}} description \"{{desc}}\"\ncommit now", "show interface brief"),
    "shutdown": ("enter candidate\nset / interface {{puerto}} admin-state disable\ncommit now", "show interface brief"),
    "ports": ("show interface brief", ""), "version": ("show version", ""),
    "routes": ("show network-instance default route-table", ""), "logs": ("show system logging buffer messages", ""),
    "ping": ("ping {{destino}} network-instance mgmt", ""), "save": ("save startup", ""),
}, doc="https://documentation.nokia.com/srlinux/",
   vars_override={"desc": [("puerto", N_("Puerto"), "ethernet-1/1"), ("desc", N_("Descripción"), "Uplink")],
                  "shutdown": [("puerto", N_("Puerto"), "ethernet-1/8")]})

# Alcatel-Lucent OmniSwitch (AOS 8)
GEN += family("aos", ("Alcatel-Lucent", "omniswitch", "OmniSwitch AOS"), {
    "hostname": ("system name \"{{nombre}}\"", "show system"),
    "user": ("user {{usuario}} password {{clave}} read-write all", "show user"),
    "syslog": ("swlog output socket {{opennccm}}", "show swlog"),
    "ntp": ("ntp server {{ntp}}\nntp client enable", "show ntp status"),
    "vlan": ("vlan {{vlan}} name \"{{nombre}}\"", "show vlan {{vlan}}"),
    "access": ("vlan {{vlan}} members port {{puerto}} untagged", "show vlan {{vlan}} members"),
    "desc": ("interfaces port {{puerto}} alias \"{{desc}}\"", "show interfaces alias"),
    "shutdown": ("interfaces port {{puerto}} admin-state disable", "show interfaces status"),
    "ports": ("show interfaces status", ""), "mac": ("show mac-learning", ""), "version": ("show microcode", ""),
    "save": ("write memory flash-synchro", ""),
}, save="write memory flash-synchro", doc="https://www.al-enterprise.com/en/support/documentation",
   vars_override={"access": [("puerto", N_("Puerto"), "1/1/5"), ("vlan", N_("VLAN"), "10")],
                  "desc": [("puerto", N_("Puerto"), "1/1/1"), ("desc", N_("Descripción"), "Uplink")],
                  "shutdown": [("puerto", N_("Puerto"), "1/1/8")]})

# Extreme Enterasys (SecureStack / EOS)
GEN += family("ets", ("Extreme", "enterasys", "Enterasys SecureStack"), {
    "hostname": ("set system name \"{{nombre}}\"", "show system"),
    "snmp": ("set snmp community {{comunidad}}", "show snmp community"),
    "syslog": ("set logging server 1 ip-addr {{opennccm}} state enable", "show logging server"),
    "ntp": ("set sntp client unicast\nset sntp server {{ntp}}", "show sntp"),
    "vlan": ("set vlan create {{vlan}}\nset vlan name {{vlan}} {{nombre}}", "show vlan {{vlan}}"),
    "desc": ("set port alias {{puerto}} \"{{desc}}\"", "show port alias {{puerto}}"),
    "shutdown": ("set port disable {{puerto}}", "show port status {{puerto}}"),
    "ports": ("show port status", ""), "mac": ("show mac", ""), "version": ("show version", ""),
    "logs": ("show logging buffer", ""), "save": ("save config", ""),
}, vars_override={"desc": [("puerto", N_("Puerto"), "ge.1.1"), ("desc", N_("Descripción"), "Uplink")],
                  "shutdown": [("puerto", N_("Puerto"), "ge.1.8")]})

# Zyxel (switches GS/XGS)
GEN += family("zyxel", ("Zyxel", "zyxel", "Zyxel"), {
    "hostname": ("configure\nhostname {{nombre}}\nexit", "show running-config"),
    "vlan": ("configure\nvlan {{vlan}}\nname {{nombre}}\nexit\nexit", "show vlan"),
    "desc": ("configure\ninterface port-channel {{puerto}}\nname {{desc}}\nexit\nexit", "show interfaces config {{puerto}}"),
    "shutdown": ("configure\ninterface port-channel {{puerto}}\ninactive\nexit\nexit", "show interfaces config {{puerto}}"),
    "ports": ("show interfaces config *", ""), "mac": ("show mac address-table all", ""), "version": ("show system-information", ""),
    "logs": ("show logging", ""), "ping": ("ping {{destino}}", ""), "save": ("write memory", ""),
}, save="write memory", vars_override={"desc": [("puerto", N_("Puerto"), "1"), ("desc", N_("Descripción"), "Uplink")],
                                       "shutdown": [("puerto", N_("Puerto"), "8")]})

# pfSense y OPNsense (shell de FreeBSD)
GEN += family("bsd", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), {
    "version": ("cat /etc/version 2>/dev/null || opnsense-version", ""),
    "ports": ("ifconfig", ""), "routes": ("netstat -rn", ""),
    "logs": ("tail -n 100 /var/log/system.log 2>/dev/null || tail -n 100 /var/log/system/latest.log", ""),
    "ping": ("ping -c 4 {{destino}}", ""),
}, extra=[
    R("bsd-rules", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "diagnostico", N_("Ver las reglas cargadas del cortafuegos (pfSense / OPNsense)"),
      N_("Las reglas de pf en uso, tal como las aplica el sistema."), [], [(N_("Reglas."), "pfctl -sr", "c")]),
    R("bsd-states", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "diagnostico", N_("Ver los estados y contadores del cortafuegos (pfSense / OPNsense)"),
      N_("Conexiones en la tabla de estados y contadores de pf."), [], [(N_("Información de pf."), "pfctl -si", "c")]),
    R("bsd-listen", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "diagnostico", N_("Ver qué puertos escuchan (pfSense / OPNsense)"),
      N_("Servicios en escucha y su proceso."), [], [(N_("Puertos en escucha."), "sockstat -4 -l", "c")]),
    R("bsd-resources", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "diagnostico", N_("Ver disco y carga (pfSense / OPNsense)"),
      N_("Espacio en disco y carga del procesador."), [], [(N_("Disco."), "df -h", ""), (N_("Carga y tiempo encendido."), "uptime", "c")]),
    R("bsd-backup", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "sistema", N_("Guardar una copia local de la configuración (pfSense / OPNsense)"),
      N_("Copia config.xml antes de un cambio grande (OpenNCCM ya guarda sus versiones)."), [],
      [(N_("Copiar."), "cp /cf/conf/config.xml /root/config-antes.xml 2>/dev/null || cp /conf/config.xml /root/config-antes.xml", ""),
       (N_("Comprobar."), "ls -l /root/config-antes.xml", "c")]),
    R("bsd-arp", ("pfSense / OPNsense", "bsdfw", "pfSense / OPNsense"), "diagnostico", N_("Ver la tabla ARP (pfSense / OPNsense)"),
      N_("Direcciones IP y MAC vistas en cada interfaz."), [], [(N_("Tabla ARP."), "arp -an", "c")]),
])
# Check Point Gaia (clish)
GEN += family("gaia", ("Check Point", "gaia", "Check Point Gaia"), {
    "hostname": ("set hostname {{nombre}}", "show hostname"),
    "syslog": ("add syslog log-remote-address {{opennccm}} level info", "show syslog all"),
    "ntp": ("set ntp active on\nset ntp server primary {{ntp}} version 4", "show ntp current"),
    "snmp": ("set snmp agent on\nadd snmp community {{comunidad}} read-only", "show snmp community"),
    "dns": ("set dns primary {{dns}}", "show dns primary"),
    "desc": ("set interface {{puerto}} comments \"{{desc}}\"", "show interface {{puerto}} comments"),
    "route": ("set static-route {{red}} nexthop gateway address {{salto}} on", "show route static"),
    "ports": ("show interfaces all", ""), "version": ("show version all", ""), "routes": ("show route", ""),
    "save": ("save config", ""),
}, save="save config", doc="https://sc1.checkpoint.com/documents/R81.20/WebAdminGuides/EN/CP_R81.20_Gaia_AdminGuide/Content/Topics-GAG/Introduction.htm",
   vars_override={"desc": [("puerto", N_("Interfaz"), "eth1"), ("desc", N_("Descripción"), "LAN")]})

# F5 BIG-IP (tmsh)
GEN += family("f5", ("F5", "tmsh", "F5 BIG-IP (tmsh)"), {
    "hostname": ("modify sys global-settings hostname {{nombre}}", "list sys global-settings hostname"),
    "user": ("create auth user {{usuario}} password {{clave}} partition-access add { all-partitions { role admin } } shell tmsh", "list auth user {{usuario}}"),
    "syslog": ("modify sys syslog remote-servers add { opennccm { host {{opennccm}} remote-port 514 } }", "list sys syslog remote-servers"),
    "ntp": ("modify sys ntp servers add { {{ntp}} }", "list sys ntp servers"),
    "dns": ("modify sys dns name-servers add { {{dns}} }", "list sys dns name-servers"),
    "ports": ("show net interface", ""), "version": ("show sys version", ""), "routes": ("show net route", ""),
    "logs": ("show sys log ltm lines 100", ""), "save": ("save sys config", ""),
}, save="save sys config", doc="https://clouddocs.f5.com/cli/tmsh-reference/latest/",
   extra=[R("f5-failover", ("F5", "tmsh", "F5 BIG-IP (tmsh)"), "diagnostico", N_("Ver el estado de alta disponibilidad (F5 BIG-IP)"),
            N_("Si este equipo está activo o en espera."), [], [(N_("Estado."), "show sys failover", "c")])])

# Citrix ADC / NetScaler
GEN += family("ns", ("Citrix", "netscaler", "Citrix ADC / NetScaler"), {
    "hostname": ("set ns hostName {{nombre}}", "show ns hostName"),
    "user": ("add system user {{usuario}} {{clave}}\nbind system user {{usuario}} superuser 100", "show system user {{usuario}}"),
    "syslog": ("add audit syslogAction OPENNCCM {{opennccm}} -logLevel ALL\nadd audit syslogPolicy OPENNCCM true OPENNCCM\nbind system global OPENNCCM -priority 100", "show audit syslogAction OPENNCCM"),
    "ntp": ("add ntp server {{ntp}}\nenable ntp sync", "show ntp status"),
    "snmp": ("add snmp community {{comunidad}} GET", "show snmp community"),
    "ports": ("show interface", ""), "version": ("show version", ""), "routes": ("show route", ""),
    "save": ("save ns config", ""),
}, save="save ns config", doc="https://docs.netscaler.com/en-us/citrix-adc-command-reference.html",
   extra=[R("ns-ha", ("Citrix", "netscaler", "Citrix ADC / NetScaler"), "diagnostico", N_("Ver el estado de alta disponibilidad (NetScaler)"),
            N_("Nodos del par y cuál es el principal."), [], [(N_("Estado."), "show ha node", "c")]),
          R("ns-vservers", ("Citrix", "netscaler", "Citrix ADC / NetScaler"), "diagnostico", N_("Ver los servidores virtuales (NetScaler)"),
            N_("Servidores virtuales de balanceo y su estado."), [], [(N_("Servidores virtuales."), "show lb vserver", "c")])])

# Cisco WLC (AireOS)
GEN += family("aireos", ("Cisco", "aireos", "Cisco WLC (AireOS)"), {
    "hostname": ("config sysname {{nombre}}", "show sysinfo"),
    "user": ("config mgmtuser add {{usuario}} {{clave}} read-write", "show mgmtuser"),
    "syslog": ("config logging syslog host {{opennccm}}", "show logging"),
    "ntp": ("config time ntp server 1 {{ntp}}", "show time"),
    "ports": ("show port summary", ""), "version": ("show sysinfo", ""), "logs": ("show msglog", ""),
    "ping": ("ping {{destino}}", ""), "save": ("save config\ny", ""),
}, save="save config\ny", extra=[
    R("aireos-aps", ("Cisco", "aireos", "Cisco WLC (AireOS)"), "diagnostico", N_("Ver los puntos de acceso unidos (Cisco WLC)"),
      N_("Puntos de acceso unidos al controlador, con su modelo e IP."), [], [(N_("Puntos de acceso."), "show ap summary", "c")]),
    R("aireos-clients", ("Cisco", "aireos", "Cisco WLC (AireOS)"), "diagnostico", N_("Ver los clientes conectados (Cisco WLC)"),
      N_("Clientes inalámbricos con su AP y estado."), [], [(N_("Clientes."), "show client summary", "c")])])

# Aruba Mobility Controller (ArubaOS)
GEN += family("arubaos", ("HPE Aruba", "arubaos", "ArubaOS (controlador)"), {
    "hostname": ("configure terminal\nhostname {{nombre}}\nend", "show hostname"),
    "user": ("configure terminal\nmgmt-user {{usuario}} root {{clave}}\nend", "show mgmt-user"),
    "syslog": ("configure terminal\nlogging {{opennccm}}\nend", "show logging servers"),
    "ntp": ("configure terminal\nntp server {{ntp}}\nend", "show ntp servers"),
    "snmp": ("configure terminal\nsnmp-server community {{comunidad}}\nend", "show snmp community"),
    "ports": ("show port status", ""), "version": ("show version", ""), "save": ("write memory", ""),
}, save="write memory", extra=[
    R("arubaos-aps", ("HPE Aruba", "arubaos", "ArubaOS (controlador)"), "diagnostico", N_("Ver los puntos de acceso (ArubaOS)"),
      N_("Puntos de acceso del controlador, con su grupo y estado."), [], [(N_("Puntos de acceso."), "show ap database", "c")]),
    R("arubaos-users", ("HPE Aruba", "arubaos", "ArubaOS (controlador)"), "diagnostico", N_("Ver los clientes conectados (ArubaOS)"),
      N_("Clientes con su IP, rol y AP."), [], [(N_("Clientes."), "show user-table", "c")])])
