"""Base grande para medir el rendimiento (capítulo 9 de la guía del desarrollador): 2000 equipos en 20 grupos, 10
copias cada uno (20 000), 60 000 eventos, cumplimiento (5 reglas por equipo), 3000 vulnerabilidades y 20 000 líneas de
auditoría. Las direcciones son de la red de pruebas 198.18.0.0/15: OpenNCCM nunca llega a un equipo real.
Uso (con un directorio de datos VACÍO):  NCM_DATA_DIR=/tmp/perf NCM_KEY_FILE=/tmp/perf/secret.key python scripts/perf_seed.py
Admin: admin / Admin12345."""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.cli import main as cli  # noqa: E402

sys.argv = ["x", "create-admin", "admin", "Admin12345"]
cli()
sys.argv = ["x", "seed"]
cli()

from app.crypto import encrypt  # noqa: E402
from app.db import (AuditLog, ComplianceResult, ConfigVersion, Credential, Device, DeviceGroup, Event, Policy,  # noqa: E402
                    Rule, SessionLocal, Vulnerability, engine)

random.seed(7)
TYPES = ["cisco_ios", "juniper_junos", "mikrotik_routeros", "fortinet", "extreme_exos", "aruba_os", "hp_procurve", "cisco_nxos"]
VER = {"cisco_ios": "15.2(7)E", "juniper_junos": "21.4R3", "mikrotik_routeros": "7.16.2", "fortinet": "7.4.2",
       "extreme_exos": "33.6.1.14", "aruba_os": "16.11", "hp_procurve": "16.10", "cisco_nxos": "9.3(10)"}
now = datetime.now().replace(microsecond=0)
base_cfg = "\n".join(f"interface Gi1/0/{i}\n description Puesto-{i}\n switchport access vlan {10 + i % 5}\n!" for i in range(1, 49))
with SessionLocal() as db:
    cred = Credential(name="perf", username="admin", password_enc=encrypt("x"))
    db.add(cred)
    groups = [DeviceGroup(name=f"Sitio {g:02d}") for g in range(20)]
    db.add_all(groups)
    db.flush()
    devs = []
    for i in range(2000):
        t = TYPES[i % len(TYPES)]
        st = random.choices(["ok", "error", "pending"], [90, 7, 3])[0]
        devs.append(Device(name=f"SW-{i:04d}", host=f"198.18.{i // 250}.{i % 250}", device_type=t, credential_id=cred.id,
                           group_id=groups[i % 20].id, model="Modelo-X", serial=f"SN{i:06d}", os_version=VER[t],
                           last_status=st, last_backup_at=now - timedelta(hours=random.randint(1, 48)),
                           last_error="timeout" if st == "error" else None, location=f"Edificio {i % 7}"))
    db.add_all(devs)
    db.commit()
    ids = [d.id for d in devs]
    print("equipos", len(ids))
    enc = encrypt(base_cfg)
    rows = []
    for d in ids:
        for k in range(10):
            rows.append(dict(device_id=d, kind="running", content_enc=enc, sha256=f"{d:032d}{k:032d}", size=len(base_cfg),
                             added=random.randint(0, 9), removed=random.randint(0, 9), trigger="scheduled",
                             fw_version="15.2", created_at=now - timedelta(days=10 - k)))
    db.execute(ConfigVersion.__table__.insert(), rows)
    db.commit()
    print("versiones", len(rows))
    cats = ["backup", "syslog", "compliance", "neighbor", "cve", "change"]
    sev = ["info", "warning", "major", "critical"]
    db.execute(Event.__table__.insert(), [dict(created_at=now - timedelta(minutes=random.randint(0, 60 * 24 * 60)),
                                               severity=random.choice(sev), category=random.choice(cats),
                                               device_id=random.choice(ids), message="Evento de prueba " + str(n),
                                               acknowledged=random.random() < .7) for n in range(60000)])
    db.commit()
    pol = db.query(Policy).first()
    rules = db.query(Rule).filter(Rule.policy_id == pol.id).limit(5).all() if pol else []
    if rules:
        db.execute(ComplianceResult.__table__.insert(), [dict(device_id=d, policy_id=pol.id, rule_id=r.id, passed=random.random() < .8,
                                                              checked_at=now) for d in ids for r in rules])
    db.execute(Vulnerability.__table__.insert(), [dict(device_id=random.choice(ids), cve_id=f"CVE-2025-{n:05d}", score=random.uniform(3, 10),
                                                       severity=random.choice(["LOW", "MEDIUM", "HIGH", "CRITICAL"]),
                                                       description="Prueba", found_at=now) for n in range(3000)])
    db.execute(AuditLog.__table__.insert(), [dict(created_at=now - timedelta(minutes=n), username="admin", action="backup",
                                                  detail="x") for n in range(20000)])
    db.commit()
    print("listo", engine.url)
