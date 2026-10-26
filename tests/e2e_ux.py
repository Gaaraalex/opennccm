"""Hallazgos de diseño del curso en vídeo: nombre propuesto en «Nueva tarea», «Corregir en N equipos…» en Cumplimiento y
aviso en la consola cuando el equipo tiene credencial pero no hay inicio automático."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db import (ChangeRequest, ComplianceResult, Credential, Device, Policy, Rule,  # noqa: E402
                    SessionLocal, set_setting)

B = "http://127.0.0.1:8000"
FAILS = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})

# 1. «Nueva tarea»: el nombre propuesto corresponde a la acción y cambia con ella mientras no se haya escrito
page = s.get(B + "/schedules/new").text
check("Nueva tarea: respaldo con «Respaldo nocturno»", 'id="t-name" name="name" required value="Respaldo nocturno"' in page)
m = re.search(r"const NAMES = (\{.*?\}), name", page)
names = json.loads(m.group(1)) if m else {}
check("el formulario conoce un nombre para cada acción",
      names.get("compliance") == "Auditoría semanal" and names.get("system_backup") == "Copia de recuperación semanal", names)
page = s.get(B + "/schedules/new?action=cve").text
check("Nueva tarea desde «Buscar CVE»: nombre propio, no «Respaldo nocturno»",
      'value="Búsqueda semanal de CVE"' in page and 'value="Respaldo nocturno"' not in page)

# 2. Cumplimiento: una regla que incumplen varios equipos tiene un solo botón con la lista de equipos
with SessionLocal() as db:
    cred = Credential(name="ux-cred", username="u")
    db.add(cred)
    db.flush()
    devs = [Device(name=f"ux-sw{i}", host=f"192.0.2.{200 + i}", device_type="cisco_ios", credential_id=cred.id)
            for i in range(7)]
    pol = Policy(name="UX · varias", device_type_filter="cisco_ios")
    db.add_all(devs + [pol])
    db.flush()
    rule = Rule(policy_id=pol.id, name="UX sin telnet", kind="must_not_contain", pattern="telnet",
                remediation="line vty 0 4\n transport input ssh")
    db.add(rule)
    db.flush()
    res = [ComplianceResult(device_id=d.id, policy_id=pol.id, rule_id=rule.id, passed=False) for d in devs]
    db.add_all(res)
    db.commit()
    dev_ids, res_ids, pol_id, cred_id = [d.id for d in devs], [r.id for r in res], pol.id, cred.id
page = s.get(B + "/compliance").text
check("un solo botón «Corregir en 7 equipos…»", page.count("Corregir en 7 equipos…") == 1 and "Corregir ux-sw" not in page)
dlg = page[page.find('<dialog class="dlg" id="fix-'):]
dlg = dlg[:dlg.find("</dialog>")]
check("el diálogo lista los 7 equipos con casillas marcadas", len(re.findall(r'name="result_id" value="\d+" checked', dlg)) == 7)
check("la lista de nombres de la regla se recorta («y 2 más»)", "y 2 más" in page)
r = s.post(B + "/compliance/remediate-rule", data={"result_id": res_ids[:2]}, allow_redirects=False)
with SessionLocal() as db:
    crs = db.query(ChangeRequest).filter(ChangeRequest.title.like("Corrección 'UX sin telnet'%")).all()
    created = sorted(json.loads(c.device_ids)[0] for c in crs)
check("solo se crean solicitudes para los equipos marcados", created == sorted(dev_ids[:2]), created)
r = s.post(B + "/compliance/remediate-rule", data={}, allow_redirects=False)
check("sin equipos marcados no crea nada", r.status_code in (302, 303) and r.headers.get("location") == "/compliance")

# 3. Consola: credencial asignada sin inicio automático → aviso con enlace a Ajustes (administrador)
set_setting("console_access", "admin")
set_setting("console_autologin", "0")
page = s.get(B + f"/devices/{dev_ids[0]}/console").text
check("consola en ventana: aviso con enlace a Ajustes", 'id="autologin-off"' in page and 'href="/settings#seguridad"' in page)
page = s.get(B + f"/devices/{dev_ids[0]}/console?embed=1").text
check("consola en el diálogo: avisa a la página (nocred)", 'const NOCRED = "admin";' in page)
set_setting("console_autologin", "1")
import time  # noqa: E402

time.sleep(31)  # los ajustes se guardan en caché 30 s
page = s.get(B + f"/devices/{dev_ids[0]}/console?embed=1").text
check("con el inicio automático activado no hay aviso", 'const NOCRED = "";' in page)
set_setting("console_autologin", "0")

with SessionLocal() as db:
    for c in db.query(ChangeRequest).filter(ChangeRequest.title.like("Corrección 'UX sin telnet'%")):
        db.delete(c)
    db.query(ComplianceResult).filter(ComplianceResult.policy_id == pol_id).delete()
    db.query(Rule).filter(Rule.policy_id == pol_id).delete()
    db.query(Policy).filter(Policy.id == pol_id).delete()
    db.query(Device).filter(Device.id.in_(dev_ids)).delete()
    db.query(Credential).filter(Credential.id == cred_id).delete()
    db.commit()

if FAILS:
    print(f"\n{len(FAILS)} FALLOS")
    sys.exit(1)
print("\nTODO OK")
