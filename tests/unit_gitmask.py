"""Copia en Git: las contraseñas, claves y comunidades se ocultan en las configuraciones de las marcas más comunes, y la
configuración normal queda intacta."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.gitexport import mask  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CASES = [
    ("Cisco enable secret", "enable secret 9 $9$abcdefgh$XYZ", "enable secret 9 <oculto>"),
    ("Cisco usuario", "username admin privilege 15 secret 5 $1$abc$xyz", "username admin privilege 15 secret 5 <oculto>"),
    ("Cisco SNMP", "snmp-server community Publica123 RO", "snmp-server community <oculto> RO"),
    ("Cisco TACACS", " key 7 0822455D0A16", " key 7 <oculto>"),
    ("Junos", 'set system root-authentication encrypted-password "$6$abc$def"',
     'set system root-authentication encrypted-password "<oculto>"'),
    ("MikroTik wifi", "/interface wireless security-profiles set [ find default=yes ] wpa2-pre-shared-key=ClaveWifi1",
     "/interface wireless security-profiles set [ find default=yes ] wpa2-pre-shared-key=<oculto>"),
    ("MikroTik PPP", '/ppp secret add name=cliente password="mi clave" service=pppoe',
     "/ppp secret add name=cliente password=<oculto> service=pppoe"),
    ("FortiGate", "        set password ENC SH2abcdef==", "        set password ENC <oculto>"),
    ("Huawei", " local-user admin password irreversible-cipher $1a$abc$", " local-user admin password irreversible-cipher <oculto>"),
    ("airOS", "users.1.password=$1$Mn4yIetm$Pp9q", "users.1.password=<oculto>"),
]
for name, src, want in CASES:
    got = mask(src)
    check(f"{name}: oculto", got == want, got)
KEEP = ["hostname R1", "interface GigabitEthernet0/1", " description enlace al core", "ip route 0.0.0.0 0.0.0.0 10.0.0.1",
        "/ip address add address=10.0.0.1/24 interface=ether1", "set system host-name SRX", "vlan 20"]
for line in KEEP:
    check(f"se conserva: «{line.strip()}»", mask(line) == line, mask(line))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
