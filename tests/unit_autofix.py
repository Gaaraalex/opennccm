"""Corrección automática de cumplimiento (app/autofix.py), sin red: qué reglas entran, la ventana, la espera de 24 h,
y qué hace en cada caso (aplicada, revertida, bloqueada, fallida) con el respaldo, el envío y la reversión simulados."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import autofix, perms, services, verified  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import (AutoFixRun, ComplianceResult, ConfigVersion, Device, Policy, Rule, SessionLocal,  # noqa: E402
                    init_db, now)

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class P:  # política mínima para la ventana
    def __init__(self, days="0123456", a=None, b=None):
        self.autofix_days, self.autofix_from, self.autofix_to = days, a, b


# --- ventana (13-11-2026 es viernes = 4)
fri = datetime(2026, 11, 13, 10, 0)
check("sin horas: cualquier hora de los días marcados", autofix.in_window(P(), fri) and not autofix.in_window(P("0123"), fri))
check("dentro y fuera de 08:00–18:00", autofix.in_window(P(a="08:00", b="18:00"), fri)
      and not autofix.in_window(P(a="08:00", b="09:59"), fri))
night = P("4", "22:00", "06:00")  # solo la del viernes
check("ventana que cruza la medianoche: viernes 23:00 y sábado 02:00 son del viernes",
      autofix.in_window(night, datetime(2026, 11, 13, 23, 0)) and autofix.in_window(night, datetime(2026, 11, 14, 2, 0)))
check("…pero el viernes a las 02:00 es la del jueves (no marcado)", not autofix.in_window(night, datetime(2026, 11, 13, 2, 0)))
check("ningún día: nunca", not autofix.in_window(P(""), fri))
check("horas válidas", autofix.parse_hm("7:05") == "07:05" and autofix.parse_hm("24:00") is None and autofix.parse_hm("") is None)
check("días limpios y en orden", autofix.clean_days(["6", "0", "x", "9", "0"]) == "06")

# --- datos: un equipo, una política con 4 reglas
with SessionLocal() as db:
    d = Device(name="R1", host="10.0.0.1", device_type="cisco_ios")
    d2 = Device(name="R2", host="10.0.0.2", device_type="cisco_ios")
    db.add_all([d, d2])
    db.flush()
    p = Policy(name="Prueba", autofix=True)
    db.add(p)
    db.flush()
    rules = [Rule(policy_id=p.id, name="SSH v2", kind="must_contain", pattern=r"(?m)^ip ssh version 2$",
                  remediation="ip ssh version 2"),
             Rule(policy_id=p.id, name="Sin sello", kind="must_contain", pattern=r"(?m)^service password-encryption$",
                  remediation="service password-encryption"),
             Rule(policy_id=p.id, name="Sin corrección", kind="must_contain", pattern=r"(?m)^x$"),
             Rule(policy_id=p.id, name="Con ejemplo", kind="must_contain", pattern=r"(?m)^ntp server 192\.0\.2\.10$",
                  remediation="ntp server 192.0.2.10")]
    db.add_all(rules)
    db.flush()
    r_ssh, r_noseal, r_nofix, r_ex = (r.id for r in rules)
    dev_id, dev2_id, pid = d.id, d2.id, p.id
    for did in (dev_id, dev2_id):
        for r in rules:
            db.add(ComplianceResult(device_id=did, policy_id=pid, rule_id=r.id, passed=False))
    for r in rules:
        r.autofix_sig = autofix.sig(r) if r.remediation else None
    db.commit()
for rid in (r_ssh, r_ex):
    verified.put("rule", rid, "IOS 15.2 (prueba)")
vmap = verified.all_of("rule")
with SessionLocal() as db:
    st = {r.name: autofix.rule_state(r, vmap) for r in db.get(Policy, pid).rules}
check("estado de cada regla", st == {"SSH v2": "on", "Sin sello": "unverified", "Sin corrección": "nofix", "Con ejemplo": "on"}, st)

with SessionLocal() as db:
    r = db.get(Rule, r_ex)
    r.remediation = "ntp server 192.0.2.11"
    db.commit()
    check("si cambian los comandos de una regla elegida, sale de la corrección automática",
          autofix.rule_state(db.get(Rule, r_ex), vmap) == "changed")
    r.remediation = "ntp server 192.0.2.10"
    db.commit()
    check("…y vuelve si se deja como estaba", autofix.rule_state(db.get(Rule, r_ex), vmap) == "on")

pend = autofix.pending()
check("pendiente: solo las reglas verificadas y elegidas, por equipo", pend == [(dev_id, pid, sorted([r_ssh, r_ex])),
                                                                          (dev2_id, pid, sorted([r_ssh, r_ex]))], pend)
with SessionLocal() as db:
    db.get(Device, dev2_id).maintenance_until = now() + timedelta(minutes=5)
    db.commit()
check("un equipo al que se le está aplicando otro cambio espera", all(x[0] != dev2_id for x in autofix.pending()))
with SessionLocal() as db:
    db.get(Device, dev2_id).enabled = False
    db.get(Device, dev2_id).maintenance_until = None
    db.commit()
check("un equipo desactivado no se toca", all(x[0] != dev2_id for x in autofix.pending()))
with SessionLocal() as db:
    db.get(Policy, pid).autofix_days = ""
    db.commit()
check("fuera de la ventana: nada", autofix.pending() == [])
with SessionLocal() as db:
    db.get(Policy, pid).autofix_days = "0123456"
    db.commit()


# --- ejecución con el equipo simulado
CFG = {"text": "hostname R1\nip http server\n"}
calls = []


def fake_backup(did, trigger="manual", username=None):
    calls.append(("backup", trigger, username))
    with SessionLocal() as db:
        last = services.latest_version(db, did)
        import hashlib
        sha = hashlib.sha256(CFG["text"].encode()).hexdigest()
        if last is None or last.sha256 != sha:
            db.add(ConfigVersion(device_id=did, kind="running", content_enc=encrypt(CFG["text"]), sha256=sha,
                                 size=len(CFG["text"]), trigger=trigger, username=username))
        for r in db.query(ComplianceResult).filter(ComplianceResult.device_id == did):
            rule = db.get(Rule, r.rule_id)
            import re
            r.passed = re.search(rule.pattern, CFG["text"], re.M) is not None
        db.commit()
    return "changed"


def fake_push(did, cmds, exec_mode=False):
    calls.append(("push", list(cmds)))
    for c in cmds:
        if c == "ip ssh version 2" and MODE["reject"]:
            raise RuntimeError("% Invalid input detected at '^' marker.")
        if c.startswith("no "):
            CFG["text"] = "".join(l + "\n" for l in CFG["text"].splitlines() if l != c[3:])
        elif MODE["apply_wrong"] and c == "ip ssh version 2":
            CFG["text"] += "ip ssh version 1\n"
        else:
            CFG["text"] += c + "\n"
    return "R1(config)#" + "\nR1(config)#".join(cmds)


def fake_rollback(did, ver_id):
    calls.append(("rollback_plan", ver_id))
    with SessionLocal() as db:
        from app.crypto import decrypt
        old = decrypt(db.get(ConfigVersion, ver_id).content_enc).splitlines()
    return ["no " + l for l in CFG["text"].splitlines() if l not in old], []


MODE = {"reject": False, "apply_wrong": False}
services.backup_device, services.push_commands, services.rollback_commands = fake_backup, fake_push, fake_rollback
services.add_event = lambda *a, **k: calls.append(("event", a[1]))


def last_run():
    with SessionLocal() as db:
        return db.query(AutoFixRun).order_by(AutoFixRun.id.desc()).first()


res = autofix.run_one(dev_id, pid, [r_ssh])
run = last_run()
check("aplicada: respalda antes, envía, respalda después y la regla ya se cumple",
      res == "applied" and run.status == "applied" and "ip ssh version 2" in CFG["text"]
      and [c[0] for c in calls if c[0] in ("backup", "push")] == ["backup", "push", "backup"], (res, calls))
check("con las versiones de antes y después", run.version_before_id and run.version_after_id
      and run.version_before_id != run.version_after_id)
check("aviso en Eventos", ("event", "Corrección automática aplicada") in calls)
check("respalda como «corrección automática»", ("backup", "manual", autofix.WHO) in calls)
check("la regla aplicada no se repite en 24 h", all(r_ssh not in x[2] for x in autofix.pending() if x[0] == dev_id))

calls.clear()
res = autofix.run_one(dev_id, pid, [r_ex])
check("bloqueada: comandos con direcciones de ejemplo; no envía nada",
      res == "blocked" and not any(c[0] == "push" for c in calls) and "192.0.2.10" in last_run().message, (res, calls))

# revertida: el equipo acepta el comando pero la regla sigue sin cumplirse
CFG["text"] = "hostname R1\nip http server\n"
MODE["apply_wrong"] = True
calls.clear()
with SessionLocal() as db:
    db.query(AutoFixRun).delete()
    db.commit()
res = autofix.run_one(dev_id, pid, [r_ssh])
run = last_run()
check("revertida: la regla siguió sin cumplirse y volvió a la configuración de antes",
      res == "rolled_back" and run.status == "rolled_back" and CFG["text"] == "hostname R1\nip http server\n"
      and any(c[0] == "rollback_plan" for c in calls) and "antes" in run.message, (res, CFG, run.message))
MODE["apply_wrong"] = False

# el equipo rechaza el comando y no cambió nada: fallida, sin reversión
MODE["reject"] = True
calls.clear()
with SessionLocal() as db:
    db.query(AutoFixRun).delete()
    db.commit()
res = autofix.run_one(dev_id, pid, [r_ssh])
check("rechazada sin cambios: fallida, no hace falta revertir", res == "failed" and not any(c[0] == "rollback_plan" for c in calls)
      and "rechazó" in last_run().message, (res, last_run().message))
MODE["reject"] = False

check("tras un intento fallido, esa regla espera 24 h", all(r_ssh not in x[2] for x in autofix.pending() if x[0] == dev_id))
check("«Reintentar» la libera", autofix.retry(last_run().id) and any(r_ssh in x[2] for x in autofix.pending() if x[0] == dev_id))

# ya cumple al respaldar: no hace nada ni deja historial
CFG["text"] = "hostname R1\nip ssh version 2\n"
with SessionLocal() as db:
    db.query(AutoFixRun).delete()
    db.commit()
check("si ya cumple al respaldar, no hace nada", autofix.run_one(dev_id, pid, [r_ssh]) is None and last_run() is None)

# pausa
autofix.set_paused(True)
check("en pausa: la revisión no hace nada", autofix.sweep() == 0)
autofix.set_paused(False)

# reinicio a mitad: lo que quedó «en curso» se cierra
with SessionLocal() as db:
    db.add(AutoFixRun(device_id=dev_id, device_name="R1", policy_id=pid, policy_name="Prueba", rule_ids=json.dumps([r_ssh]),
                      rule_names="[]", status="running", started_at=now() - timedelta(hours=2)))
    db.commit()
CFG["text"] = "hostname R1\n"
autofix.sweep()
with SessionLocal() as db:
    left = db.query(AutoFixRun).filter(AutoFixRun.status == "running").count()
check("lo que quedó en curso tras un reinicio pasa a «Fallida»", left == 0)

# permisos
check("«Corrección automática» es una función crítica de administradores", "autofix" in perms.CRITICAL
      and perms.ROLE_MIN["autofix"] == "admin" and perms.for_path("/compliance/autofix/policy/1") == "autofix"
      and perms.for_path("/compliance/autofix/pause") == "autofix" and perms.for_path("/compliance/autofix") is None)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
