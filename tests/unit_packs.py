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
