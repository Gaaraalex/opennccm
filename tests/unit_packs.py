"""Paquetes de cumplimiento de Extreme VOSS y Ubiquiti: una configuración segura los pasa todos, una configuración de
fábrica falla donde debe, y los paquetes nuevos llegan una sola vez a las instalaciones que se actualizan."""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.compliance_packs import ADDED_LATER, PACKS, seed_new_packs, seed_packs  # noqa: E402
from app.db import Policy, SessionLocal, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def failed_rules(pack, text):
    out = []
    for name, kind, pattern, _sev, _rem in PACKS[pack][2]:
        found = re.search(pattern, text, re.MULTILINE) is not None
        if found != (kind == "must_contain"):
            out.append(name)
    return out


VOSS = "Extreme VOSS · Seguridad básica"
UBNT = "Ubiquiti EdgeSwitch/UniFi · Seguridad básica"

VOSS_SECURE = """#
# Sun Oct 18 01:30:01 2026 UTC
# box type             : 5520-24T-FabricEngine
# software version     : 9.3.0.0
#
config terminal
#
# BOOT CONFIGURATION
#
no boot config flags telnetd
#
# CLI CONFIGURATION
#
cli timeout 600
banner custom
banner motd "Acceso solo para personal autorizado"
banner displaymotd
#
# SNMP V3 GLOBAL CONFIGURATION
#
snmp-server community "Red-Lectura-7x" index first secname "readview"
#
# WEB CONFIGURATION
#
web-server enable
web-server secure-only
#
# SSH CONFIGURATION
#
ssh
#
# RADIUS CONFIGURATION
#
radius server host 192.0.2.20 key ****** used-by cli
radius enable
#
# NTP CONFIGURATION
#
ntp server 192.0.2.1 enable
ntp
#
# SYSLOG CONFIGURATION
#
syslog host 1 address 192.0.2.10
syslog host 1 enable
end
"""
VOSS_DEFAULT = """config terminal
boot config flags telnetd
boot config flags ftpd
boot config flags tftpd
cli timeout 3600
snmp-server community "public" index first secname "readview"
web-server enable
end
"""
check("VOSS: una configuración segura pasa todas las reglas", failed_rules(VOSS, VOSS_SECURE) == [],
      failed_rules(VOSS, VOSS_SECURE))
bad = failed_rules(VOSS, VOSS_DEFAULT)
for rule in ("Sin Telnet", "SSH habilitado", "Sin servidor FTP", "Sin servidor TFTP", "Interfaz web solo por HTTPS",
             "Sin comunidades SNMP por defecto", "Cierre de sesión inactiva ≤ 15 min",
             "Autenticación centralizada (RADIUS o TACACS+)", "Envío de logs a syslog", "Sincronización horaria NTP",
             "Banner de uso autorizado"):
    check(f"VOSS de fábrica: falla «{rule}»", rule in bad, bad)
check("VOSS: sin interfaz web habilitada no se exige HTTPS",
      "Interfaz web solo por HTTPS" not in failed_rules(VOSS, "no web-server enable\n"))
check("VOSS: cli timeout de 900 s (15 min) pasa y de 901 falla",
      "Cierre de sesión inactiva ≤ 15 min" not in failed_rules(VOSS, "cli timeout 900\n")
      and "Cierre de sesión inactiva ≤ 15 min" in failed_rules(VOSS, "cli timeout 901\n"))

UBNT_SECURE = """!Current Configuration:
!
!System Description "EdgeSwitch 16-Port 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe, 1.0.0.4857129"
!System Software Version "1.9.3.5263085"
!
hostname "SW-Bodega"
network protocol none
network parms 192.0.2.30 255.255.255.0 192.0.2.1
username "admin-red" password 3c1f0e5a2b level 15 encrypted
ip ssh server enable
ip ssh protocol 2
no ip telnet server enable
ip http secure-server
snmp-server community "Red-Lectura-7x" ro
sntp client mode unicast
sntp server "192.0.2.1"
logging host 192.0.2.10 ipv4 514 info
"""
UBNT_DEFAULT = """!Current Configuration:
hostname "UBNT"
username "ubnt" password 8d7a1c level 15 encrypted
ip telnet server enable
ip http server
snmp-server community "public" ro
"""
check("Ubiquiti: una configuración segura pasa todas las reglas", failed_rules(UBNT, UBNT_SECURE) == [],
      failed_rules(UBNT, UBNT_SECURE))
bad = failed_rules(UBNT, UBNT_DEFAULT)
for rule in ("Sin la cuenta por defecto «ubnt»", "SSH habilitado", "Sin Telnet", "Sin HTTP sin cifrar",
             "Sin comunidades SNMP por defecto", "Envío de logs a syslog", "Sincronización horaria (SNTP)"):
    check(f"Ubiquiti de fábrica: falla «{rule}»", rule in bad, bad)
check("Ubiquiti: «ip http secure-server» no cuenta como HTTP sin cifrar",
      "Sin HTTP sin cifrar" not in failed_rules(UBNT, "ip http secure-server\n"))
check("Ubiquiti: un usuario llamado «ubnt-admin» no es la cuenta por defecto",
      "Sin la cuenta por defecto «ubnt»" not in failed_rules(UBNT, 'username "ubnt-admin" password x level 15\n'))
check("los filtros de equipo apuntan a los perfiles correctos",
      re.search(PACKS[VOSS][0], "extreme_vsp") and re.search(PACKS[UBNT][0], "ubiquiti_unifiswitch")
      and re.search(PACKS[UBNT][0], "ubiquiti_edgeswitch") and not re.search(PACKS[UBNT][0], "ubiquiti_edgerouter"))

# ---------------------------------------------------------------- MikroTik RouterOS («/export terse»)
MKT = "MikroTik RouterOS · Seguridad básica"
MKT_SECURE = """# 2026-10-21 10:00:00 by RouterOS 7.12.1
# software id = ABCD-1234
/interface list add name=MGMT
/ip firewall filter add action=accept chain=input connection-state=established,related comment="defconf"
/ip firewall filter add action=accept chain=input in-interface-list=MGMT
/ip firewall filter add action=drop chain=input comment="defconf: drop all not coming from LAN" in-interface-list=!LAN
/ip neighbor discovery-settings set discover-interface-list=MGMT
/ip service set telnet disabled=yes
/ip service set ftp disabled=yes
/ip service set www disabled=yes
/ip service set ssh port=2200
/ip service set api disabled=yes
/ip service set winbox address=192.0.2.0/24
/ip ssh set strong-crypto=yes
/snmp community set [ find default=yes ] name=Red-Lectura-7x
/system logging action set 3 remote=192.0.2.10
/system logging add action=remote topics=info
/system ntp client set enabled=yes servers=192.0.2.1
/tool bandwidth-server set enabled=no
/tool mac-server set allowed-interface-list=MGMT
/tool mac-server mac-winbox set allowed-interface-list=MGMT
"""
MKT_DEFAULT = """# 2026-10-21 10:00:00 by RouterOS 7.12.1
/ip address add address=192.168.88.1/24 interface=bridge network=192.168.88.0
/snmp community add name=public addresses=0.0.0.0/0
/system identity set name=MikroTik
"""
check("MikroTik: una configuración segura pasa todas las reglas", failed_rules(MKT, MKT_SECURE) == [],
      failed_rules(MKT, MKT_SECURE))
bad = failed_rules(MKT, MKT_DEFAULT)
check("MikroTik de fábrica: falla todas las reglas", len(bad) == len(PACKS[MKT][2]), sorted(set(r[0] for r in PACKS[MKT][2]) - set(bad)))
check("MikroTik: «/ip service set telnet address=…» sin desactivar sigue fallando",
      "Sin Telnet" in failed_rules(MKT, "/ip service set telnet address=10.0.0.0/8\n"))
check("MikroTik: «/ip service set www-ssl disabled=yes» no cuenta como www desactivado",
      "Sin web HTTP (www)" in failed_rules(MKT, "/ip service set www-ssl disabled=yes\n"))
check("MikroTik: «/ip service set api-ssl disabled=yes» no cuenta como API desactivada",
      "Sin API sin cifrar" in failed_rules(MKT, "/ip service set api-ssl disabled=yes\n"))
check("MikroTik RouterOS 6: «/tool mac-server set [ find default=yes ] disabled=yes» cuenta como limitado",
      "Acceso por MAC (MAC-Telnet/MAC-Winbox) limitado" not in
      failed_rules(MKT, "/tool mac-server set [ find default=yes ] disabled=yes\n"))

# ---------------------------------------------------------------- Juniper Junos (formato set)
JUN = "Juniper Junos · Seguridad básica"
JUN_SECURE = """set version 21.4R3-S5.4
set system host-name SRX-Borde
set system root-authentication encrypted-password "$6$abc$def"
set system login message "Acceso solo para personal autorizado"
set system login class operadores idle-timeout 10
set system login retry-options tries-before-disconnect 5
set system login retry-options lockout-period 30
set system tacplus-server 192.0.2.20 secret "$9$xyz"
set system services ssh root-login deny
set system services ssh protocol-version v2
set system services netconf ssh
set system services web-management https system-generated-certificate
set system syslog host 192.0.2.10 any notice
set system ntp server 192.0.2.1
set snmp community Red-Lectura-7x authorization read-only
"""
JUN_DEFAULT = """set version 21.4R3-S5.4
set system services telnet
set system services ftp
set system services web-management http interface fxp0.0
set snmp community public authorization read-only
"""
check("Junos: una configuración segura pasa todas las reglas", failed_rules(JUN, JUN_SECURE) == [],
      failed_rules(JUN, JUN_SECURE))
bad = failed_rules(JUN, JUN_DEFAULT)
check("Junos de fábrica: falla todas las reglas", len(bad) == len(PACKS[JUN][2]), sorted(set(r[0] for r in PACKS[JUN][2]) - set(bad)))
check("Junos: J-Web por HTTPS no cuenta como HTTP",
      "Sin J-Web por HTTP" not in failed_rules(JUN, "set system services web-management https system-generated-certificate\n"))

# ---------------------------------------------------------------- FortiGate («show»)
FGT = "FortiGate · Seguridad básica"
FGT_SECURE = """#config-version=FGT60F-7.2.5-FW-build1517-230606:opmode=0:vdom=0:user=red-admin
config system global
    set admintimeout 10
    set hostname "FGT-Central"
    set pre-login-banner enable
    set timezone 04
end
config system password-policy
    set status enable
    set minimum-length 12
end
config system interface
    edit "wan1"
        set ip 203.0.113.2 255.255.255.252
        set allowaccess ping https ssh
    next
    edit "internal"
        set allowaccess ping https ssh fgfm
    next
end
config system admin
    edit "red-admin"
        set accprofile "super_admin"
        set trusthost1 192.0.2.0 255.255.255.0
    next
end
config system snmp community
    edit 1
        set name "Red-Lectura-7x"
    next
end
config log syslogd setting
    set status enable
    set server "192.0.2.10"
end
"""
FGT_DEFAULT = """config system global
    set admintimeout 480
    set hostname "FortiGate-60F"
    set strong-crypto disable
end
config system ntp
    set ntpsync disable
end
config system interface
    edit "wan1"
        set allowaccess ping https ssh http telnet
    next
end
config system admin
    edit "admin"
        set accprofile "super_admin"
    next
end
config system snmp community
    edit 1
        set name "public"
    next
end
"""
check("FortiGate: una configuración segura pasa todas las reglas", failed_rules(FGT, FGT_SECURE) == [],
      failed_rules(FGT, FGT_SECURE))
bad = failed_rules(FGT, FGT_DEFAULT)
check("FortiGate de fábrica: falla todas las reglas", len(bad) == len(PACKS[FGT][2]), sorted(set(r[0] for r in PACKS[FGT][2]) - set(bad)))
check("FortiGate: «https» en allowaccess no cuenta como HTTP",
      "Sin HTTP en las interfaces" not in failed_rules(FGT, "        set allowaccess ping https ssh\n"))
check("FortiGate: un «public» fuera de SNMP no cuenta como comunidad",
      "Sin comunidades SNMP por defecto" not in failed_rules(FGT, 'config system snmp community\n    edit 1\n        set name "Red-7x"\n    next\nend\nconfig user local\n    edit "x"\n        set name "public"\n    next\nend\n'))
check("FortiGate: syslog configurado pero desactivado no cuenta",
      "Envío de logs a syslog o FortiAnalyzer" in failed_rules(FGT, 'config log syslogd setting\n    set server "192.0.2.10"\nend\n'))

# instalación que se actualiza: los paquetes nuevos llegan una vez; si se borran no vuelven
init_db()
with SessionLocal() as db:
    n = seed_new_packs(db)
    names = set(db.scalars(select(Policy.name)))
check("al actualizar se cargan los paquetes nuevos (solo esos)", n == len(ADDED_LATER) and names == set(ADDED_LATER),
      names)
with SessionLocal() as db:
    db.delete(db.scalars(select(Policy).where(Policy.name == VOSS)).first())
    db.commit()
    n = seed_new_packs(db)
    names = set(db.scalars(select(Policy.name)))
check("un paquete borrado no vuelve a aparecer al reiniciar", n == 0 and VOSS not in names, names)
with SessionLocal() as db:
    seed_packs(db)
    total = len(set(db.scalars(select(Policy.name))))
check("«opennccm seed» carga todos los paquetes", total == len(PACKS), total)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
