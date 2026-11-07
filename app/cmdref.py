"""Comandos de cada equipo para la consola (autocompletado y frecuentes) y para el asistente IA, según su tipo, su
modelo y su versión de firmware.

Para cada tipo de equipo se junta:
1. Su lista propia (TYPES) o, si no tiene, la de su familia de consola (consolecore.SUGGEST) más lo que se le agrega
   (EXTEND). Los tipos sin familia propia («generic») tienen todos lista propia o se quedan con lo de 2.
2. Lo que OpenNCCM ya ejecuta en ese equipo: el respaldo, el inventario, la licencia y las lecturas de puertos, chasis
   y vecinos (drivers, ports, chassis, topology). Así ningún tipo queda sin comandos suyos.
3. Las variantes por modelo y versión (variants): PoE, inalámbrico, stack, SD-WAN, RouterOS 6 o 7…

Los comandos de consulta de la lista de un tipo cuentan como consulta en el modo solo lectura de ese tipo
(consolecore.is_change), aunque no empiecen por show/display (porterrshow, ifconfig…).

Fuentes: la referencia de comandos de cada fabricante (WatchGuard: Fireware CLI Reference v12.9.4; Fortinet: FortiOS
CLI Reference; Palo Alto: PAN-OS CLI Quick Start; Cisco: guías de comandos de IOS, IOS-XE, IOS-XR, ASA, NX-OS y AireOS;
Juniper: CLI Explorer; Aruba: AOS-CX y AOS-S Command-Line Interface Guides; etc.).
"""
from __future__ import annotations

import re
from functools import lru_cache

from .i18n import N_

# ---------------------------------------------------------------- descripciones repetidas
IFS, RT, ARP, MAC, VLAN, LLDP = (N_("resumen de interfaces"), N_("tabla de rutas"), N_("tabla ARP"), N_("tabla MAC"),
                                 N_("VLAN"), N_("vecinos LLDP"))
VER, LOG, RUN, CFG, PORTS, IPS = (N_("versión"), N_("registro"), N_("configuración en ejecución"), N_("configuración"),
                                  N_("estado de los puertos"), N_("direcciones IP"))
ENV, INV, CPU, MEM, PING, TRACE = (N_("fuentes, ventiladores y temperatura"), N_("módulos y números de serie"),
                                   N_("uso de CPU"), N_("memoria"), N_("ping"), N_("traceroute"))
BOOT, CONF, HA, VPN, POL, NAT = (N_("reiniciar el equipo"), N_("entrar en modo configuración"),
                                 N_("estado del clúster (HA)"), N_("túneles VPN"), N_("políticas del firewall"),
                                 N_("reglas NAT"))
SESS, BGP, OSPF, POE, STACK, USERS = (N_("conexiones activas"), N_("vecinos BGP"), N_("vecinos OSPF"),
                                      N_("PoE por puerto"), N_("estado del stack"), N_("sesiones abiertas"))
APS, CLIENTS, SSIDS, SYS = (N_("puntos de acceso"), N_("clientes inalámbricos"), N_("redes inalámbricas (SSID)"),
                            N_("datos del equipo"))


def _r(cmd, desc):  # consulta
    return (cmd, desc, "r")


def _c(cmd, desc):  # cambia la configuración (entrar en modo configuración, etc.)
    return (cmd, desc, "c")


def _x(cmd, desc):  # corta el servicio
    return (cmd, desc, "x")


# ---------------------------------------------------------------- listas propias (reemplazan la de la familia)
_ASA = [_r("show interface ip brief", IFS), _r("show route", RT), _r("show arp", ARP), _r("show conn count", SESS),
        _r("show nat", NAT), _r("show access-list", N_("listas de acceso y sus contadores")), _r("show failover", HA),
        _r("show vpn-sessiondb summary", N_("sesiones de VPN")), _r("show cpu usage", CPU), _r("show memory", MEM),
        _r("show logging", LOG), _r("show inventory", INV), _r("show clock", N_("fecha y hora")), _r("show version", VER),
        _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE),
        _c("configure terminal", CONF), _x("reload", BOOT)]
_FTD = [_r("show interface ip brief", IFS), _r("show route", RT), _r("show arp", ARP), _r("show conn count", SESS),
        _r("show nat", NAT), _r("show failover", HA), _r("show vpn-sessiondb summary", N_("sesiones de VPN")),
        _r("show cpu usage", CPU), _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING),
        _r("traceroute 8.8.8.8", TRACE), _x("reboot", BOOT)]
_XR = [_r("show ip interface brief", IFS), _r("show interfaces summary", N_("interfaces por estado")), _r("show route", RT),
       _r("show arp", ARP), _r("show bgp summary", BGP), _r("show ospf neighbor", OSPF), _r("show lldp neighbors", LLDP),
       _r("show cdp neighbors", N_("vecinos CDP")), _r("show platform", N_("tarjetas y estado")), _r("show inventory", INV),
       _r("show environment", ENV), _r("show install active summary", N_("paquetes instalados")), _r("show logging", LOG),
       _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE),
       _c("configure", CONF), _x("reload", BOOT)]
_AIREOS = [_r("show sysinfo", SYS), _r("show ap summary", APS), _r("show client summary", CLIENTS),
           _r("show wlan summary", SSIDS), _r("show interface summary", N_("interfaces")), _r("show port summary", PORTS),
           _r("show cdp neighbors", N_("vecinos CDP")), _r("show redundancy summary", HA), _r("show msglog", LOG),
           _r("show run-config commands", RUN), _r("ping 8.8.8.8", PING), _x("reset system", BOOT)]
_CISCO_AP = [_r("show dot11 associations", CLIENTS), _r("show ip interface brief", IFS), _r("show interfaces", N_("detalle de las interfaces")),
             _r("show cdp neighbors", N_("vecinos CDP")), _r("show logging", LOG), _r("show version", VER),
             _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure terminal", CONF), _x("reload", BOOT)]
_VIPTELA = [_r("show control connections", N_("conexiones con los controladores")), _r("show bfd sessions", N_("túneles (BFD)")),
            _r("show omp peers", N_("vecinos OMP")), _r("show interface", N_("interfaces")), _r("show ip route", RT),
            _r("show system status", SYS), _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING),
            _x("reboot", BOOT)]
_CISCO_SMB = [_r("show interfaces status", PORTS), _r("show interfaces counters", N_("contadores por puerto")), _r("show vlan", VLAN),
              _r("show ip interface", IPS), _r("show ip route", RT), _r("show arp", ARP), _r("show mac address-table", MAC),
              _r("show lldp neighbors", LLDP), _r("show cdp neighbors", N_("vecinos CDP")), _r("show spanning-tree", N_("spanning tree")),
              _r("show system", SYS), _r("show inventory", INV), _r("show logging", LOG), _r("show version", VER),
              _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure terminal", CONF), _x("reload", BOOT)]
_PROCURVE = [_r("show interfaces brief", PORTS), _r("show vlans", VLAN), _r("show ip", IPS), _r("show ip route", RT),
             _r("show arp", ARP), _r("show mac-address", MAC), _r("show lldp info remote-device", LLDP),
             _r("show spanning-tree", N_("spanning tree")), _r("show power-over-ethernet brief", POE), _r("show modules", INV),
             _r("show system", SYS), _r("show logging -r", LOG), _r("show version", VER), _r("show running-config", RUN),
             _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE), _c("configure", CONF), _x("reload", BOOT)]
_AOSCX = [_r("show interface brief", PORTS), _r("show vlan", VLAN), _r("show ip interface brief", IPS), _r("show ip route", RT),
          _r("show arp", ARP), _r("show mac-address-table", MAC), _r("show lldp neighbor-info", LLDP),
          _r("show power-over-ethernet", POE), _r("show vsf", N_("estado del stack (VSF)")), _r("show vsx status", HA),
          _r("show system", SYS), _r("show environment fan", N_("ventiladores")), _r("show environment power-supply", N_("fuentes de alimentación")),
          _r("show logging -r", LOG), _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING),
          _r("traceroute 8.8.8.8", TRACE), _c("configure terminal", CONF), _x("boot system", BOOT)]
_ARUBA_MC = [_r("show ap database", APS), _r("show user-table", CLIENTS), _r("show ip interface brief", IPS), _r("show ip route", RT),
             _r("show vlan", VLAN), _r("show switches", N_("controladoras")), _r("show license", N_("licencias")),
             _r("show log system 50", LOG), _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING),
             _c("configure terminal", CONF), _x("reload", BOOT)]
_FORTI = [_r("get system status", SYS), _r("get system performance status", N_("CPU, memoria y sesiones")),
          _r("get system interface physical", PORTS), _r("get router info routing-table all", RT), _r("get system arp", ARP),
          _r("get router info bgp summary", BGP), _r("get router info ospf neighbor", OSPF),
          _r("get vpn ipsec tunnel summary", VPN), _r("get system ha status", HA), _r("get hardware status", N_("hardware")),
          _r("show firewall policy", POL), _r("show system interface", N_("configuración de las interfaces")),
          _r("execute ping 8.8.8.8", PING), _r("execute traceroute 8.8.8.8", TRACE), _x("execute reboot", BOOT)]
_PANOS = [_r("show system info", SYS), _r("show interface all", PORTS), _r("show routing route", RT), _r("show arp all", ARP),
          _r("show session info", SESS), _r("show high-availability state", HA), _r("show vpn ipsec-sa", VPN),
          _r("show running security-policy", POL), _r("show system resources", N_("CPU, memoria y procesos")),
          _r("show system disk-space", N_("espacio en disco")), _r("show jobs all", N_("tareas (commits, descargas…)")),
          _r("show admins", USERS), _r("show clock", N_("fecha y hora")), _r("show config running", RUN),
          _r("ping host 8.8.8.8", PING), _r("traceroute host 8.8.8.8", TRACE), _c("configure", CONF),
          _x("request restart system", BOOT)]
_GAIA = [_r("show interfaces all", PORTS), _r("show route", RT), _r("show arp dynamic all", ARP), _r("show asset all", INV),
         _r("show sysenv all", ENV), _r("show hostname", N_("nombre del equipo")), _r("show uptime", N_("tiempo encendido")),
         _r("show version all", VER), _r("show configuration", CFG), _r("ping 8.8.8.8", PING), _x("reboot", BOOT)]
_F5 = [_r("show sys version", VER), _r("show sys hardware", INV), _r("show net interface", PORTS), _r("list net self", IPS),
       _r("show net route", RT), _r("show net arp", ARP), _r("show ltm virtual", N_("servidores virtuales")),
       _r("show ltm pool", N_("pools y sus miembros")), _r("show sys failover", HA), _r("show sys cpu", CPU),
       _r("show sys memory", MEM), _r("list one-line", CFG)]
_FIREWARE = [_r("show interface", IFS), _r("show ip route", RT), _r("show ip static-route", N_("rutas estáticas")),
             _r("show vlan", VLAN), _r("show bridge", N_("bridges")), _r("show link-aggregation", N_("agregación de enlaces")),
             _r("show sysinfo", N_("modelo, serie, versión y uso")), _r("show feature-key", N_("feature key y vencimientos")),
             _r("show cluster status", HA), _r("show connection count", SESS), _r("show users", N_("usuarios autenticados")),
             _r("show vpn-status bovpn gateway", N_("VPN de sucursal (BOVPN)")), _r("show bovpn-tunnel", N_("túneles BOVPN")),
             _r("show vpn-status l2tp session", N_("sesiones de Mobile VPN L2TP")),
             _r("show ip dynamic-routing bgp", BGP), _r("show ip dynamic-routing ospf", OSPF),
             _r("show log-cache tail 50", LOG), _r("show sys-storage", N_("espacio en disco")),
             _r("show signature-update", N_("firmas de los servicios")), _r("show update-history", N_("historial de actualizaciones")),
             _r("show status-report", N_("informe de estado completo")), _r("who", USERS), _r("ping 8.8.8.8", PING),
             _r("traceroute 8.8.8.8", TRACE), _r("export config to console", CFG), _c("configure", CONF), _x("reboot", BOOT)]
_SROS = [_r("show port", PORTS), _r("show router interface", IFS), _r("show router route-table", RT), _r("show router arp", ARP),
         _r("show router bgp summary", BGP), _r("show router ospf neighbor", OSPF), _r("show card", N_("tarjetas y estado")),
         _r("show chassis", ENV), _r("show system information", SYS), _r("show log log-id 99", LOG), _r("show version", VER),
         _r("admin display-config", CFG), _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE)]
_SRL = [_r("show interface brief", IFS), _r("show network-instance default route-table", RT),
        _r("show network-instance default protocols bgp neighbor", BGP), _r("show system lldp neighbor", LLDP),
        _r("show platform chassis", N_("chasis")), _r("show version", VER), _r("info flat", CFG)]
_AOS = [_r("show interfaces status", PORTS), _r("show vlan", VLAN), _r("show ip interface", IPS), _r("show ip routes", RT),
        _r("show arp", ARP), _r("show mac-learning", MAC), _r("show lldp remote-system", LLDP), _r("show chassis", INV),
        _r("show health", N_("uso de CPU y memoria")), _r("show system", SYS), _r("show microcode", VER),
        _r("show configuration snapshot", CFG), _r("ping 8.8.8.8", PING)]
_VYOS = [_r("show interfaces", IFS), _r("show ip route", RT), _r("show arp", ARP), _r("show dhcp server leases", N_("préstamos DHCP")),
         _r("show vpn ipsec sa", VPN), _r("show system uptime", N_("tiempo encendido")), _r("show log tail 50", LOG),
         _r("show version", VER), _r("show configuration commands", CFG), _r("ping 8.8.8.8", PING),
         _r("traceroute 8.8.8.8", TRACE), _c("configure", CONF), _x("reboot", BOOT)]
_EDGEMAX = [_r("show interfaces", IFS), _r("show ip route", RT), _r("show arp", ARP), _r("show dhcp leases", N_("préstamos DHCP")),
            _r("show vpn ipsec sa", VPN), _r("show ip bgp summary", BGP), _r("show ubnt discover", N_("equipos Ubiquiti vecinos")),
            _r("show system uptime", N_("tiempo encendido")), _r("show log tail 50", LOG), _r("show version", VER),
            _r("show configuration commands", CFG), _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE),
            _c("configure", CONF), _x("reboot", BOOT)]
_AIROS = [_r("mca-status", N_("estado del radio y del enlace")), _r("wstalist", N_("estaciones conectadas")),
          _r("iwconfig", N_("interfaces inalámbricas")), _r("ifconfig", N_("interfaces")), _r("route -n", RT),
          _r("cat /etc/version", VER), _r("cat /etc/board.info", N_("placa, modelo y MAC")), _r("uptime", N_("tiempo encendido y carga")),
          _r("free", MEM), _r("ping -c 4 8.8.8.8", PING), _x("reboot", BOOT)]
_AIRFIBER = [_r("ifconfig", N_("interfaces")), _r("route -n", RT), _r("cat /etc/version", VER),
             _r("cat /etc/board.info", N_("placa, modelo y MAC")), _r("uptime", N_("tiempo encendido y carga")), _r("free", MEM),
             _r("ping -c 4 8.8.8.8", PING), _x("reboot", BOOT)]
_UNIFI_AP = [_r("info", N_("modelo, versión y estado")), _r("iwconfig", N_("interfaces inalámbricas")), _r("ifconfig", N_("interfaces")),
             _r("route -n", RT), _r("uptime", N_("tiempo encendido y carga")), _r("cat /var/log/messages", LOG),
             _r("ping -c 4 8.8.8.8", PING), _x("reboot", BOOT)]
_BSD_FW = [_r("ifconfig -a", N_("interfaces")), _r("netstat -rn", RT), _r("arp -an", ARP), _r("pfctl -si", N_("estado del firewall")),
           _r("pfctl -sr", POL), _r("pfctl -sn", NAT), _r("sockstat -4l", N_("puertos abiertos")),
           _r("uptime", N_("tiempo encendido y carga")), _r("df -h", N_("espacio en disco")),
           _r("tail -n 50 /var/log/system.log", LOG), _r("ping -c 4 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE),
           _x("reboot", BOOT)]
_OS10 = [_r("show interface status", PORTS), _r("show vlan", VLAN), _r("show ip interface brief", IPS), _r("show ip route", RT),
         _r("show ip arp", ARP), _r("show mac address-table", MAC), _r("show lldp neighbors", LLDP), _r("show inventory", INV),
         _r("show environment", ENV), _r("show system", SYS), _r("show version", VER), _r("show running-configuration", RUN),
         _r("ping 8.8.8.8", PING), _r("traceroute 8.8.8.8", TRACE), _c("configure terminal", CONF), _x("reload", BOOT)]
_FTOS = [_r("show interfaces status", PORTS), _r("show ip interface brief", IFS), _r("show ip route", RT), _r("show arp", ARP),
         _r("show mac-address-table", MAC), _r("show vlan", VLAN), _r("show lldp neighbors", LLDP), _r("show inventory", INV),
         _r("show environment", ENV), _r("show logging", LOG), _r("show version", VER), _r("show running-config", RUN),
         _r("ping 8.8.8.8", PING), _c("configure", CONF), _x("reload", BOOT)]
_DELL_N = [_r("show interfaces status", PORTS), _r("show vlan", VLAN), _r("show ip interface", IPS), _r("show ip route", RT),
           _r("show arp", ARP), _r("show mac address-table", MAC), _r("show lldp remote-device all", LLDP),
           _r("show switch", STACK), _r("show system", SYS), _r("show logging", LOG), _r("show version", VER),
           _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure", CONF), _x("reload", BOOT)]
_ICX = [_r("show interfaces brief", PORTS), _r("show vlan", VLAN), _r("show ip interface", IPS), _r("show ip route", RT),
        _r("show arp", ARP), _r("show mac-address", MAC), _r("show lldp neighbors", LLDP), _r("show stack", STACK),
        _r("show inline power", POE), _r("show media", N_("módulos SFP")), _r("show chassis", ENV), _r("show logging", LOG),
        _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure terminal", CONF),
        _x("reload", BOOT)]
_AWPLUS = [_r("show interface brief", IFS), _r("show vlan all", VLAN), _r("show ip interface brief", IPS), _r("show ip route", RT),
           _r("show arp", ARP), _r("show mac address-table", MAC), _r("show lldp neighbors", LLDP), _r("show stack", STACK),
           _r("show power-inline", POE), _r("show system environment", ENV), _r("show log", LOG), _r("show version", VER),
           _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure terminal", CONF), _x("reload", BOOT)]
_RUIJIE = [_r("show interfaces status", PORTS), _r("show vlan", VLAN), _r("show ip interface brief", IPS), _r("show ip route", RT),
           _r("show arp", ARP), _r("show mac-address-table", MAC), _r("show lldp neighbors", LLDP), _r("show logging", LOG),
           _r("show version", VER), _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure terminal", CONF),
           _x("reload", BOOT)]
_JETSTREAM = [_r("show interface status", PORTS), _r("show vlan", VLAN), _r("show mac address-table all", MAC),
              _r("show system-info", SYS), _r("show cpu-utilization", CPU), _r("show logging buffer", LOG),
              _r("show running-config", RUN), _r("ping 8.8.8.8", PING), _c("configure", CONF), _x("reboot", BOOT)]
_OLT = [_r("display board 0", N_("tarjetas y estado")), _r("display ont autofind all", N_("ONT nuevas sin registrar")),
        _r("display ont info 0 all", N_("ONT registradas y su estado")), _r("display alarm active all", N_("alarmas activas")),
        _r("display version", VER), _r("display current-configuration", RUN)]
_ONYX = [_r("show interfaces ethernet status", PORTS), _r("show vlan", VLAN), _r("show ip route", RT), _r("show ip arp", ARP),
         _r("show mac-address-table", MAC), _r("show lldp remote", LLDP), _r("show inventory", INV), _r("show version", VER),
         _r("show running-config", RUN), _c("configure terminal", CONF)]
_SONIC = [_r("show interfaces status", PORTS), _r("show ip interfaces", IPS), _r("show ip route", RT), _r("show arp", ARP),
          _r("show mac", MAC), _r("show vlan brief", VLAN), _r("show lldp table", LLDP), _r("show platform summary", SYS),
          _r("show platform syseeprom", N_("placa y número de serie")), _r("show environment", ENV), _r("show version", VER)]
_DELL_SONIC = [_r("show interface status", PORTS), _r("show ip route", RT), _r("show vlan", VLAN), _r("show version", VER),
               _r("show running-configuration", RUN), _c("configure terminal", CONF)]
_CUMULUS = [_r("nv show interface", IFS), _r("nv show system", SYS), _r("nv show platform", N_("plataforma y hardware")),
            _r("nv config diff", N_("cambios sin aplicar")), _r("nv config show -o commands", CFG), _r("ip -br addr", IPS),
            _r("ip route", RT), _r("ping -c 4 8.8.8.8", PING)]
_OCNOS = [_r("show interface brief", IFS), _r("show ip route", RT), _r("show version", VER), _r("show running-config", RUN),
          _c("configure terminal", CONF)]
_DLINK = [_r("show ports", PORTS), _r("show vlan", VLAN), _r("show ipif", IPS), _r("show iproute", RT), _r("show arpentry", ARP),
          _r("show fdb", MAC), _r("show lldp remote_ports", LLDP), _r("show switch", SYS), _r("show log", LOG),
          _r("show config current_config", CFG), _r("ping 8.8.8.8", PING), _x("reboot", BOOT)]
_HIOS = [_r("show port all", PORTS), _r("show vlan brief", VLAN), _r("show system info", SYS), _r("show running-config-script", CFG)]
_NETSCALER = [_r("show ns version", VER), _r("show ns ip", IPS), _r("show interface", PORTS), _r("show route", RT),
              _r("show lb vserver", N_("servidores virtuales")), _r("show service", N_("servicios y su estado")),
              _r("show ha node", HA), _r("show ns runningConfig", CFG)]
_A10 = [_r("show interfaces brief", PORTS), _r("show slb virtual-server", N_("servidores virtuales")), _r("show ip route", RT),
        _r("show version", VER), _r("show running-config", RUN)]
_FOS = [_r("switchshow", N_("estado del switch y sus puertos")), _r("fabricshow", N_("switches de la fábrica")),
        _r("zoneshow", N_("zonas")), _r("porterrshow", N_("errores por puerto")), _r("sfpshow", N_("módulos SFP")),
        _r("psshow", N_("fuentes de alimentación")), _r("fanshow", N_("ventiladores")), _r("tempshow", N_("temperatura")),
        _r("version", VER), _r("configshow", CFG)]
_SAOS = [_r("port show", PORTS), _r("vlan show", VLAN), _r("lldp show neighbors", LLDP), _r("alarm show", N_("alarmas activas")),
         _r("software show", VER), _r("chassis show", SYS), _r("configuration show", CFG)]
_SCREENOS = [_r("get interface", IFS), _r("get route", RT), _r("get arp", ARP), _r("get policy", POL), _r("get session info", SESS),
             _r("get vpn", VPN), _r("get system", SYS), _r("get log event", LOG), _r("get config", CFG), _r("ping 8.8.8.8", PING)]
_MES = [_r("show interfaces status", PORTS), _r("show vlan", VLAN), _r("show ip interface", IPS), _r("show mac address-table", MAC),
        _r("show lldp neighbors", LLDP), _r("show system", SYS), _r("show version", VER), _r("show running-config", RUN),
        _c("configure", CONF), _x("reload", BOOT)]
_ZXROS = [_r("show ip interface brief", IFS), _r("show interface", N_("detalle de las interfaces")), _r("show ip route", RT),
          _r("show arp", ARP), _r("show mac", N_("tabla de direcciones MAC")), _r("show vlan", VLAN),
          _r("show version", VER), _r("show running-config", RUN), _c("configure terminal", CONF), _c("write", N_("guardar la configuración"))]
# OLT ZTE ZXA10 (C300/C320 y TITAN): tarjetas, ONU y potencia óptica
_ZTE_OLT = [_r("show card", N_("tarjetas de la OLT, con su versión y estado")),
            _r("show gpon onu state gpon-olt_1/1/1", N_("estado de las ONU de un puerto PON")),
            _r("show gpon onu uncfg", N_("ONU conectadas que todavía no se dieron de alta")),
            _r("show gpon onu by sn ZTEG00000000", N_("buscar una ONU por su número de serie")),
            _r("show gpon onu baseinfo gpon-olt_1/1/1", N_("ONU de un puerto PON con su tipo y serie")),
            _r("show pon power attenuation gpon-onu_1/1/1:1", N_("potencia óptica y atenuación de una ONU")),
            _r("show running-config interface gpon-onu_1/1/1:1", N_("configuración de una ONU")),
            _r("show mac gpon onu gpon-onu_1/1/1:1", N_("direcciones MAC detrás de una ONU")),
            _r("show system-group", N_("modelo, versión y tiempo encendida")), _r("show running-config", RUN),
            _c("configure terminal", CONF), _c("write", N_("guardar la configuración"))]
_ZTE_TITAN = [_r("show software", VER)] + [x for x in _ZTE_OLT if x[0] != "show system-group"]
_NOS = [_r("show interface brief", IFS), _r("show ip route", RT), _r("show lldp neighbors", LLDP), _r("show vlan brief", VLAN),
        _r("show version", VER), _r("show running-config", RUN), _c("configure terminal", CONF)]
_STONEOS = [_r("show interface", IFS), _r("show ip route", RT), _r("show arp", ARP), _r("show version", VER),
            _r("show configuration", CFG), _c("configure", CONF)]

TYPES: dict[str, list] = {
    "eltex": _MES, "zte_zxros": _ZXROS, "zte_olt": _ZTE_OLT, "zte_olt_titan": _ZTE_TITAN, "brocade_nos": _NOS, "extreme_nos": _NOS, "extreme_slx": _NOS, "brocade_netiron": _NOS,
    "hillstone_stoneos": _STONEOS,
    "cisco_asa": _ASA, "cisco_ftd": _FTD, "cisco_xr": _XR, "cisco_wlc": _AIREOS, "cisco_ap": _CISCO_AP, "cisco_viptela": _VIPTELA,
    "cisco_s200": _CISCO_SMB, "cisco_s300": _CISCO_SMB, "cisco_s500": _CISCO_SMB,
    "hp_procurve": _PROCURVE, "aruba_osswitch": _PROCURVE, "aruba_aoscx": _AOSCX, "aruba_os": _ARUBA_MC,
    "fortinet": _FORTI, "paloalto_panos": _PANOS, "checkpoint_gaia": _GAIA, "f5_tmsh": _F5, "watchguard_fireware": _FIREWARE,
    "nokia_sros": _SROS, "nokia_srl": _SRL, "alcatel_aos": _AOS, "vyos": _VYOS, "ubiquiti_edgerouter": _EDGEMAX,
    "ubiquiti_usg": _EDGEMAX, "ubiquiti_airos": _AIROS, "ubiquiti_airfiber": _AIRFIBER, "ubiquiti_unifi_ap": _UNIFI_AP,
    "pfsense": _BSD_FW + [_r("cat /etc/version", VER)], "opnsense": _BSD_FW + [_r("opnsense-version", VER)],
    "dell_os10": _OS10, "dell_force10": _FTOS, "dell_os9": _FTOS, "dell_powerconnect": _DELL_N, "dell_os6": _DELL_N,
    "ruckus_fastiron": _ICX, "allied_telesis_awplus": _AWPLUS, "ruijie_os": _RUIJIE, "tplink_jetstream": _JETSTREAM,
    "huawei_olt": _OLT, "huawei_smartax": _OLT, "mellanox_mlnxos": _ONYX, "edgecore_sonic": _SONIC, "dell_sonic": _DELL_SONIC,
    "cumulus_linux": _CUMULUS, "ipinfusion_ocnos": _OCNOS, "dlink_ds": _DLINK, "hirschmann_hios": _HIOS,
    "netscaler": _NETSCALER, "a10": _A10, "brocade_fos": _FOS, "ciena_saos": _SAOS, "juniper_screenos": _SCREENOS,
}
# se agregan a la lista de la familia
EXTEND: dict[str, list] = {
    "cisco_ioswlc": [_r("show ap summary", APS), _r("show wlan summary", SSIDS), _r("show wireless client summary", CLIENTS),
                     _r("show redundancy", HA)],
    "hp_comware": [_r("display irf", N_("estado del stack (IRF)"))], "h3c_comware": [_r("display irf", N_("estado del stack (IRF)"))],
    "huawei": [_r("display stack", STACK)],
    "arista_eos": [_r("show mlag", N_("estado de MLAG"))],
    "cisco_nxos": [_r("show vpc", N_("estado de vPC")), _r("show module", N_("módulos y su estado"))],
}

# ---------------------------------------------------------------- lo que OpenNCCM ya ejecuta en el equipo
_SHELLISH = re.compile(r"\$\(|\bfor\s|;|\|\s*sed|&&")


def _usable(cmd: str) -> bool:
    return bool(cmd) and len(cmd) <= 60 and not _SHELLISH.search(cmd) and "(HTTP)" not in cmd


def _base(device_type: str) -> list[tuple]:
    from . import chassis, ports, topology
    from .drivers import get_profile
    p = get_profile(device_type)
    out = []
    if p.key != device_type and device_type != "generic":
        return out
    out.append(_r(p.backup, RUN))
    if p.startup:
        out.append(_r(p.startup, N_("configuración guardada (arranque)")))
    for c in (p.inventory or "").split("\n"):
        out.append(_r(c.strip(), N_("versión, modelo y serie")))
    if p.license_cmd:
        out.append(_r(p.license_cmd, N_("licencias")))
    out += [_r(c, PORTS) for c in ports.COMMANDS.get(device_type, [])]
    out += [_r(c, ENV) for c in chassis.COMMANDS.get(device_type, [])]
    out += [_r(c, N_("vecinos (LLDP/CDP)")) for c in topology.commands(device_type)]
    return [x for x in out if _usable(x[0])]


# ---------------------------------------------------------------- variantes por modelo y versión
def version_tuple(v: str | None) -> tuple:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", v or "")
    return tuple(int(x or 0) for x in m.groups()) if m else ()


_POE = re.compile(r"(?i)poe|-\d+(?:L|F)?P(?:D|S|X)?\b|-\d+(?:L|F)?P(?:D|S|X)?-|-\d+U\b|-\d+FPD|PoE\+")
_ROS_WIFI = re.compile(r"(?i)\b(cap|hap|wap|sxt|lhg|ldf|disc|groove|metal|audience|chateau|ltap|knot|mantbox|qrt|netmetal|"
                       r"wireless|rb9\d\d|rb4011igs\+5hac)|-[25]H?n?D\b|-[25]?H?acD?\b|HacQ|\bax\d?\b")
_IOS_STACK = re.compile(r"(?i)WS-C3[678]\d\d|C9[23]00|WS-C2960(?:X|XR|S)|C9200|IE-?\d")
_IOS_ROUTER = re.compile(r"(?i)\bISR|C11\d\d|C8[235]\d\d|CISCO\d{4}|ASR|C1[01]\d\d|C8000")
_IOS_XE = re.compile(r"(?i)C9\d\d\d|ISR4|C8[235]\d\d|C11\d\d|ASR1|CAT9")


def variants(device_type: str, model: str, version: str, os_key: str) -> tuple[list, set]:
    """(comandos a agregar al principio, comandos a quitar) según el modelo y la versión."""
    add, drop = [], set()
    v, m = version_tuple(version), model or ""
    if os_key == "ros":
        if v and v[0] >= 7:
            add += [_r("/routing ospf neighbor print", OSPF), _r("/routing bgp session print", N_("sesiones BGP"))]
        elif v:
            add += [_r("/routing ospf neighbor print", OSPF), _r("/routing bgp peer print", BGP)]
        if m and _ROS_WIFI.search(m):
            add += ([_r("/interface wifi print", N_("interfaces wifi")), _r("/interface wifi registration-table print", CLIENTS)]
                    if v and v[0] >= 7 else [_r("/interface wireless registration-table print", CLIENTS)])
        elif m:
            drop.add("/interface wireless print")  # sin radio: no hay interfaces inalámbricas
        if m and _POE.search(m):
            add.append(_r("/interface ethernet poe print", POE))
        if re.search(r"(?i)\bCHR\b|x86", m):
            drop.add("/system routerboard print")  # máquina virtual o PC: no tiene RouterBOARD
    if os_key == "ios" and device_type in ("cisco_ios", "cisco_xe"):
        if _IOS_XE.search(m) or device_type == "cisco_xe" or (v and v[0] >= 16):
            add += [_r("show platform", N_("hardware y estado")), _r("show install summary", N_("paquetes instalados"))]
        if _IOS_STACK.search(m):
            add.append(_r("show switch", STACK))
        if _POE.search(m):
            add.append(_r("show power inline", POE))
        if _IOS_ROUTER.search(m):
            add.append(_r("show ip nat translations", N_("traducciones NAT")))
            drop |= {"show interfaces status", "show vlan brief", "show mac address-table", "show spanning-tree summary"}
    if os_key == "junos":
        if re.match(r"(?i)v?srx", m):
            add += [_r("show security policies", POL), _r("show security flow session summary", SESS),
                    _r("show security ipsec security-associations", VPN)]
        if re.match(r"(?i)(mx|ptx|acx)", m):
            add += [_r("show bgp summary", BGP), _r("show ospf neighbor", OSPF)]
        if re.match(r"(?i)(ex|qfx)", m):
            add.append(_r("show virtual-chassis", N_("estado del Virtual Chassis")))
            if _POE.search(m):
                add.append(_r("show poe interface", POE))
    if device_type == "fortinet" and v:
        add.append(_r("show system sdwan", N_("SD-WAN")) if v >= (6, 4) else _r("show system virtual-wan-link", N_("SD-WAN")))
    if device_type == "watchguard_fireware":
        if v and (v >= (12, 4) or v[0] >= 2025):
            add.append(_r("show sd-wan", N_("SD-WAN")))
        if re.search(r"(?i)\d+-?W\b", m):
            add.append(_r("show wireless", N_("Wi-Fi del Firebox")))
    if device_type == "vyos" and v:
        add.append(_r("show bgp summary", BGP) if v >= (1, 4) else _r("show ip bgp summary", BGP))
    if device_type in ("aruba_aoscx", "hp_procurve", "aruba_osswitch") and m and not _POE.search(m):
        drop |= {"show power-over-ethernet", "show power-over-ethernet brief"}
    return add, drop


# ---------------------------------------------------------------- lo que usan la consola y el asistente
@lru_cache(maxsize=512)
def entries(device_type: str, model: str = "", version: str = "") -> list[tuple]:
    """(comando, descripción, tipo r|c|x) del equipo, sin repetir, lo más específico primero."""
    from . import consolecore as cc
    os_key = cc.os_of(device_type)
    if device_type in TYPES:
        own = TYPES[device_type]
    elif os_key != "generic":
        own = [(c, d, "x" if cc.is_danger(os_key, c) else "c" if cc.is_change(os_key, c) else "r")
               for c, d in cc.SUGGEST.get(os_key, [])] + EXTEND.get(device_type, [])
    else:
        own = []
    add, drop = variants(device_type, model, version, os_key)
    out, seen = [], set()
    for c, d, k in add + own + _base(device_type):
        if c in seen or c in drop:
            continue
        seen.add(c)
        out.append((c, d, k))
    if not out:  # solo «Genérico SSH» (y SwitchOS, que no tiene consola): lo común a casi todos
        out = [(c, d, "r") for c, d in cc.SUGGEST["generic"]]
    return out


@lru_cache(maxsize=256)
def reads(device_type: str) -> frozenset[str]:
    """Comandos de consulta conocidos de ese tipo (en cualquier modelo o versión): el modo solo lectura los deja pasar."""
    from . import consolecore as cc
    os_key = cc.os_of(device_type)
    out = {c for c, _d, k in entries(device_type) if k == "r"}
    for model, version in (("", "6"), ("", "7"), ("", "1.3"), ("", "1.4"), ("", "6.2"), ("", "7.2"), ("", "12.9"),
                           ("SRX MX EX-48P QFX", ""), ("C9300-48P ISR4331", "17")):
        add, _drop = variants(device_type, model, version, os_key)
        out |= {c for c, _d, k in add if k == "r"}
    return frozenset(out)


FREQ = 7


def frequent(device_type: str, model: str = "", version: str = "") -> list[tuple]:
    """Los botones de comandos frecuentes: las primeras consultas del equipo y, al final, su reinicio."""
    e = entries(device_type, model, version)
    reads_ = [x for x in e if x[2] == "r"][:FREQ - 1]
    boot = [x for x in e if x[2] == "x"][:1]
    return reads_ + boot


def reference(device_type: str, model: str = "", version: str = "", limit: int = 40) -> str:
    """Los comandos del equipo para el asistente IA, una línea cada uno."""
    kinds = {"r": "consulta", "c": "cambia la configuración", "x": "corta el servicio"}
    return "\n".join(f"{c}  — {d} ({kinds[k]})" for c, d, k in entries(device_type, model, version)[:limit])
