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
            "junos": "Junos", "exos": "EXOS", "voss": "VOSS / Fabric Engine", "fastpath": "EdgeSwitch", "linux": "Linux",
            "edgeos": "EdgeOS / VyOS", "airos": "airOS / UniFi AP", "zxros": "ZTE ZXR10", "zteolt": "ZTE OLT",
            "comware": "H3C / HPE Comware", "huaweiolt": "Huawei OLT", "fortios": "FortiOS", "panos": "PAN-OS",
            "asa": "Cisco ASA", "ftd": "Cisco FTD", "iosxr": "IOS-XR", "procurve": "ProCurve / Aruba OS-Switch",
            "aoscx": "Aruba AOS-CX", "ioslike": "CLI tipo Cisco", "dellos10": "Dell OS10", "sonic": "SONiC", "sros": "Nokia SR OS",
            "srl": "Nokia SR Linux", "omniswitch": "OmniSwitch AOS", "enterasys": "Enterasys", "zyxel": "Zyxel", "pfsense": "pfSense", "opnsense": "OPNsense",
            "gaia": "Check Point Gaia", "tmsh": "F5 BIG-IP", "netscaler": "NetScaler", "aireos": "Cisco WLC", "arubaos": "ArubaOS",
            "sfos": "Sophos SFOS", "viptela": "Cisco SD-WAN vEdge", "bfos": "Brocade FOS", "wing": "Extreme WiNG",
            "adva": "ADVA FSP 150CC", "saos": "Ciena SAOS 6/8", "saos10": "Ciena SAOS 10", "calixexa": "Calix EXA",
            "ipos": "Ericsson IPOS", "mltn": "Ericsson MINI-LINK", "coriant": "Coriant 8600", "isam": "Nokia ISAM",
            "dlink": "D-Link", "hios": "Hirschmann HiOS", "yamaha": "Yamaha RTX", "seil": "IIJ SEIL", "sir": "Fujitsu Si-R",
            "esr": "Eltex ESR", "ekinops": "Ekinops 360", "teldat": "Teldat", "digi": "Digi TransPort",
            "garderos": "Siemens Garderos", "audiocodes": "AudioCodes Mediant", "screenos": "ScreenOS",
            "stoneos": "Hillstone StoneOS", "fireware": "WatchGuard Fireware", "ion": "Prisma SD-WAN ION",
            "generic": ""}


# Tipos de equipo que tienen familia propia desde 2.30 (antes «generic»): los favoritos de la consola ya guardados
# usan la clave de antes (el fabricante), para que nadie pierda los suyos (app.favorites.family_of).
FAV_LEGACY = frozenset({"adva_fsp150f2", "adva_fsp150f3", "alaxala_ax26s", "alaxala_ax36s", "apresia_aeos", "audiocode_66", "audiocode_72", "brocade_fos", "calix_b6", "calix_exa", "ciena_saos", "ciena_saos10", "cisco_viptela", "cloudgenix_ion", "coriant", "digi_transport", "dlink_ds", "ekinops_ek360", "eltex_esr", "ericsson_ipos", "ericsson_mltn63", "ericsson_mltn66", "extreme_wing", "fujitsu_sir", "garderos_grs", "hillstone_stoneos", "hirschmann_hios", "iij_seilos", "infinera_packet", "juniper_screenos", "nec_ix", "nokia_isam", "sophos_sfos", "teldat_cit", "watchguard_fireware", "yamaha"})


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
    if t in ("huawei_olt", "huawei_smartax"):
        return "huaweiolt"
    if t.startswith(("h3c", "hp_comware")):
        return "comware"
    if t.startswith("huawei"):
        return "vrp"
    if t in ("juniper_junos", "juniper"):
        return "junos"
    if t in ("extreme_exos", "extreme"):
        return "exos"
    if t in ("extreme_vsp", "extreme_ers"):
        return "voss"
    if t in ("ubiquiti_edgeswitch", "ubiquiti_unifiswitch", "broadcom_icos", "netgear_prosafe"):
        return "fastpath"
    if t in ("ubiquiti_edgerouter", "ubiquiti_usg", "vyos"):
        return "edgeos"
    if t in ("ubiquiti_airos", "ubiquiti_airfiber", "ubiquiti_unifi_ap"):
        return "airos"
    if t == "zte_zxros":
        return "zxros"
    if t in ("zte_olt", "zte_olt_titan"):
        return "zteolt"
    fam = {"fortinet": "fortios", "paloalto_panos": "panos", "cisco_asa": "asa", "cisco_ftd": "ftd", "cisco_xr": "iosxr",
           "hp_procurve": "procurve", "aruba_osswitch": "procurve", "aruba_aoscx": "aoscx", "cisco_ap": "ios",
           "dell_os10": "dellos10", "dell_sonic": "sonic", "edgecore_sonic": "sonic", "nokia_sros": "sros", "nokia_srl": "srl",
           "alcatel_aos": "omniswitch", "enterasys": "enterasys", "zyxel_os": "zyxel", "pfsense": "pfsense", "opnsense": "opnsense",
           "checkpoint_gaia": "gaia", "f5_tmsh": "tmsh", "netscaler": "netscaler", "cisco_wlc": "aireos", "aruba_os": "arubaos",
           "sophos_sfos": "sfos", "cisco_viptela": "viptela", "brocade_fos": "bfos", "extreme_wing": "wing",
           "adva_fsp150f2": "adva", "adva_fsp150f3": "adva", "ciena_saos": "saos", "ciena_saos10": "saos10",
           "calix_exa": "calixexa", "ericsson_ipos": "ipos", "ericsson_mltn63": "mltn", "ericsson_mltn66": "mltn",
           "coriant": "coriant", "nokia_isam": "isam", "dlink_ds": "dlink", "hirschmann_hios": "hios", "yamaha": "yamaha",
           "iij_seilos": "seil", "fujitsu_sir": "sir", "eltex_esr": "esr", "ekinops_ek360": "ekinops", "teldat_cit": "teldat",
           "digi_transport": "digi", "garderos_grs": "garderos", "audiocode_66": "audiocodes", "audiocode_72": "audiocodes",
           "juniper_screenos": "screenos", "hillstone_stoneos": "stoneos", "watchguard_fireware": "fireware",
           "cloudgenix_ion": "ion",
           **{k: "ioslike" for k in ("dell_force10", "brocade_netiron", "extreme_slx", "extreme_nos", "brocade_nos", "ipinfusion_ocnos",
                                    "raisecom_roap", "raisecom_ros", "casa_cmts", "arris_cer", "mellanox_mlnxos", "fiberstore_fsos",
                                    "fiberstore_fsosv2", "fiberstore_networkos", "moxa_nos", "lancom_lcossx4", "lancom_lcossx5", "maipu",
                                    "centec_os", "quanta_mesh", "supermicro_smis", "eltex", "adtran_os", "a10", "oneaccess_oneos",
                                    "furukawa_fitelnet", "calix_b6", "alaxala_ax26s", "alaxala_ax36s",
                                    "apresia_aeos", "infinera_packet", "nec_ix")}}.get(t)
    if fam:
        return fam
    if t in ("linux", "ubuntu_server", "alpine_linux", "cumulus_linux", "opengear_linux"):
        return "linux"
    return "generic"


# para que la salida llegue entera (sin «--More--»). Son los mismos que usa netmiko al conectar.
PAGING_OFF = {"ios": "terminal length 0", "nxos": "terminal length 0", "eos": "terminal length 0",
              "fastpath": "terminal length 0", "vrp": "screen-length 0 temporary", "junos": "set cli screen-length 0",
              "exos": "disable clipaging", "voss": "terminal more disable", "zxros": "terminal length 0",
              "zteolt": "terminal length 0", "edgeos": "terminal length 0", "comware": "screen-length disable",
              "huaweiolt": "scroll 512"}
# prompt de modo configuración: mientras se vea, el cambio aún no terminó (el aviso espera a que se salga)
CONFIG_PROMPT = {"ios": r"\(config[^)]*\)#", "nxos": r"\(config[^)]*\)#", "eos": r"\(config[^)]*\)#", "voss": r"\(config[^)]*\)#",
                 "fastpath": r"\(Config|\(Interface|\(Vlan", "vrp": r"^\[[^\]]+\]", "junos": r"\[edit", "exos": r"$^",
                 "zxros": r"\(config[^)]*\)#", "zteolt": r"\(config[^)]*\)#|\(gpon-onu-mng", "edgeos": r"@[^#\s]+#\s*$", "comware": r"^\[[^\]]+\]",
                 "huaweiolt": r"\(config[^)]*\)#"}


def in_config(os_key: str, prompt_line: str) -> bool:
    p = CONFIG_PROMPT.get(os_key)
    return bool(p and re.search(p, prompt_line.strip()))


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
    "voss": r"(show|sh\b|ping|traceroute|terminal|enable|exit|end)\b",
    "linux": r"(ls|cat|ip (a|addr|r|route|link)( show)?\s*$|ip -\w|ping|traceroute|uptime|df|free|ps|top -b|ss|netstat|uname|"
             r"hostname\s*$|whoami|id\s*$|journalctl|dmesg|tail|head|grep|less|more|pwd|date|who|w\s*$|systemctl status)\b",
    "generic": r"(show|sh\b|display|dis\b|ping|trace(route)?|tracert|get\b|exit|quit)\b",
}
# pfSense y OPNsense: shell de FreeBSD (ifconfig o sysctl con argumentos de cambio, o pfctl fuera de «-s», son cambios)
_QUERY["pfsense"] = _QUERY["opnsense"] = (
    r"(ls|cat|ifconfig(\s+-a)?(\s+[\w.]+)?\s*$|netstat|sockstat|pfctl\s+-v*s|arp\s+-an?\s*$|df|uptime|tail|head|grep|"
    r"ping|traceroute|uname|ps|top\s+-b|date|who|w\s*$|pwd|id\s*$|whoami|hostname\s*$|kenv\s+-q|sysctl\s+[\w.]+\s*$|"
    r"opnsense-version|pkg\s+(info|version)|exit)\b")
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
    "voss": r"^\s*(reset|boot\s+(?!config)|sys\s+shutdown|delete\s)",
    "linux": r"^\s*(sudo\s+)?(reboot|shutdown|halt|poweroff|init\s+[06]|rm\s+-rf?\s+/)",
    "generic": r"^\s*(reload|reboot|shutdown|halt|erase|format|factory)",
}
_DANGER["pfsense"] = _DANGER["opnsense"] = (r"^\s*(reboot|shutdown|halt|poweroff|rm\s+-rf?\s+/|pfctl\s+-(d|F\s*all)\b|"
                                           r"opnsense-(update|revert)|/etc/rc\.reload_all)")
DANGER_WHY = {
    "ros": N_("reinicia o borra la configuración del equipo: corta todo el tráfico que pasa por él"),
    "linux": N_("apaga o reinicia el servidor, o borra archivos del sistema"),
}
DANGER_WHY["pfsense"] = DANGER_WHY["opnsense"] = N_("reinicia o apaga el cortafuegos, apaga pf o borra archivos: corta el tráfico que pasa por él")
DANGER_WHY_DEFAULT = N_("reinicia el equipo o borra su configuración: corta el servicio mientras tanto")


def _norm(cmd: str) -> str:
    return " ".join(cmd.split())


def _known(device_type: str | None, kind: str) -> frozenset[str] | set[str]:
    """Comandos del catálogo de ese tipo de equipo (app.cmdref) de ese tipo: r consulta, x corta el servicio."""
    if not device_type:
        return set()
    from . import cmdref
    if kind == "r":
        return cmdref.reads(device_type)
    return {c for c, _d, k in cmdref.entries(device_type) if k == kind}


def is_danger(os_key: str, cmd: str, device_type: str | None = None) -> bool:
    return bool(re.search(_DANGER.get(os_key, _DANGER["generic"]), cmd.strip(), re.I)) or _norm(cmd) in _known(device_type, "x")


def is_change(os_key: str, cmd: str, device_type: str | None = None) -> bool:
    """¿El comando cambia algo en el equipo? Lo dudoso cuenta como cambio (así lo bloquea el modo solo lectura). Los
    comandos de consulta del catálogo del tipo de equipo (app.cmdref) son consultas aunque la familia no los reconozca."""
    c = cmd.strip()
    if not c:
        return False
    if is_danger(os_key, c, device_type):
        return True
    if _norm(c) in _known(device_type, "r"):
        return False
    if os_key == "ros":
        if c.startswith(":"):  # scripts (:put, :foreach…): pueden hacer de todo
            return not re.match(r":(put|local|global)\b", c)
        return bool(_ROS_CHANGE.search(c))
    return not re.match(_QUERY.get(os_key, _QUERY["generic"]), c, re.I)


def is_query(os_key: str, cmd: str, device_type: str | None = None) -> bool:
    return bool(cmd.strip()) and not is_change(os_key, cmd, device_type)


def risk(os_key: str, cmd: str, device_type: str | None = None) -> str:
    """read | change | outage: lo que se muestra junto a cada comando del asistente y de las recetas."""
    if is_danger(os_key, cmd, device_type):
        return "outage"
    return "change" if is_change(os_key, cmd, device_type) else "read"


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
    "voss": [
        ("show interfaces gigabitEthernet", N_("puertos")), ("show vlan basic", N_("VLAN")), ("show ip interface", N_("IP de cada interfaz")),
        ("show ip route", N_("tabla de rutas")), ("show isis adjacencies", N_("vecinos de la fábrica (IS-IS)")),
        ("show lldp neighbor", N_("vecinos LLDP")), ("show sys-info", N_("datos del switch")), ("show software", N_("versión")),
        ("show logging file tail", N_("registro")), ("show running-config", N_("configuración en ejecución")),
        ("show boot config flags", N_("indicadores de arranque (SSH, web…)")), ("ping 8.8.8.8", N_("ping")),
        ("configure terminal", N_("entrar en modo configuración")), ("reset", N_("reiniciar el equipo")),
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
    "voss": ["show interfaces gigabitEthernet", "show vlan basic", "show isis adjacencies", "show sys-info", "show running-config", "reset"],
    "fastpath": ["show interfaces status all", "show vlan brief", "show logging buffered", "show version",
                 "show running-config", "reload"],
    "linux": ["ip -br addr", "ss -tulpn", "uptime", "df -h", "journalctl -n 50 --no-pager", "sudo reboot"],
    "generic": ["show version", "show running-config"],
}


def client_profile(os_key: str, tr, dev=None) -> dict:
    """Lo que necesita la consola en el navegador: autocompletado y frecuentes. Con el equipo (dev), los de su tipo,
    modelo y versión de firmware (app.cmdref); el historial de comandos se guarda por tipo de equipo («key»)."""
    if dev is None:
        return {"os": os_key, "key": os_key, "label": OS_LABEL.get(os_key, ""),
                "suggest": [[c, tr(d)] for c, d in SUGGEST.get(os_key, SUGGEST["generic"])],
                "frequent": [[c, is_danger(os_key, c)] for c in FREQUENT.get(os_key, FREQUENT["generic"])]}
    from . import cmdref
    from .drivers import get_profile
    t = dev.device_type or "generic"
    label = " ".join(x for x in (get_profile(t).label, dev.model or "", dev.os_version or "") if x)
    return {"os": os_key, "key": t, "label": label,
            "suggest": [[c, tr(d)] for c, d, _k in cmdref.entries(t, dev.model or "", dev.os_version or "")],
            "frequent": [[c, k == "x"] for c, _d, k in cmdref.frequent(t, dev.model or "", dev.os_version or "")]}
