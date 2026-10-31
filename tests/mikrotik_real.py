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
    # sin los elementos que RouterOS crea por sí mismo (la reversión no los borra: p. ej. keymat-provider en 7.25)
    return [l for l in clean_config(text, prof).splitlines() if l.startswith("/") and "name=default" not in l]


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
    # qué muestra RouterOS al entrar como lo hace Netmiko (usuario «admin+ct511w4098h»)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("127.0.0.1", PORT, "admin+ct511w4098h", PASSWORD, look_for_keys=False, allow_agent=False, timeout=20)
    ch = c.invoke_shell(width=511, height=4098)
    time.sleep(8)
    raw = b""
    while ch.recv_ready():
        raw += ch.recv(65535)
    print("PANTALLA AL ENTRAR:", repr(raw.decode(errors="replace")[-1500:]))
    ch.send("\r\n")
    time.sleep(3)
    raw = b""
    while ch.recv_ready():
        raw += ch.recv(65535)
    print("TRAS ENTER:", repr(raw.decode(errors="replace")[-800:]))
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

# sonda: qué selectores encuentra RouterOS (se imprime para revisar la sintaxis en cada versión)
PROBE = [
    ("find k=v (IP)", ":put [:len [/ip address find address=192.168.20.1/24]]"),
    ("find where k=v (IP)", ":put [:len [/ip address find where address=192.168.20.1/24]]"),
    ('find where k="v" (IP)', ':put [:len [/ip address find where address="192.168.20.1/24"]]'),
    ("find k=v k=v (regla)", ":put [:len [/ip firewall filter find action=accept chain=input comment=ping protocol=icmp]]"),
    ("find where … and … (regla)",
     ":put [:len [/ip firewall filter find where action=accept and chain=input and comment=ping and protocol=icmp]]"),
    ('find where … and … con comillas (regla)',
     ':put [:len [/ip firewall filter find where action="accept" and chain="input" and comment="ping" and protocol="icmp"]]'),
    ("find where lista sin comillas",
     ":put [:len [/ip firewall filter find where connection-state=established,related]]"),
    ('find where lista con comillas',
     ':put [:len [/ip firewall filter find where connection-state="established,related"]]'),
    ("find where puerto",
     ":put [:len [/ip firewall filter find where dst-port=21 and protocol=tcp]]"),
    ('find where puerto con comillas',
     ':put [:len [/ip firewall filter find where dst-port="21" and protocol="tcp"]]'),
]
for label, cmd in PROBE:
    print(f"SONDA {label}: {ros(cmd).strip()!r}")

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
t0, ident = time.time(), ""
for _ in range(20):  # RouterOS puede tardar en deshacer el modo seguro tras cortar la sesión
    try:
        ident = ros("/system identity print")
        break
    except Exception as e:  # noqa: BLE001
        print("esperando a RouterOS:", type(e).__name__)
        time.sleep(10)
print(f"RouterOS respondió {time.time() - t0:.0f} s después de cortar la sesión en modo seguro")
check("y el modo seguro deshace lo que ya se había aplicado (la identidad no cambió)", "A-MEDIAS" not in ident and ident.strip() == before.strip(), ident)
# tras un cambio correcto el modo seguro queda liberado y el cambio se conserva
services.push_commands(dev_id, ["/system identity set name=CHR-Final"])
time.sleep(2)
check("un cambio correcto se conserva al cerrar la sesión", "CHR-Final" in ros("/system identity print"))

# .backup del equipo: se crea, se trae por SFTP y se borra del equipo; con contraseña, cifrado
for pw in ("", "Clave.2026"):
    try:
        fname, data = services.mikrotik_backup_file(dev_id, pw)
    except Exception as e:  # noqa: BLE001
        fname, data = str(e), b""
    files = ros("/file print terse")
    check(f".backup {'cifrado' if pw else 'sin cifrar'}: llega entero y no queda en el equipo",
          fname.endswith(".backup") and len(data) > 1000 and "opennccm-" not in files, f"{fname} {len(data)} bytes; {files[-300:]}")
try:
    services.mikrotik_backup_file(dev_id, 'x" ; /system reboot')
    rejected = False
except ValueError:
    rejected = True
check("una contraseña con comillas o «;» se rechaza (no se inyectan órdenes)", rejected)

# puertos de la ficha: el respaldo lee /interface ethernet y su estado
import json  # noqa: E402

from app import restore  # noqa: E402

services.backup_device(dev_id, "manual", "ci")
with SessionLocal() as db:
    d = db.get(Device, dev_id)
    pj = json.loads(d.ports or "{}")
    model = d.model
print("PUERTOS:", pj)
by = {p["n"]: p for p in pj.get("ports", [])}
check("puertos: el respaldo lee los ethernet del equipo con su estado", pj.get("status") is True and "ether1" in by
      and by["ether1"]["s"] == "up", pj)

# restaurar en «otro equipo» (el mismo CHR por SSH, como si fuera uno nuevo): mismo modelo, vuelve a la copia
with SessionLocal() as db:
    ver = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id).order_by(ConfigVersion.id.desc())).first()
    ver_id = ver.id
ros("/system identity set name=CHR-Restaurar", "/ip address add address=192.0.2.99/32 interface=lo comment=para-quitar")
with SessionLocal() as db:
    src = db.get(Device, dev_id)
    target = restore.transient(src, "127.0.0.1", PORT, "admin", PASSWORD)
res = restore.check_ssh(ver_id, target, "ci")
check("restaurar en otro equipo: mismo modelo y comandos calculados", res["ok"] and res["model"] == model, res)
if res.get("token"):
    restore.start(res["token"], "ci")
    for _ in range(120):
        time.sleep(0.5)
        if restore.get(res["token"], "ci")["state"] in ("done", "error"):
            break
    job = restore.get(res["token"], "ci")
    check("restaurar en otro equipo: aplicado en modo seguro", job["state"] == "done", job.get("error"))
    check("el equipo vuelve a la copia (identidad y sin la IP añadida)", "CHR-Final" in ros("/system identity print")
          and "192.0.2.99" not in ros("/ip address print terse"), ros("/system identity print"))
with SessionLocal() as db:
    db.get(Device, dev_id).model = "RB760iGS"
    db.commit()
res = restore.check_ssh(ver_id, target, "ci")
check("restaurar en otro equipo: otro modelo se rechaza", not res["ok"] and "mismo modelo" in res["error"], res)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
