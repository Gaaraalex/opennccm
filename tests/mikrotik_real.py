"""Reversión en un MikroTik real (lo arranca tests/mikrotik-real-test.sh): respaldo, cambio no autorizado hecho a mano,
reversión generada por OpenNCCM aplicada en modo seguro y comprobación de que el equipo queda como antes. También que
un fallo a mitad deshace lo aplicado (modo seguro) y que lo que no se puede revertir se avisa."""
import sys
import time

import paramiko

sys.path.insert(0, ".")
from app import services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal, init_db, select  # noqa: E402
from app.drivers import clean_config, get_profile  # noqa: E402

PORT, PASSWORD, VER = int(sys.argv[1]), sys.argv[2], sys.argv[3]
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def ros(*cmds):
    """Ejecuta órdenes en el router «a mano» (fuera de OpenNCCM), como haría alguien por SSH."""
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("127.0.0.1", PORT, "admin", PASSWORD, look_for_keys=False, allow_agent=False, timeout=20)
    out = []
    for cmd in cmds:
        _, so, se = c.exec_command(cmd, timeout=30)
        out.append(so.read().decode(errors="replace") + se.read().decode(errors="replace"))
    c.close()
    return "\n".join(out)


def latest(dev_id):
    with SessionLocal() as db:
        v = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id)
                       .order_by(ConfigVersion.id.desc())).first()
        return v.id, decrypt(v.content_enc)


def body(text):
    """Configuración sin cabecera ni comentarios, para comparar."""
    return [l for l in clean_config(text, prof).splitlines() if l.startswith("/")]


prof = get_profile("mikrotik_routeros")
init_db()
with SessionLocal() as db:
    cred = Credential(name="chr", username="admin", password_enc=encrypt(PASSWORD))
    db.add(cred)
    db.flush()
    dev = Device(name=f"CHR-{VER}", host="127.0.0.1", port=PORT, device_type="mikrotik_routeros", credential_id=cred.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id

# configuración de partida
print(ros("/system identity set name=CHR-Base",
          "/interface bridge add name=br-base comment=base",
          '/ip firewall filter add chain=input action=accept connection-state=established,related comment="ya establecidas"',
          "/ip firewall filter add chain=input action=drop protocol=tcp dst-port=23 comment=telnet",
          "/ip firewall filter add chain=input action=drop protocol=tcp dst-port=21 comment=ftp",
          "/ip address add address=172.16.9.1/24 interface=br-base"))
r = services.backup_device(dev_id, "manual", "ci")
with SessionLocal() as db:
    err = db.get(Device, dev_id).last_error
check(f"RouterOS {VER}: primer respaldo", r == "first", f"{r}: {err}")
if r != "first":
    sys.exit(1)
v1, base = latest(dev_id)

# cambio no autorizado, hecho a mano
print(ros("/system identity set name=CAMBIADO",
          "/interface bridge set [ find name=br-base ] comment=tocado",
          "/ip firewall filter remove [ find comment=telnet ]",
          "/ip firewall filter add chain=input action=accept protocol=icmp comment=ping",
          "/interface bridge add name=br-nuevo",
          "/interface vlan add interface=br-nuevo name=vlan20 vlan-id=20",
          "/ip address add address=192.168.20.1/24 interface=vlan20",
          "/ip firewall nat add chain=srcnat action=masquerade out-interface=vlan20"))
r = services.backup_device(dev_id, "manual", "ci")
check("el cambio se detecta", r == "changed", r)
_, changed = latest(dev_id)

cmds, notes = services.rollback_commands(dev_id, v1)
print("--- comandos de reversión\n" + "\n".join(cmds or []) + "\n--- avisos\n" + "\n".join(notes))
check("genera la reversión (no re-aplica la configuración entera)", cmds and len(cmds) < len(body(base)) + 5, cmds)
check("borra lo añadido en orden inverso (la IP antes que la VLAN, la VLAN antes que el bridge)",
      cmds and [i for i, c in enumerate(cmds) if "/ip address remove" in c] < [i for i, c in enumerate(cmds) if "/interface vlan remove" in c]
      < [i for i, c in enumerate(cmds) if "/interface bridge remove" in c])
try:
    out = services.push_commands(dev_id, cmds)
    applied = True
except Exception as e:  # noqa: BLE001
    applied, out = False, str(e)
print(out[-1500:])
check("la reversión se aplica sin errores (en modo seguro)", applied and "modo seguro liberado" in out, out[-400:])
r = services.backup_device(dev_id, "manual", "ci")
_, after = latest(dev_id)
check("el equipo queda exactamente como en la versión de partida (reglas en el mismo orden)",
      body(after) == body(base), "\n".join(sorted(set(body(after)) ^ set(body(base)))))
check("la regla de telnet vuelve a su sitio (entre «ya establecidas» y «ftp»)",
      [l for l in body(after) if "/ip firewall filter" in l] == [l for l in body(base) if "/ip firewall filter" in l])

# un fallo a mitad: el modo seguro deshace lo que ya se aplicó
before = ros("/system identity print")
try:
    services.push_commands(dev_id, ["/system identity set name=A-MEDIAS", "/ip address add address=esto-no-es-una-ip interface=br-base"])
    failed = False
except Exception as e:  # noqa: BLE001
    failed, msg = True, str(e)
check("un comando rechazado detiene el cambio", failed)
time.sleep(3)
ident = ros("/system identity print")
check("y el modo seguro deshace lo que ya se había aplicado (la identidad no cambió)", "A-MEDIAS" not in ident and ident.strip() == before.strip(), ident)
# tras un cambio correcto el modo seguro queda liberado y el cambio se conserva
services.push_commands(dev_id, ["/system identity set name=CHR-Final"])
time.sleep(2)
check("un cambio correcto se conserva al cerrar la sesión", "CHR-Final" in ros("/system identity print"))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
