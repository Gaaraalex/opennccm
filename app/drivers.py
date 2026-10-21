"""Perfiles de fabricantes.

Cada perfil indica a Netmiko qué driver usar y qué comandos ejecutar para
respaldar, inventariar y aplicar cambios. Para añadir un fabricante nuevo basta
con agregar un Profile a la lista PROFILES (cualquier device_type soportado por
Netmiko: https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Familias de sintaxis (se usan para generar comandos de reversión)
IOS = "ios"        # jerárquico con indentación, negación con "no "
HUAWEI = "huawei"  # jerárquico, negación con "undo "
SET = "set"        # líneas "set ..." / "delete ..." (Junos set, VyOS)
MERGE = "merge"    # sin reversión inteligente: se re-aplica la configuración completa
FASTPATH = "fastpath"  # Broadcom FastPath (Ubiquiti EdgeSwitch): bloques cerrados con «exit», sin sangría


@dataclass
class Profile:
    key: str                      # device_type de Netmiko
    label: str
    vendor: str
    backup: str                   # comando para obtener la configuración en ejecución
    startup: str | None = None    # comando para la configuración de arranque (detección de conflictos)
    inventory: str | None = "show version"  # varios comandos: uno por línea (se juntan las salidas)
    family: str = IOS
    commit: bool = False          # requiere commit tras cambios (Junos, PAN-OS, VyOS...)
    save: bool = True             # ejecutar save_config() tras aplicar cambios
    telnet: bool = False          # (obsoleto: el soporte Telnet se detecta en Netmiko en tiempo de ejecución)
    note: str | None = None       # advertencia mostrada al usuario (p. ej. comando a verificar)
    firmware: dict | None = None  # {"fs":..., "boot":[config], "exec":[exec], "reload":cmd} para actualizar firmware
    ignore: list[str] = field(default_factory=list)  # regex de líneas volátiles a descartar
    cpe: str | tuple[str, ...] | None = None  # prefijo(s) CPE para buscar CVE en NVD
    cpe_version: str | None = None  # regex que extrae de la versión del equipo la que usa NVD (p. ej. sin n.º de compilación)
    cpe_format: str | None = None   # "junos": NVD separa versión y actualización («21.4R3-S5.4» → «21.4:r3-s5»)
    cve_note: tuple[str, str] | None = None  # (clave, enlace): aviso en Vulnerabilidades si el fabricante publica sus CVE fuera de NVD
    version_re: list[str] = field(default_factory=list)
    model_re: list[str] = field(default_factory=list)
    serial_re: list[str] = field(default_factory=list)

    @property
    def inventory_label(self) -> str:
        """Comandos de inventario en una línea («show version + show switch»)."""
        return (self.inventory or "").replace("\n", " + ")


CISCO_IGNORE = [
    r"^! (Last configuration change|NVRAM config last updated|No configuration change since)",
    r"^Building configuration",
    r"^Current configuration\s*:",
    r"^ntp clock-period",
    r"^!Time:",
    r"^!Running configuration last done at",
    r"^Cryptochecksum:",
]

# Broadcom FastPath (EdgeSwitch, UniFi Switch): líneas del encabezado que cambian en cada lectura
FASTPATH_IGNORE = [r"^!System Up Time", r"^!Current SNTP Synchronized Time", r"^!Current Configuration:"]
# «show version» de FastPath usa relleno con puntos («Serial Number....... X»); parse_inventory lo convierte a «:»
FASTPATH_VERSION = [r"Software Version\s*:\s*(\S+)"]
FASTPATH_MODEL = [r"Machine Model\s*:\s*(.+)", r"Machine Type\s*:\s*(.+)"]
FASTPATH_SERIAL = [r"Serial Number\s*:\s*(\S+)"]
SEMVER3 = r"^v?(\d+\.\d+\.\d+)"  # «1.9.3.5263085» o «v2.0.9-hotfix.7» → «1.9.3», «2.0.9», como las publica NVD
VOSS_CVE_NOTE = ("extreme", "https://www.extremenetworks.com/support/security-advisories")  # (aviso, enlace)

PROFILES: list[Profile] = [
    # ---------------- Cisco ----------------
    Profile("cisco_ios", "Cisco IOS", "Cisco", "show running-config", "show startup-config",
            telnet=True, ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:ios",
            version_re=[r"Version ([\w.()]+)"], model_re=[r"[Cc]isco (\S+) .*(?:processor|bytes of memory)"],
            serial_re=[r"Processor board ID (\S+)", r"System serial number\s*:\s*(\S+)"]),
    Profile("cisco_xe", "Cisco IOS-XE", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:ios_xe",
            version_re=[r"Cisco IOS XE Software, Version (\S+)", r"Version ([\w.()]+)"],
            model_re=[r"[Cc]isco (\S+) .*(?:processor|bytes of memory)"],
            serial_re=[r"Processor board ID (\S+)", r"System serial number\s*:\s*(\S+)"]),
    Profile("cisco_nxos", "Cisco NX-OS", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:nx-os",
            version_re=[r"NXOS: version (\S+)", r"system:\s+version (\S+)"],
            model_re=[r"cisco (Nexus\S*\s*\S+)"], serial_re=[r"Processor Board ID (\S+)"]),
    Profile("cisco_xr", "Cisco IOS-XR", "Cisco", "show running-config", None, commit=True, save=False,
            telnet=True, ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:ios_xr",
            version_re=[r"Version ([\w.]+)"], model_re=[r"cisco (\S+) .*processor"]),
    Profile("cisco_asa", "Cisco ASA / Firepower (ASA)", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:adaptive_security_appliance_software",
            version_re=[r"Software Version (\S+)"], model_re=[r"Hardware:\s+(\S+?),"],
            serial_re=[r"Serial Number: (\S+)"]),
    Profile("cisco_ftd", "Cisco Firepower Threat Defense", "Cisco", "show running-config", None,
            save=False, family=MERGE, ignore=CISCO_IGNORE,
            version_re=[r"Version (\S+)"], serial_re=[r"Serial Number: (\S+)"]),
    Profile("cisco_wlc", "Cisco WLC (AireOS)", "Cisco", "show run-config commands", None,
            inventory="show sysinfo", family=MERGE,
            version_re=[r"Product Version\.+ (\S+)"], model_re=[r"Product Name\.+ (.+)"],
            serial_re=[r"Serial Number\.+ (\S+)"]),
    Profile("cisco_s300", "Cisco Small Business (SG300/350/550)", "Cisco", "show running-config",
            "show startup-config", ignore=CISCO_IGNORE,
            version_re=[r"SW version\s+(\S+)", r"Version:\s*(\S+)"]),
    # ---------------- Juniper ----------------
    Profile("juniper_junos", "Juniper Junos (formato set)", "Juniper",
            "show configuration | display set | no-more", None, family=SET, commit=True, save=False,
            telnet=True, ignore=[r"^## Last commit", r"^## Last changed"], cpe="cpe:2.3:o:juniper:junos",
            cpe_format="junos",
            version_re=[r"Junos: (\S+)", r"JUNOS .*\[(\S+)\]"], model_re=[r"Model: (\S+)"],
            serial_re=[r"Chassis\s+(\S+)"]),
    # ---------------- Arista ----------------
    Profile("arista_eos", "Arista EOS", "Arista", "show running-config", "show startup-config",
            telnet=True, ignore=[r"^! Command:", r"^! Startup-config last modified", r"^! device:"],
            cpe="cpe:2.3:o:arista:eos",
            version_re=[r"Software image version: (\S+)"], model_re=[r"^Arista (\S+)"],
            serial_re=[r"Serial number:\s+(\S+)"]),
    # ---------------- HPE / Aruba ----------------
    Profile("hp_procurve", "HPE ProCurve / Aruba OS-Switch", "HPE", "show running-config",
            "show config", inventory="show system", telnet=True,
            ignore=[r"^Running configuration:", r"^; .* Configuration Editor; Created on release"],
            version_re=[r"Software revision\s*:\s*(\S+)"], serial_re=[r"Serial Number\s*:\s*(\S+)"]),
    Profile("hp_comware", "HPE Comware / H3C", "HPE", "display current-configuration",
            "display saved-configuration", inventory="display version", family=HUAWEI, telnet=True,
            version_re=[r"Version (\S+),"], model_re=[r"(?:HPE|H3C) (\S+) uptime"]),
    Profile("aruba_aoscx", "Aruba AOS-CX", "HPE Aruba", "show running-config", "show startup-config",
            ignore=[r"^Current configuration:", r"^!Version ArubaOS-CX", r"^!export-password"],
            cpe="cpe:2.3:o:arubanetworks:arubaos-cx",
            version_re=[r"Version\s*:\s*(\S+)"], serial_re=[r"Serial Number\s*:\s*(\S+)"]),
    Profile("aruba_os", "Aruba Controller (ArubaOS)", "HPE Aruba", "show running-config", None,
            version_re=[r"Version (\S+)"]),
    # ---------------- Huawei ----------------
    Profile("huawei", "Huawei VRP", "Huawei", "display current-configuration", "display saved-configuration",
            inventory="display version", family=HUAWEI, telnet=True,
            ignore=[r"^!Software Version", r"^!Last configuration was", r"^!Last configuration was saved"],
            version_re=[r"VRP \(R\) software, Version (\S+ \([^)]+\))", r"Version (\S+)"],
            model_re=[r"HUAWEI (\S+) (?:uptime|Routing Switch)"]),
    Profile("huawei_vrpv8", "Huawei VRP v8 (CE/NE)", "Huawei", "display current-configuration", None,
            inventory="display version", family=HUAWEI, commit=True,
            ignore=[r"^!Software Version", r"^!Last configuration was"], version_re=[r"Version (\S+)"]),
    # ---------------- Firewalls ----------------
    Profile("fortinet", "Fortinet FortiGate (FortiOS)", "Fortinet", "show", None,
            inventory="get system status", family=MERGE, save=False,
            ignore=[r"^#conf_file_ver=", r"^#config-version=", r"^#buildno=", r"^#global_vdom="],
            cpe="cpe:2.3:o:fortinet:fortios",
            version_re=[r"Version: \S+ v([\d.]+)"], model_re=[r"Version: (\S+) v"],
            serial_re=[r"Serial-Number: (\S+)"]),
    Profile("paloalto_panos", "Palo Alto PAN-OS", "Palo Alto", "show config running", None,
            inventory="show system info", family=MERGE, commit=True, save=False,
            cpe="cpe:2.3:o:paloaltonetworks:pan-os",
            version_re=[r"sw-version: (\S+)"], model_re=[r"model: (\S+)"], serial_re=[r"serial: (\S+)"]),
    Profile("checkpoint_gaia", "Check Point Gaia", "Check Point", "show configuration", None,
            inventory="show version all", family=MERGE, save=True,
            ignore=[r"^# Exported by", r"^# Language version"],
            version_re=[r"Product version Check Point Gaia (\S+)"]),
    Profile("sophos_sfos", "Sophos XG / SFOS", "Sophos", "show", None, inventory=None, family=MERGE,
            save=False),
    Profile("f5_tmsh", "F5 BIG-IP (tmsh)", "F5", "list one-line", None, inventory="show sys version",
            family=MERGE, save=False, version_re=[r"Version\s+(\S+)"]),
    # ---------------- Otros switches/routers ----------------
    Profile("mikrotik_routeros", "MikroTik RouterOS", "MikroTik", "/export terse", None,
            inventory="/system resource print", family=MERGE, save=False,
            ignore=[r"^# \w{3}/\d{2}/\d{4} \d", r"^# \d{4}-\d{2}-\d{2} \d", r"^# software id"],
            cpe="cpe:2.3:o:mikrotik:routeros",
            version_re=[r"version: ([\d.]+)"], model_re=[r"board-name: (.+)"]),
    Profile("dell_os10", "Dell EMC OS10", "Dell", "show running-configuration", "show startup-configuration",
            ignore=[r"^! Version"], version_re=[r"OS Version: (\S+)"], serial_re=[r"Service Tag: (\S+)"]),
    Profile("dell_force10", "Dell Force10 / FTOS", "Dell", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, version_re=[r"Software Version:\s*(\S+)"]),
    Profile("dell_powerconnect", "Dell PowerConnect / N-Series", "Dell", "show running-config",
            "show startup-config", ignore=CISCO_IGNORE, version_re=[r"Version\s+(\S+)"]),
    Profile("extreme_exos", "Extreme EXOS", "Extreme", "show configuration", None,
            telnet=True, family=MERGE,
            ignore=[r"^# Module devmgr configuration", r"^#\s*$", r"^# .*Configuration generated"],
            # «show version» trae la serie («Switch : <n.º de parte> <serie> Rev ...») pero no el modelo,
            # que sale de «System Type» en «show switch»
            inventory="show version\nshow switch",
            version_re=[r"Image\s*:\s*ExtremeXOS version (\S+)"],
            model_re=[r"System Type:\s*(\S.*?)\s*$"],
            serial_re=[r"^Switch\s*:\s*\S+\s+(\S+)\s+Rev"]),
    # VOSS no tiene «show version»: el modelo, la serie y la versión salen de «show sys-info»
    Profile("extreme_vsp", "Extreme VSP (VOSS)", "Extreme", "show running-config", None,
            inventory="show sys-info", ignore=CISCO_IGNORE,
            cpe="cpe:2.3:a:avaya:vsp_operating_system_software", cve_note=VOSS_CVE_NOTE,  # VOSS era «Avaya VSP OS»
            version_re=[r"SysDescr\s*:.*?\((\d[\w.\-]*)\)", r"Software Version\s*:\s*(\S+)"],
            model_re=[r"ModelName\s*:\s*(\S+)", r"BoxType\s*:\s*(\S+)", r"^\s*Chassis\s*:\s*(\S+)"],
            serial_re=[r"Serial#\s*:\s*(\S+)", r"Serial Number\s*:\s*(\S+)"]),
    Profile("ruckus_fastiron", "Ruckus/Brocade ICX FastIron", "Ruckus", "show running-config",
            "show configuration", telnet=True, ignore=CISCO_IGNORE,
            version_re=[r"SW: Version (\S+)"], serial_re=[r"Serial\s+#:\s*(\S+)"]),
    Profile("nokia_sros", "Nokia SR OS", "Nokia", "admin display-config", None, family=MERGE,
            ignore=[r"^# Generated", r"^# Finished"], version_re=[r"TiMOS-\S+-(\S+)"]),
    Profile("alcatel_aos", "Alcatel-Lucent OmniSwitch", "Alcatel-Lucent", "show configuration snapshot",
            None, inventory="show system", family=MERGE, ignore=[r"^! File:"]),
    Profile("vyos", "VyOS", "VyOS", "show configuration commands", None, family=SET, commit=True,
            save=True, version_re=[r"Version:\s+VyOS (\S+)"]),
    Profile("ubiquiti_edgerouter", "Ubiquiti EdgeRouter", "Ubiquiti", "show configuration commands", None,
            family=SET, commit=True, version_re=[r"Version:\s+v(\S+)"],
            cpe=("cpe:2.3:o:ui:edgeos", "cpe:2.3:o:ui:edgemax_edgerouter_firmware"), cpe_version=SEMVER3),
    Profile("ubiquiti_edgeswitch", "Ubiquiti EdgeSwitch", "Ubiquiti", "show running-config", None,
            family=FASTPATH, ignore=CISCO_IGNORE + FASTPATH_IGNORE, version_re=FASTPATH_VERSION,
            model_re=FASTPATH_MODEL, serial_re=FASTPATH_SERIAL,
            cpe=("cpe:2.3:o:ui:edgeswitch_firmware", "cpe:2.3:o:ubnt:edgeswitch_firmware"), cpe_version=SEMVER3),
    Profile("tplink_jetstream", "TP-Link JetStream", "TP-Link", "show running-config", "show startup-config",
            inventory="show system-info", ignore=CISCO_IGNORE),
    Profile("zte_zxros", "ZTE ZXROS", "ZTE", "show running-config", None, telnet=True, ignore=CISCO_IGNORE),
    Profile("ruijie_os", "Ruijie OS", "Ruijie", "show running-config", None, telnet=True, ignore=CISCO_IGNORE),
    Profile("allied_telesis_awplus", "Allied Telesis AlliedWare Plus", "Allied Telesis",
            "show running-config", "show startup-config", ignore=CISCO_IGNORE),
    Profile("zyxel_os", "Zyxel", "Zyxel", "show running-config", None, family=MERGE),
    Profile("linux", "Linux (iptables/servidor)", "Linux", "cat /etc/network/interfaces; iptables-save",
            None, inventory="uname -a", family=MERGE, save=False, ignore=[r"^# (Generated|Completed) .* on "]),
    # Cortafuegos FreeBSD: toda la configuración está en un XML (el mismo que exporta la web en Diagnóstico →
    # Copia de seguridad). Driver propio en app/shell_drivers.py: atraviesa el menú de consola («8) Shell»).
    Profile("pfsense", "pfSense", "Netgate", "cat /cf/conf/config.xml", None,
            inventory='echo "Version: $(cat /etc/version)"; echo "Model: $(kenv -q smbios.system.product)"; '
                      'echo "Serial: $(kenv -q smbios.system.serial)"',
            family=MERGE, save=False, cpe="cpe:2.3:a:netgate:pfsense",
            version_re=[r"^Version: (\S+)"], model_re=[r"^Model: (\S.*?)\s*$"], serial_re=[r"^Serial: (\S+)"],
            note="Use el usuario admin (o uno con acceso al shell): el archivo de configuración solo lo lee root. "
                 "Los cambios y la reversión se hacen desde la web de pfSense; OpenNCCM respalda y compara."),
    Profile("opnsense", "OPNsense", "Deciso", "cat /conf/config.xml", None,
            inventory='echo "Version: $(opnsense-version -v)"; echo "Model: $(kenv -q smbios.system.product)"; '
                      'echo "Serial: $(kenv -q smbios.system.serial)"',
            family=MERGE, save=False, cpe="cpe:2.3:a:opnsense:opnsense",
            version_re=[r"^Version: (\S+)"], model_re=[r"^Model: (\S.*?)\s*$"], serial_re=[r"^Serial: (\S+)"],
            note="Use root (o un usuario con shell y permiso de lectura de /conf/config.xml). Los cambios y la "
                 "reversión se hacen desde la web de OPNsense; OpenNCCM respalda y compara."),
    Profile("alpine_linux", "Alpine Linux", "Linux",
            "for f in /etc/hostname /etc/hosts /etc/resolv.conf /etc/network/interfaces /etc/apk/world "
            "/etc/apk/repositories /etc/ssh/sshd_config; do [ -f $f ] && echo \"### $f\" && cat $f; done; "
            "rc-update show 2>/dev/null | sed 's/^/### servicio: /'; iptables-save 2>/dev/null; "
            "nft list ruleset 2>/dev/null",
            None, inventory='echo "Version: $(cat /etc/alpine-release)"; '
                            'echo "Model: $(cat /sys/class/dmi/id/product_name 2>/dev/null)"; '
                            'echo "Serial: $(cat /sys/class/dmi/id/product_serial 2>/dev/null)"',
            family=MERGE, save=False, ignore=[r"^# (Generated|Completed) .* on "],
            version_re=[r"^Version: (\S+)"], model_re=[r"^Model: (\S.*?)\s*$"], serial_re=[r"^Serial: (\S+)"],
            note="Respalda red, DNS, paquetes (apk world), servicios, SSH y cortafuegos. Con un usuario sin root "
                 "el número de serie y las reglas del cortafuegos pueden quedar vacíos."),
]

VERIFY = ("Comando tomado de la documentación del fabricante, sin verificar en hardware. Si el respaldo "
          "falla, defina un 'comando de respaldo personalizado' en el dispositivo.")

# Fabricantes adicionales soportados por Netmiko 4.x
PROFILES += [
    # Cisco (variantes)
    Profile("cisco_s200", "Cisco Small Business SG200", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("cisco_s500", "Cisco Small Business SG500/550X", "Cisco", "show running-config",
            "show startup-config", ignore=CISCO_IGNORE),
    Profile("cisco_ioswlc", "Cisco Catalyst 9800 WLC", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, cpe="cpe:2.3:o:cisco:ios_xe",
            version_re=[r"Cisco IOS XE Software, Version (\S+)"], serial_re=[r"Processor board ID (\S+)"]),
    Profile("cisco_ap", "Cisco Aironet AP (autónomo)", "Cisco", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("cisco_viptela", "Cisco SD-WAN vEdge (Viptela)", "Cisco", "show running-config", None,
            family=MERGE, commit=True, save=False, note=VERIFY),
    # Aruba / HPE / H3C alias
    Profile("aruba_osswitch", "Aruba OS-Switch (ProCurve)", "HPE Aruba", "show running-config", "show config",
            inventory="show system",
            ignore=[r"^Running configuration:", r"^; .* Configuration Editor; Created on release"],
            version_re=[r"Software revision\s*:\s*(\S+)"], serial_re=[r"Serial Number\s*:\s*(\S+)"]),
    Profile("h3c_comware", "H3C Comware", "H3C", "display current-configuration", "display saved-configuration",
            inventory="display version", family=HUAWEI, version_re=[r"Version (\S+),"]),
    # Huawei acceso/óptico
    Profile("huawei_olt", "Huawei OLT (MA5600/MA5800)", "Huawei", "display current-configuration", None,
            inventory="display version", family=MERGE, note=VERIFY),
    Profile("huawei_smartax", "Huawei SmartAX", "Huawei", "display current-configuration", None,
            inventory="display version", family=MERGE, note=VERIFY),
    # Brocade / Extreme / Avaya / Nortel / Enterasys
    Profile("brocade_netiron", "Brocade/Extreme NetIron", "Brocade", "show running-config", "show configuration",
            ignore=CISCO_IGNORE),
    Profile("brocade_nos", "Brocade VDX (NOS)", "Brocade", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("brocade_fos", "Brocade Fibre Channel (FOS)", "Brocade", "configshow", None, inventory="version",
            family=MERGE, save=False, version_re=[r"Fabric OS:\s+v(\S+)"]),
    Profile("extreme_ers", "Extreme/Avaya/Nortel ERS (BayStack)", "Extreme", "show running-config", None,
            inventory="show sys-info", ignore=CISCO_IGNORE, version_re=[r"SW:v(\S+)"],
            model_re=[r"(?:Ethernet Routing Switch|Ethernet Switch) (\S+)"]),
    Profile("extreme_slx", "Extreme SLX", "Extreme", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("extreme_nos", "Extreme NOS (VDX)", "Extreme", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("extreme_wing", "Extreme WiNG (Motorola/Zebra)", "Extreme", "show running-config", None,
            ignore=CISCO_IGNORE),
    Profile("extreme", "Extreme ExtremeWare (Summit/Alpine/BlackDiamond)", "Extreme", "show configuration",
            None, family=MERGE),
    Profile("enterasys", "Extreme Enterasys (SecureStack/Matrix)", "Extreme", "show config", None,
            family=MERGE, note=VERIFY),
    # Balanceadores / ADC
    Profile("a10", "A10 Networks Thunder/AX", "A10 Networks", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE, version_re=[r"version (\S+),"]),
    Profile("netscaler", "Citrix ADC / NetScaler", "Citrix", "show ns runningConfig", None,
            inventory="show ns version", family=MERGE, version_re=[r"NetScaler NS(\S+)"]),
    # Proveedores de servicio / óptico
    Profile("adtran_os", "Adtran NetVanta / AOS", "Adtran", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("adva_fsp150f2", "ADVA FSP150CC (F2)", "ADVA", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("adva_fsp150f3", "ADVA FSP150CC (F3)", "ADVA", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("ciena_saos", "Ciena SAOS 6/8", "Ciena", "configuration show", None, inventory="chassis show",
            family=MERGE),
    Profile("ciena_saos10", "Ciena SAOS 10", "Ciena", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("calix_b6", "Calix B6", "Calix", "show running-config", None, family=MERGE, note=VERIFY),
    Profile("calix_exa", "Calix E-Series (EXA)", "Calix", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("ericsson_ipos", "Ericsson IPOS (Redback/SmartEdge)", "Ericsson", "show configuration", None,
            family=MERGE, commit=True, save=False),
    Profile("ericsson_mltn63", "Ericsson MINI-LINK 6352/63xx", "Ericsson", "show running-config", None,
            family=MERGE, note=VERIFY),
    Profile("ericsson_mltn66", "Ericsson MINI-LINK 6600", "Ericsson", "show running-config", None,
            family=MERGE, note=VERIFY),
    Profile("ipinfusion_ocnos", "IP Infusion OcNOS", "IP Infusion", "show running-config", None,
            commit=True, ignore=CISCO_IGNORE),
    Profile("infinera_packet", "Infinera (Packet)", "Infinera", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("coriant", "Coriant 8600", "Coriant", "show running-config", None, family=MERGE, note=VERIFY),
    Profile("nokia_srl", "Nokia SR Linux", "Nokia", "info flat", None, family=MERGE, commit=True,
            save=False, note=VERIFY),
    Profile("nokia_isam", "Nokia ISAM (OLT)", "Nokia", "info configure flat", None, family=MERGE,
            note=VERIFY),
    Profile("raisecom_roap", "Raisecom (ROAP)", "Raisecom", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("raisecom_ros", "Raisecom (ROS)", "Raisecom", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("casa_cmts", "Casa Systems CMTS", "Casa", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("arris_cer", "Arris/CommScope E6000 CER", "Arris", "show running-config", None,
            ignore=CISCO_IGNORE, note=VERIFY),
    # Switches de campus/datacenter de otros fabricantes
    Profile("dell_os6", "Dell EMC N-Series (OS6)", "Dell", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("dell_os9", "Dell EMC S/Z-Series (OS9)", "Dell", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("dell_sonic", "Dell Enterprise SONiC", "Dell", "show running-configuration", None, family=MERGE),
    Profile("edgecore_sonic", "Edge-Core SONiC", "Edge-Core", "show runningconfiguration all", None,
            family=MERGE, save=False),
    Profile("cumulus_linux", "NVIDIA Cumulus Linux 5.x (NVUE)", "NVIDIA", "nv config show -o commands", None,
            family=MERGE, save=False, inventory="nv show system"),
    Profile("mellanox_mlnxos", "NVIDIA/Mellanox Onyx (MLNX-OS)", "NVIDIA", "show running-config", None,
            ignore=[r"^## Running database", r"^## Generated at", r"^##$"], family=MERGE),
    Profile("dlink_ds", "D-Link DES/DGS/DXS", "D-Link", "show config current_config", None,
            inventory="show switch", family=MERGE),
    Profile("netgear_prosafe", "Netgear ProSafe / M-Series", "Netgear", "show running-config", None,
            ignore=CISCO_IGNORE),
    Profile("fiberstore_fsos", "FS (FSOS)", "FS", "show running-config", "show startup-config",
            ignore=CISCO_IGNORE),
    Profile("fiberstore_fsosv2", "FS (FSOS v2)", "FS", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("fiberstore_networkos", "FS (NetworkOS)", "FS", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("hirschmann_hios", "Belden Hirschmann HiOS", "Hirschmann", "show running-config-script", None,
            family=MERGE),
    Profile("moxa_nos", "Moxa", "Moxa", "show running-config", None, ignore=CISCO_IGNORE, note=VERIFY),
    Profile("lancom_lcossx4", "LANCOM LCOS SX 4", "LANCOM", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("lancom_lcossx5", "LANCOM LCOS SX 5", "LANCOM", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("maipu", "Maipu", "Maipu", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("alaxala_ax26s", "Alaxala AX26S", "Alaxala", "show running-config", None, family=MERGE),
    Profile("alaxala_ax36s", "Alaxala AX36S/AX3600", "Alaxala", "show running-config", None, family=MERGE),
    Profile("apresia_aeos", "Hitachi Cable Apresia", "Hitachi", "show running-config", None, family=MERGE,
            note=VERIFY),
    Profile("nec_ix", "NEC UNIVERGE IX", "NEC", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("yamaha", "Yamaha RTX", "Yamaha", "show config", None, inventory="show environment",
            family=MERGE),
    Profile("iij_seilos", "IIJ SEIL", "IIJ", "show config", None, family=MERGE, note=VERIFY),
    Profile("furukawa_fitelnet", "Furukawa FITELnet", "Furukawa", "show running-config", None,
            ignore=CISCO_IGNORE, note=VERIFY),
    Profile("fujitsu_sir", "Fujitsu Si-R", "Fujitsu", "show candidate-config", None, family=MERGE,
            note=VERIFY),
    Profile("centec_os", "Centec", "Centec", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("broadcom_icos", "Broadcom ICOS (white box)", "Broadcom", "show running-config", None,
            ignore=CISCO_IGNORE),
    Profile("quanta_mesh", "Quanta", "Quanta", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("supermicro_smis", "Supermicro", "Supermicro", "show running-config", None, ignore=CISCO_IGNORE),
    Profile("ubiquiti_unifiswitch", "Ubiquiti UniFi Switch", "Ubiquiti", "show running-config", None,
            ignore=CISCO_IGNORE + FASTPATH_IGNORE, version_re=FASTPATH_VERSION, model_re=FASTPATH_MODEL,
            serial_re=FASTPATH_SERIAL, cpe="cpe:2.3:o:ui:unifi_switch_firmware", cpe_version=SEMVER3),
    Profile("eltex", "Eltex MES", "Eltex", "show running-config", "show startup-config", ignore=CISCO_IGNORE),
    Profile("eltex_esr", "Eltex ESR", "Eltex", "show running-config", None, commit=True, ignore=CISCO_IGNORE),
    Profile("ekinops_ek360", "Ekinops 360", "Ekinops", "show running-config", None, family=MERGE, note=VERIFY),
    Profile("oneaccess_oneos", "OneAccess / Ekinops OneOS", "OneAccess", "show running-config", None,
            ignore=CISCO_IGNORE),
    Profile("teldat_cit", "Teldat", "Teldat", "show config", None, family=MERGE, note=VERIFY),
    Profile("digi_transport", "Digi TransPort", "Digi", "type config.da0", None, family=MERGE, note=VERIFY),
    Profile("garderos_grs", "Siemens Garderos GRS", "Siemens", "show configuration running", None,
            family=MERGE, note=VERIFY),
    Profile("audiocode_66", "AudioCodes Mediant (6.6)", "AudioCodes", "show running-config", None,
            family=MERGE, note=VERIFY),
    Profile("audiocode_72", "AudioCodes Mediant (7.2)", "AudioCodes", "show running-config", None,
            family=MERGE, note=VERIFY),
    # Seguridad
    Profile("juniper_screenos", "Juniper NetScreen / ScreenOS", "Juniper", "get config", None,
            inventory="get system", family=MERGE),
    Profile("hillstone_stoneos", "Hillstone StoneOS", "Hillstone", "show configuration", None,
            family=MERGE, note=VERIFY),
    # Fireware CLI: «show sysinfo» trae modelo, serie y versión («12.10.3.B704297» → NVD usa «12.10.3»)
    Profile("watchguard_fireware", "WatchGuard Fireware", "WatchGuard", "show running-config", None,
            inventory="show sysinfo", family=MERGE, save=False, note=VERIFY,
            cpe="cpe:2.3:o:watchguard:fireware", cpe_version=r"^v?(\d+\.\d+(?:\.\d+)?)",
            version_re=[r"(?i)(?:software|fireware|os|system)[ _-]*version\s*[:=]?\s*v?(\d+\.\d+[\w.]*)",
                        r"(?i)Fireware(?: OS)?\s+v?(\d+\.\d+[\w.]*)"],
            model_re=[r"(?im)^\s*(?:model|system model|appliance model)\s*[:=]\s*(\S+)"],
            serial_re=[r"(?im)^\s*serial[ _-]*(?:number|no\.?)?\s*[:=]\s*(\S+)"]),
    # Consolas / acceso fuera de banda
    Profile("opengear_linux", "Opengear", "Opengear", "config -g config", None, inventory="cat /etc/version",
            family=MERGE, save=False),
    Profile("cloudgenix_ion", "Prisma SD-WAN (CloudGenix ION)", "Palo Alto", "dump config", None,
            family=MERGE, save=False, note=VERIFY),
    # Genérico: para cualquier equipo con CLI por SSH (SonicWall, Riverbed, etc.)
    Profile("generic", "Genérico SSH (defina el comando de respaldo)", "Genérico", "show running-config",
            None, inventory=None, family=MERGE, save=False,
            note="Defina en el dispositivo el 'comando de respaldo personalizado' (el que muestra la "
                 "configuración completa en ese equipo)."),
]

# Firmware: transferencia por SCP (netmiko.file_transfer) + comandos de arranque
_FW = {
    "cisco_ios": {"fs": "flash:", "boot": ["no boot system", "boot system flash:{file}"], "reload": "reload"},
    "cisco_xe": {"fs": "bootflash:", "boot": ["no boot system", "boot system bootflash:{file}"],
                 "reload": "reload"},
    "cisco_nxos": {"fs": "bootflash:", "boot": ["boot nxos bootflash:{file}"], "reload": "reload"},
    "cisco_asa": {"fs": "disk0:", "boot": ["boot system disk0:/{file}"], "reload": "reload"},
    "arista_eos": {"fs": "flash:", "boot": ["boot system flash:{file}"], "reload": "reload"},
    "juniper_junos": {"fs": "/var/tmp", "exec": ["request system software add /var/tmp/{file} no-validate"],
                      "reload": "request system reboot"},
}

PROFILE_MAP = {p.key: p for p in PROFILES}
for _k, _fw in _FW.items():
    PROFILE_MAP[_k].firmware = _fw

GENERIC_VERSION = [r"[Vv]ersion[:\s]+([\w.()\-]+)"]
# [ \t]* y no \s*: un valor vacío («Model: ») no debe tomar la línea siguiente como valor
GENERIC_MODEL = [r"[Mm]odel(?: [Nn]umber)?[ \t]*[:=][ \t]*(\S+)", r"Hardware[ \t]*:[ \t]*(\S+)"]
GENERIC_SERIAL = [r"[Ss]erial[ _-]?(?:[Nn]umber|[Nn]o\.?|#)[ \t]*[:=]?[ \t]*(\S+)", r"Processor board ID (\S+)"]


def telnet_supported(key: str) -> bool:
    from netmiko.ssh_dispatcher import CLASS_MAPPER
    return f"{key}_telnet" in CLASS_MAPPER


def scp_supported(key: str) -> bool:
    from netmiko.ssh_dispatcher import FILE_TRANSFER_MAP
    return key in FILE_TRANSFER_MAP


def unknown_profiles() -> list[str]:
    """device_types de PROFILES que la versión instalada de Netmiko no conoce."""
    from netmiko.ssh_dispatcher import CLASS_MAPPER
    return [p.key for p in PROFILES if p.key not in CLASS_MAPPER]


def get_profile(key: str) -> Profile:
    return PROFILE_MAP.get(key) or Profile(key, key, "Genérico", "show running-config", family=MERGE)


# Líneas de comentario con fecha/hora o contadores que muchos fabricantes añaden al mostrar la configuración.
# Se quitan en todas las plataformas: no son configuración y generarían un «cambio» en cada respaldo.
GLOBAL_IGNORE = [
    r"^!System Up Time", r"^!Current SNTP Synchronized Time",                     # Broadcom FastPath, Dell, Netgear
    r"^## Last (commit|changed)", r"^## Generated at ",                           # Junos, Mellanox
    r"^!Last configuration was (updated|saved)",                                  # Huawei
    r"^# \w{3}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} by RouterOS",                        # MikroTik 6.x
    r"^# \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} by RouterOS",                        # MikroTik 7.x
    r"^#conf_file_ver=",                                                          # FortiGate
    r"^# Exported by \S+ on ",                                                    # Check Point Gaia
    r"^#\s*(Generated|Finished|Completed) .*\d{1,2}:\d{2}:\d{2}",                 # Nokia SR OS y otros
    r"^! Startup-config last modified",                                           # Arista
    r"^: Written by ",                                                            # Cisco ASA
    r"^!\s*(Last configuration change|NVRAM config last updated) at ",            # Cisco y clones IOS
    r"^!\s*Time: ",                                                               # Cisco NX-OS
    r"Command Execution Time:",                                                   # Extreme VOSS / EXOS
    r"^#\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +\d{1,2} "
    r"\d{2}:\d{2}:\d{2} \d{4}",                                                  # Extreme VOSS: «# Sun Oct 18 …»
]
# Valores que el equipo vuelve a cifrar con sal aleatoria en cada lectura (no son un cambio real).
# Solo se enmascaran para decidir si hubo cambios: la versión guardada conserva el texto original.
GLOBAL_MASK = [
    r"(\bENC )\S+",                      # FortiGate: «set password ENC <base64>» cambia en cada «show»
]
_GLOBAL_IGNORE_RE = [re.compile(p) for p in GLOBAL_IGNORE]
_GLOBAL_MASK_RE = [re.compile(p) for p in GLOBAL_MASK]


def compare_text(text: str) -> str:
    """Texto normalizado para detectar cambios (sin valores cifrados con sal aleatoria)."""
    for p in _GLOBAL_MASK_RE:
        text = p.sub(r"\1***", text)
    return text


def clean_config(text: str, profile: Profile) -> str:
    """Quita líneas volátiles (marcas de tiempo, checksums) para evitar falsos cambios."""
    pats = [re.compile(p) for p in profile.ignore] + _GLOBAL_IGNORE_RE
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if any(p.search(line) for p in pats):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip() + "\n"


def _first(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        for m in re.finditer(p, text, re.MULTILINE):
            value = next((g for g in m.groups() if g), m.group(0)).strip()[:120]
            if re.search(r"[A-Za-z0-9]", value):  # descarta «.......» u otros rellenos
                return value
    return None


def parse_inventory(text: str, profile: Profile) -> dict:
    # «Serial Number.................. ABC» (FastPath, Dell, Netgear...) -> «Serial Number: ABC»
    text = re.sub(r"(?m)^(\s*[A-Za-z][\w /()-]*?)\s*\.{3,}\s*", r"\1: ", text)
    return {
        "os_version": _first(profile.version_re + GENERIC_VERSION, text),
        "model": _first(profile.model_re + GENERIC_MODEL, text),
        "serial": _first(profile.serial_re + GENERIC_SERIAL, text),
    }


# drivers propios (pfSense, OPNsense, Alpine) que Netmiko no trae
from . import shell_drivers  # noqa: E402

shell_drivers.register()
