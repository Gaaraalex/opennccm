"""Respaldo real de servidores Ubuntu/Debian (los prepara tests/linux-real-test.sh).
Uso: linux_real.py <imagen> <puerto> [<imagen> <puerto> …]"""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402
from app.compliance_packs import PACKS  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()
args = sys.argv[1:]
WANT = {"ubuntu:26.04": "26.04", "ubuntu:24.04": "24.04", "ubuntu:22.04": "22.04", "debian:13": "13"}
with SessionLocal() as db:
    con = Credential(name="ncm", username="ncm", password_enc=encrypt("Clave-Linux-2026"))
    lec = Credential(name="lector", username="lector", password_enc=encrypt("Clave-Lector-2026"))
    db.add_all([con, lec])
    db.commit()
    cred_ids = (con.id, lec.id)


def backup(name, port, cred):
    with SessionLocal() as db:
        d = Device(name=name, host="127.0.0.1", port=int(port), device_type="ubuntu_server", credential_id=cred)
        db.add(d)
        db.commit()
        dev_id = d.id
    return dev_id, services.backup_device(dev_id, "manual", "ci")


def latest(dev_id):
    with SessionLocal() as db:
        v = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id).order_by(ConfigVersion.id.desc())).first()
        dev = db.get(Device, dev_id)
        return (decrypt(v.content_enc) if v else ""), dev


for img, port in zip(args[::2], args[1::2]):
    print(f"\n=== {img}")
    dev_id, res = backup(f"srv-{img.replace(':', '-')}", port, cred_ids[0])
    text, dev = latest(dev_id)
    check(f"{img}: primer respaldo con sudo", res == "first", (res, dev.last_error))
    check(f"{img}: versión leída de /etc/os-release", (dev.os_version or "").startswith(WANT.get(img, "?")), dev.os_version)
    for sec in ("### /etc/hostname", "### /etc/ssh/sshd_config", "### /etc/passwd", "### /etc/sudoers",
                "### /etc/netplan/50-ci.yaml", "### /etc/apt/apt.conf.d/20auto-upgrades", "### /etc/cron.d/respaldo",
                "### paquetes instalados a mano", "### iptables", "### nftables"):
        check(f"{img}: incluye {sec}", sec in text)
    check(f"{img}: la red de netplan (solo legible con sudo) se respalda", "dhcp4: true" in text)
    check(f"{img}: la contraseña de wifi de netplan no se copia", "secreto-wifi-123" not in text and "password: ***" in text)
    check(f"{img}: ni contraseñas cifradas ni claves privadas",
          not re.search(r"\$[0-9a-z]+\$[^:\s]{8,}", text) and "PRIVATE KEY" not in text and "### /etc/shadow" not in text)
    check(f"{img}: sin contadores de paquetes de iptables", not re.search(r"\[[1-9]\d*:\d+\]", text))
    check(f"{img}: la tarea de cron y los paquetes se ven", "/usr/local/bin/respaldo.sh" in text and "openssh-server" in text)
    size = len(text.encode())
    print(f"     tamaño del respaldo: {size / 1024:.1f} KB, {len(text.splitlines())} líneas")
    check(f"{img}: tamaño razonable (menos de 200 KB)", size < 200 * 1024, size)
    res2 = services.backup_device(dev_id, "manual", "ci")
    check(f"{img}: un segundo respaldo sin cambios no crea versión nueva (nada volátil)", res2 == "unchanged", res2)

    # usuario sin sudo: se respalda lo que se puede leer, sin fallar
    dev2, res3 = backup(f"srv-{img.replace(':', '-')}-lector", port, cred_ids[1])
    text2, d2 = latest(dev2)
    check(f"{img}: un usuario sin sudo también respalda (sin fallar)", res3 == "first", (res3, d2.last_error))
    check(f"{img}: sin sudo no ve el netplan protegido, pero sí lo demás",
          "dhcp4: true" not in text2 and "### /etc/hostname" in text2 and "### /etc/passwd" in text2)

    pack = PACKS.get("Ubuntu / Debian · Seguridad básica")
    if pack:
        fails_rules = [r[0] for r in pack[2] if (re.search(r[2], text, re.M) is not None) != (r[1] == "must_contain")]
        print(f"     cumplimiento sobre el respaldo real: incumple {fails_rules}")
        check(f"{img}: las reglas reconocen el respaldo real (actualizaciones automáticas activadas)",
              "Actualizaciones automáticas de seguridad" not in fails_rules, fails_rules)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
