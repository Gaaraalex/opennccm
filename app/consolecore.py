"""Lo que la consola web sabe de cada sistema: qué comandos solo consultan, cuáles cambian la configuración y cuáles
pueden cortar el servicio; cómo apagar el paginado; autocompletado y comandos frecuentes.

Los sistemas se agrupan en «familias de consola» (os_of). Lo que no se reconoce cae en «generic», donde solo se
consideran consultas los comandos claramente de lectura (show, display, ping…): en modo solo lectura todo lo demás se
bloquea, por si acaso.
"""
from __future__ import annotations

import re

from .i18n import N_

OS_LABEL = {"ros": "RouterOS", "ios": "Cisco IOS", "nxos": "NX-OS", "eos": "Arista EOS", "vrp": "Huawei VRP",
            "junos": "Junos", "exos": "EXOS", "fastpath": "EdgeSwitch", "linux": "Linux", "generic": ""}


def os_of(device_type: str) -> str:
    t = device_type or ""
    if t.startswith("mikrotik_routeros"):
        return "ros"
    if t in ("cisco_ios", "cisco_xe", "cisco_s200", "cisco_s300", "cisco_s500", "cisco_ioswlc", "ruckus_fastiron",
             "dell_os6", "dell_os9", "dell_powerconnect", "allied_telesis_awplus", "ruijie_os", "tplink_jetstream"):
        return "ios"
    if t == "cisco_nxos":
        return "nxos"
    if t == "arista_eos":
        return "eos"
    if t.startswith(("huawei", "h3c", "hp_comware")):
        return "vrp"
    if t.startswith("juniper"):
        return "junos"
    if t in ("extreme_exos", "extreme"):
        return "exos"
    if t in ("ubiquiti_edgeswitch", "ubiquiti_unifiswitch", "broadcom_icos", "netgear_prosafe"):
        return "fastpath"
    if t in ("linux", "ubuntu_server", "alpine_linux", "cumulus_linux", "opengear_linux"):
        return "linux"
    return "generic"


# para que la salida llegue entera (sin «--More--»). Son los mismos que usa netmiko al conectar.
PAGING_OFF = {"ios": "terminal length 0", "nxos": "terminal length 0", "eos": "terminal length 0",
              "fastpath": "terminal length 0", "vrp": "screen-length 0 temporary", "junos": "set cli screen-length 0",
              "exos": "disable clipaging"}
# si aun así el equipo pagina, la consola responde sola con un espacio
MORE = re.compile(r"(-+ ?\(?more\)?[^\n]*-*|--More--|---- More ----|Press any key to continue[^\n]*)\s*$", re.I)

# --------------------------------------------------------------------------- clasificación
_QUERY = {
    "ios": r"(sh(o(w)?)?|ping|trace(route)?|dir|more|terminal|who|exit|end|enable|disable|traceroute|verify)\b",
    "nxos": r"(sh(o(w)?)?|ping|trace(route)?|dir|terminal|exit|end)\b",
    "eos": r"(sh(o(w)?)?|ping|trace(route)?|dir|terminal|exit|end|enable)\b",
    "fastpath": r"(sh(o(w)?)?|ping|traceroute|dir|terminal|exit|end|enable)\b",
    "vrp": r"(dis(p(lay)?)?|ping|tracert|dir|screen-length|quit|return)\b",
    "junos": r"(show|ping|traceroute|exit|quit|file (show|list)|set cli |monitor)",
    "exos": r"(show|sh\b|ping|traceroute|disable clipaging|exit|ls)\b",
    "linux": r"(ls|cat|ip (a|addr|r|route|link)( show)?\s*$|ip -\w|ping|traceroute|uptime|df|free|ps|top -b|ss|netstat|uname|"
             r"hostname\s*$|whoami|id\s*$|journalctl|dmesg|tail|head|grep|less|more|pwd|date|who|w\s*$|systemctl status)\b",
    "generic": r"(show|sh\b|display|dis\b|ping|trace(route)?|tracert|get\b|exit|quit)\b",
}
_ROS_CHANGE = re.compile(r"(^|\s|/)(add|set|remove|enable|disable|unset|move|import|reset|reboot|shutdown|upgrade|"
                         r"reset-configuration|install|uninstall|comment|edit)(\s|$)")
_DANGER = {
    "ros": r"/system\s+(reboot|reset-configuration|shutdown|routerboard\s+upgrade)|^\s*/?(system\s+)?(reboot|shutdown)\b",
    "ios": r"^\s*(reload|write\s+erase|erase\s|format\s|delete\s|boot\s)",
    "nxos": r"^\s*(reload|write\s+erase|delete\s|init system)",
    "eos": r"^\s*(reload|write\s+erase|erase\s|delete\s|zerotouch)",
    "fastpath": r"^\s*(reload|erase\s|clear config|delete\s)",
    "vrp": r"^\s*(reboot|reset saved-configuration|delete\s|format\s)",
    "junos": r"request system (reboot|halt|power-off|zeroize)|load factory-default",
    "exos": r"^\s*(reboot|unconfigure switch|rm\s)",
    "linux": r"^\s*(sudo\s+)?(reboot|shutdown|halt|poweroff|init\s+[06]|rm\s+-rf?\s+/)",
    "generic": r"^\s*(reload|reboot|shutdown|halt|erase|format|factory)",
}
DANGER_WHY = {
    "ros": N_("reinicia o borra la configuración del equipo: corta todo el tráfico que pasa por él"),
    "linux": N_("apaga o reinicia el servidor, o borra archivos del sistema"),
}
DANGER_WHY_DEFAULT = N_("reinicia el equipo o borra su configuración: corta el servicio mientras tanto")


def is_danger(os_key: str, cmd: str) -> bool:
    return bool(re.search(_DANGER.get(os_key, _DANGER["generic"]), cmd.strip(), re.I))


def is_change(os_key: str, cmd: str) -> bool:
    """¿El comando cambia algo en el equipo? Lo dudoso cuenta como cambio (así lo bloquea el modo solo lectura)."""
    c = cmd.strip()
    if not c:
        return False
    if is_danger(os_key, c):
        return True
    if os_key == "ros":
        if c.startswith(":"):  # scripts (:put, :foreach…): pueden hacer de todo
            return not re.match(r":(put|local|global)\b", c)
        return bool(_ROS_CHANGE.search(c))
    return not re.match(_QUERY.get(os_key, _QUERY["generic"]), c, re.I)


def is_query(os_key: str, cmd: str) -> bool:
    return bool(cmd.strip()) and not is_change(os_key, cmd)


def risk(os_key: str, cmd: str) -> str:
    """read | change | outage: lo que se muestra junto a cada comando del asistente y de las recetas."""
    if is_danger(os_key, cmd):
        return "outage"
    return "change" if is_change(os_key, cmd) else "read"


def denied(cmd: str, patterns: str) -> str | None:
    """El primer comando prohibido (una línea por patrón; prefijo o expresión regular entre /…/) que coincide."""
    c = cmd.strip()
    for p in (patterns or "").splitlines():
        p = p.strip()
        if not p:
            continue
        if len(p) > 2 and p.startswith("/") and p.endswith("/"):
            try:
                if re.search(p[1:-1], c, re.I):
                    return p
            except re.error:
                continue
        elif c.lower().startswith(p.lower()):
            return p
    return None


# --------------------------------------------------------------------------- autocompletado y frecuentes
SUGGEST = {
    "ros": [
        ("/interface print", N_("lista de interfaces")), ("/interface ethernet print", N_("puertos físicos")),
        ("/interface monitor-traffic [find] once", N_("tráfico en vivo")), ("/interface bridge print", N_("bridges")),
        ("/interface bridge port print", N_("puertos de cada bridge")), ("/interface vlan print", N_("VLAN")),
        ("/interface wireless print", N_("interfaces inalámbricas")), ("/ip address print", N_("direcciones IP")),
        ("/ip route print", N_("tabla de rutas")), ("/ip arp print", N_("tabla ARP")),
        ("/ip dhcp-server lease print", N_("préstamos DHCP")), ("/ip firewall filter print", N_("reglas del firewall")),
        ("/ip firewall nat print", N_("reglas NAT")), ("/ip service print", N_("servicios (SSH, Winbox, web…)")),
        ("/ip neighbor print", N_("vecinos descubiertos")), ("/system resource print", N_("CPU, memoria y versión")),
        ("/system routerboard print", N_("placa y firmware")), ("/system identity print", N_("nombre del equipo")),
        ("/system clock print", N_("fecha y hora")), ("/log print", N_("registro")),
        ('/log print where topics~"interface"', N_("registro de interfaces")), ("/user print", N_("usuarios")),
        ("/export", N_("configuración completa")), ("/ping 8.8.8.8 count=4", N_("ping")),
        ("/tool traceroute 8.8.8.8", N_("traceroute")), ("/system reboot", N_("reiniciar el equipo")),
    ],
    "ios": [
        ("show ip interface brief", N_("resumen de interfaces")), ("show interfaces status", N_("estado de los puertos")),
        ("show interfaces", N_("detalle de los puertos")), ("show interfaces counters errors", N_("errores por puerto")),
        ("show ip route", N_("tabla de rutas")), ("show ip arp", N_("tabla ARP")),
        ("show mac address-table", N_("tabla MAC")), ("show vlan brief", N_("VLAN")),
        ("show cdp neighbors", N_("vecinos CDP")), ("show lldp neighbors", N_("vecinos LLDP")),
        ("show spanning-tree summary", N_("spanning tree")), ("show version", N_("versión y tiempo encendido")),
        ("show inventory", N_("módulos y números de serie")), ("show environment", N_("fuentes, ventiladores y temperatura")),
        ("show logging", N_("registro")), ("show running-config", N_("configuración en ejecución")),
        ("show running-config | section ", N_("una parte de la configuración")), ("show ip ssh", N_("estado de SSH")),
        ("show users", N_("sesiones abiertas")), ("show processes cpu sorted", N_("uso de CPU")),
        ("show clock", N_("fecha y hora")), ("ping 8.8.8.8", N_("ping")), ("traceroute 8.8.8.8", N_("traceroute")),
        ("configure terminal", N_("entrar en modo configuración")), ("reload", N_("reiniciar el equipo")),
    ],
    "vrp": [
        ("display interface brief", N_("resumen de interfaces")), ("display ip interface brief", N_("IP de cada interfaz")),
        ("display ip routing-table", N_("tabla de rutas")), ("display arp", N_("tabla ARP")),
        ("display mac-address", N_("tabla MAC")), ("display vlan", N_("VLAN")), ("display lldp neighbor brief", N_("vecinos LLDP")),
        ("display version", N_("versión y tiempo encendido")), ("display device", N_("tarjetas y estado")),
        ("display power", N_("fuentes de alimentación")), ("display fan", N_("ventiladores")),
        ("display logbuffer", N_("registro")), ("display current-configuration", N_("configuración en ejecución")),
        ("display users", N_("sesiones abiertas")), ("display cpu-usage", N_("uso de CPU")),
        ("ping 8.8.8.8", N_("ping")), ("tracert 8.8.8.8", N_("traceroute")), ("system-view", N_("entrar en modo configuración")),
        ("reboot", N_("reiniciar el equipo")),
    ],
    "junos": [
        ("show interfaces terse", N_("resumen de interfaces")), ("show route", N_("tabla de rutas")),
        ("show arp no-resolve", N_("tabla ARP")), ("show ethernet-switching table", N_("tabla MAC")),
        ("show vlans", N_("VLAN")), ("show lldp neighbors", N_("vecinos LLDP")), ("show version", N_("versión")),
        ("show chassis hardware", N_("módulos y números de serie")), ("show chassis environment", N_("fuentes, ventiladores y temperatura")),
        ("show log messages | last 50", N_("registro")), ("show configuration | display set", N_("configuración en formato set")),
        ("show system users", N_("sesiones abiertas")), ("show system uptime", N_("tiempo encendido")),
        ("ping 8.8.8.8 count 4", N_("ping")), ("traceroute 8.8.8.8", N_("traceroute")), ("configure", N_("entrar en modo configuración")),
        ("request system reboot", N_("reiniciar el equipo")),
    ],
    "exos": [
        ("show ports no-refresh", N_("estado de los puertos")), ("show ports information", N_("detalle de los puertos")),
        ("show vlan", N_("VLAN")), ("show ipconfig", N_("direcciones IP")), ("show iproute", N_("tabla de rutas")),
        ("show iparp", N_("tabla ARP")), ("show fdb", N_("tabla MAC")), ("show lldp neighbors", N_("vecinos LLDP")),
        ("show switch", N_("datos del switch")), ("show version", N_("versión")), ("show power", N_("fuentes de alimentación")),
        ("show fans", N_("ventiladores")), ("show management", N_("acceso: SSH, Telnet y SNMP")), ("show log", N_("registro")),
        ("show configuration", N_("configuración")), ("show session", N_("sesiones abiertas")),
        ("ping 8.8.8.8", N_("ping")), ("traceroute 8.8.8.8", N_("traceroute")), ("reboot", N_("reiniciar el equipo")),
    ],
    "fastpath": [
        ("show interfaces status all", N_("estado de los puertos")), ("show port all", N_("puertos")),
        ("show ip interface brief", N_("IP de cada interfaz")), ("show vlan brief", N_("VLAN")),
        ("show mac-addr-table", N_("tabla MAC")), ("show lldp remote-device all", N_("vecinos LLDP")),
        ("show version", N_("versión")), ("show environment", N_("fuentes y temperatura")), ("show logging buffered", N_("registro")),
        ("show running-config", N_("configuración en ejecución")), ("ping 8.8.8.8", N_("ping")),
        ("configure", N_("entrar en modo configuración")), ("reload", N_("reiniciar el equipo")),
    ],
    "linux": [
        ("ip -br addr", N_("direcciones IP")), ("ip route", N_("tabla de rutas")), ("ss -tulpn", N_("puertos abiertos")),
        ("uptime", N_("tiempo encendido y carga")), ("df -h", N_("espacio en disco")), ("free -m", N_("memoria")),
        ("journalctl -n 50 --no-pager", N_("registro")), ("systemctl --failed --no-pager", N_("servicios con fallos")),
        ("ping -c 4 8.8.8.8", N_("ping")), ("sudo reboot", N_("reiniciar el servidor")),
    ],
}
SUGGEST["nxos"] = [x for x in SUGGEST["ios"] if not x[0].startswith(("show ip ssh", "show environment"))] + [
    ("show interface brief", N_("resumen de interfaces")), ("show environment", N_("fuentes, ventiladores y temperatura"))]
SUGGEST["eos"] = SUGGEST["ios"]
SUGGEST["generic"] = [("show version", N_("versión")), ("show running-config", N_("configuración")),
                      ("display version", N_("versión (Huawei, H3C)")), ("ping 8.8.8.8", N_("ping"))]

FREQUENT = {
    "ros": ["/interface print", "/ip address print", "/ip route print", "/system resource print", "/log print", "/export",
            "/system reboot"],
    "ios": ["show ip interface brief", "show interfaces status", "show logging", "show cdp neighbors", "show version",
            "show running-config", "reload"],
    "nxos": ["show interface brief", "show ip route", "show logging", "show version", "show running-config", "reload"],
    "eos": ["show ip interface brief", "show interfaces status", "show logging", "show version", "show running-config", "reload"],
    "vrp": ["display interface brief", "display ip routing-table", "display logbuffer", "display version",
            "display current-configuration", "reboot"],
    "junos": ["show interfaces terse", "show route", "show log messages | last 50", "show version",
              "show configuration | display set", "request system reboot"],
    "exos": ["show ports no-refresh", "show vlan", "show log", "show version", "show configuration", "reboot"],
    "fastpath": ["show interfaces status all", "show vlan brief", "show logging buffered", "show version",
                 "show running-config", "reload"],
    "linux": ["ip -br addr", "ss -tulpn", "uptime", "df -h", "journalctl -n 50 --no-pager", "sudo reboot"],
    "generic": ["show version", "show running-config"],
}


def client_profile(os_key: str, tr) -> dict:
    """Lo que necesita la consola en el navegador (autocompletado, frecuentes y qué se considera cambio)."""
    return {"os": os_key, "label": OS_LABEL.get(os_key, ""),
            "suggest": [[c, tr(d)] for c, d in SUGGEST.get(os_key, SUGGEST["generic"])],
            "frequent": [[c, is_danger(os_key, c)] for c in FREQUENT.get(os_key, FREQUENT["generic"])]}
