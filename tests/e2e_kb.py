"""Equipo › Base de conocimiento: acceso por rol en el servidor (solo lectura apagado por omisión, «Pedir acceso»),
un operador documenta y envía a revisión (las claves se ocultan solas), el admin aprueba o pide cambios, «Proponer
cambio» en un caso publicado, «Me sirvió», comentarios, «Convertir en configlet», una alarma crítica no se cierra sin
caso o enlace, «Falta documentar» (enlazar y «No hace falta» solo admin), reversiones que dejan pendiente y Auditoría."""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import kb  # noqa: E402
from app.crypto import hash_password  # noqa: E402
from app.db import (AuditLog, ChangeRequest, Device, Event, KbPending, SessionLocal, User, select,  # noqa: E402
                    set_setting)

B = "http://127.0.0.1:8000"
RUN = str(int(time.time()))[-6:]  # títulos distintos en cada corrida (el editor avisa de títulos repetidos)
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(u, p):
    s = requests.Session()
    s.post(B + "/login", data={"username": u, "password": p})
    return s


with SessionLocal() as db:
    for name, role in (("kb_op", "operator"), ("kb_ro", "viewer")):
        if not db.scalar(select(User).where(User.username == name)):
            db.add(User(username=name, password_hash=hash_password("Clave-kb-2026"), role=role))
    db.commit()
    dev = db.scalars(select(Device).order_by(Device.id)).first()
    dev_id, dev_name = dev.id, dev.name
for k in ("kb_operators", "kb_viewers", "kb_publish", *kb.RULES):
    set_setting(k, {"kb_operators": "1", "kb_viewers": "0", "kb_publish": "admin"}.get(k, kb.RULES.get(k, "")))

adm, op, ro = login("admin", "Admin12345"), login("kb_op", "Clave-kb-2026"), login("kb_ro", "Clave-kb-2026")

# --- acceso
r = adm.get(B + "/kb")
check("admin: la página abre con sus pestañas", r.status_code == 200 and "Falta documentar" in r.text and "Acceso y reglas" in r.text)
check("el menú tiene la sección «Equipo» con la base de conocimiento", 'href="/kb"' in r.text)
r = op.get(B + "/kb")
check("operador: la ve, sin «Acceso y reglas»", r.status_code == 200 and "Acceso y reglas" not in r.text and "Documentar caso" in r.text)
r = ro.get(B + "/kb")
check("solo lectura: apagado por omisión, ve el aviso sin el menú", "Pedir acceso a un admin" in r.text and 'href="/kb"' not in r.text)
check("solo lectura: tampoco por la API del editor", ro.post(B + "/kb/save", json={"title": "x"}).status_code == 403)
r = ro.post(B + "/kb/request-access")
check("«Pedir acceso a un admin» deja un aviso para el admin", "Solicitud enviada" in r.text)
check("operador no entra a «Acceso y reglas»", op.get(B + "/kb/access").status_code == 403)

# --- un operador documenta un caso
base = {"title": f"hEX rechaza el respaldo por SSH tras actualizar {RUN}", "category": "respaldos", "devices": [dev_id],
        "symptom": "ssh: handshake failed: no common algorithm", "cause": "",
        "steps": [{"t": "Ver qué acepta", "c": "/ip ssh print"}, {"t": "Usuario de respaldo", "c": "/user set opennccm password=Secreta123"}],
        "verify": ""}
j = op.post(B + "/kb/save", json={**base, "action": "auto"}).json()
cid = j.get("id")
check("guardado automático: crea el borrador", j.get("ok") and cid and j["status"] == "draft", j)
check("la contraseña del comando se oculta al guardar", "Secreta123" not in json.dumps(j) and j["data"]["steps"][1]["hid"]
      and "••••••" in j["data"]["steps"][1]["c"], j.get("data"))
j = op.post(B + "/kb/save", json={**base, "id": cid, "action": "send"}).json()
check("enviar sin causa ni comprobación: dice lo que falta", not j["ok"] and "Causa" in j["missing"] and "Cómo comprobar" in j["missing"], j)
full = {**base, "id": cid, "cause": "RouterOS 7.16 dejó de aceptar RSA de 1024 bits",
        "verify": "El respaldo sale como Correcto", "action": "send"}
j = op.post(B + "/kb/save", json=full).json()
check("el operador envía a revisión (no publica)", j["ok"] and j["status"] == "review", j)
code = f"KB-{cid:04d}"
r = adm.get(B + "/kb/review")
check("en revisión: la tarjeta muestra lo que trae", code in r.text and "1 contraseña ocultada" in r.text and "2 pasos" in r.text)
check("el operador no puede aprobar", op.post(B + f"/kb/{cid}/approve").status_code == 403)
adm.post(B + f"/kb/{cid}/return", data={"comment": "Falta el paso de la credencial"})
r = op.get(B + f"/kb/{cid}")
check("«Pedir cambios»: vuelve a borrador con el comentario", "Borrador" in r.text and "Falta el paso de la credencial" in r.text)
j = op.post(B + "/kb/save", json=full).json()
adm.post(B + f"/kb/{cid}/approve")
r = op.get(B + f"/kb/{cid}")
check("aprobado: publicado con su versión 1", "Publicado" in r.text and "v1" in r.text and "Secreta123" not in r.text)
check("la ficha ofrece Copiar y Abrir en consola", "data-copy" in r.text and "data-run" in r.text and "clave oculta" in r.text)
r = ro.get(B + f"/kb/{cid}")
check("solo lectura sigue sin ver la ficha", "Pedir acceso a un admin" in r.text and "Secreta" not in r.text and "RouterOS 7.16" not in r.text)

# --- búsqueda, voto, comentario
r = op.get(B + "/kb", params={"q": dev_name})
check("buscar por el nombre del equipo encuentra el caso", code in r.text)
r = op.get(B + "/kb", params={"q": "no common algorithm"})
check("buscar por el mensaje de error encuentra el caso", code in r.text)
v1 = op.post(B + f"/kb/{cid}/vote").json()
v2 = adm.post(B + f"/kb/{cid}/vote").json()
check("«Me sirvió»: un voto por persona", v1["on"] and v2["n"] == 2)
check("y se puede quitar", op.post(B + f"/kb/{cid}/vote").json()["n"] == 1)
r = op.post(B + f"/kb/{cid}/comment", data={"text": "En otro equipo hubo que reiniciar SSH; password=Otra-clave9"}, allow_redirects=False)
check("comentar vuelve a la ficha", r.status_code == 303, r.status_code)
r = op.get(B + f"/kb/{cid}")
check("comentario del equipo publicado, con la clave oculta", "hubo que reiniciar SSH" in r.text and "Otra-clave9" not in r.text)

# --- «Proponer cambio»: el operador en un caso publicado (sin publicar directo) → propuesta
j = op.post(B + "/kb/save", json={**full, "mode": "propose", "steps": full["steps"] + [{"t": "Reiniciar SSH", "c": "/ip service disable ssh"}]}).json()
check("cambio en caso publicado: va como propuesta", j["ok"] and j["status"] == "proposal", j)
r = adm.get(B + "/kb/review")
check("la propuesta muestra qué cambió (+ 1 paso)", "Cambio en caso publicado" in r.text and "+ 1 paso" in r.text)
pid = re.search(r"/kb/proposals/(\d+)/approve", r.text).group(1)
adm.post(B + f"/kb/proposals/{pid}/approve")
r = op.get(B + f"/kb/{cid}")
check("al aprobarla queda la versión 2 con el paso nuevo", "v2" in r.text and "Reiniciar SSH" in r.text)
r = op.get(B + f"/kb/{cid}?v=1")
check("se puede ver la versión 1", "Estás viendo la versión 1" in r.text and "Reiniciar SSH" not in r.text)

# --- títulos repetidos: «Casos parecidos» lo marca y al enviar se avisa (se puede enviar igual)
sim = op.get(B + "/kb/api/similar", params={"title": base["title"].upper(), "q": "", "exclude": 0}).json()
check("«Casos parecidos» detecta el mismo título", sim["items"] and sim["items"][0]["id"] == cid and sim["items"][0]["same"], sim)
sim = op.get(B + "/kb/api/similar", params={"title": "hEX rechaza respaldo por SSH tras actualizar " + RUN, "exclude": 0}).json()
check("y uno casi igual", any(i["id"] == cid for i in sim["items"]), sim)
dup = {**full, "id": None, "mode": "new"}
j = op.post(B + "/kb/save", json=dup).json()
check("al enviar con el mismo título avisa «Ya hay un caso con este título»", not j["ok"] and j.get("dup", {}).get("id") == cid
      and "Ya hay un caso con este título" in j.get("error", ""), j)
j = op.post(B + "/kb/save", json={**dup, "allow_dup": True}).json()
check("y si la persona sigue, se envía", j["ok"] and j["status"] == "review", j)
adm.post(B + f"/kb/{j['id']}/archive")

# --- convertir en configlet
r = op.post(B + f"/kb/{cid}/configlet", allow_redirects=False)
check("«Convertir en configlet» crea el configlet", r.status_code == 303 and "/configlets/" in r.headers.get("location", ""))

# --- una alarma crítica no se cierra sin caso o enlace
with SessionLocal() as db:
    e1 = Event(severity="critical", category="Respaldo fallido", device_id=dev_id, message=f"{dev_name}: respaldo fallido (prueba)")
    e2 = Event(severity="critical", category="Interfaz caída", device_id=dev_id, message=f"{dev_name}: ether4 caída (prueba)")
    db.add_all([e1, e2])
    db.commit()
    e1_id, e2_id = e1.id, e2.id
r = op.post(B + "/events/ack", data={"id": str(e1_id)}, allow_redirects=False)
check("reconocer una crítica lleva a «Cerrar alarma crítica»", r.status_code == 303 and r.headers["location"] == f"/kb/close/{e1_id}", r.headers)
op.post(B + "/events/ack", data={})
with SessionLocal() as db:
    check("«reconocer todas» no cierra las críticas", not db.get(Event, e1_id).acknowledged and not db.get(Event, e2_id).acknowledged)
r = op.post(B + f"/kb/close/{e1_id}", data={"how": "doc"}, allow_redirects=False)
check("«Cerrar y documentar» abre el editor desde el pendiente", "/kb/new?pending=" in r.headers.get("location", ""), r.headers)
p_id = int(r.headers["location"].split("=")[1])
r = op.get(B + f"/kb/new?pending={p_id}")
check("el editor ya trae la alarma y el equipo", f"A-{e1_id}" in r.text and f'"devices": [{dev_id}]' in r.text)
op.post(B + f"/kb/close/{e2_id}", data={"how": "link", "case_id": str(cid)})
with SessionLocal() as db:
    check("las dos alarmas quedan cerradas", db.get(Event, e1_id).acknowledged and db.get(Event, e2_id).acknowledged)
    p2 = db.scalar(select(KbPending).where(KbPending.kind == "event", KbPending.source_id == e2_id))
    check("la enlazada queda resuelta con el caso", p2 is not None and p2.state == "linked" and p2.case_id == cid)
r = op.get(B + "/kb/pending")
check("«Falta documentar» lista la que falta, con quién la resolvió", "kb_op" in r.text and "respaldo fallido" in r.text.lower())
check("el operador no ve «No hace falta»", "No hace falta" not in r.text)
check("ni puede usarlo", op.post(B + f"/kb/pending/{p_id}/dismiss").status_code == 403)
j = op.post(B + "/kb/save", json={**full, "id": None, "mode": "new", "pending": p_id, "source_kind": "event", "source_id": e1_id,
                                   "title": f"Respaldo fallido: SSH colgado {RUN}", "action": "send"}).json()
with SessionLocal() as db:
    p1 = db.get(KbPending, p_id)
    check("al enviar el caso el pendiente se enlaza a él", p1.case_id == j["id"])
adm.post(B + f"/kb/{j['id']}/approve")
with SessionLocal() as db:
    check("y al publicarse queda documentado", db.get(KbPending, p_id).state == "documented")

# --- reversión → pendiente; «No hace falta» del admin
with SessionLocal() as db:
    cr = ChangeRequest(title=f"Reversión de {dev_name} a la versión #1", body="no ntp server 10.0.0.1", variables="{}",
                       device_ids=json.dumps([dev_id]), status="done", requested_by="kb_op", is_rollback=True)
    db.add(cr)
    db.commit()
    cr_id = cr.id
kb.on_rollback_done(cr_id)
with SessionLocal() as db:
    pc = db.scalar(select(KbPending).where(KbPending.kind == "change", KbPending.source_id == cr_id))
    check("una reversión terminada deja su pendiente", pc is not None and pc.assignee == "kb_op")
r = op.get(B + f"/kb/new?pending={pc.id}")
check("el editor trae los comandos de la reversión", "no ntp server 10.0.0.1" in r.text)
adm.post(B + f"/kb/pending/{pc.id}/dismiss")
with SessionLocal() as db:
    pc = db.get(KbPending, pc.id)
    check("«No hace falta» del admin queda registrado con quién", pc.state == "dismissed" and pc.decided_by == "admin")

# --- reglas y acceso
adm.post(B + "/kb/access", data={"kb_viewers": "on", "kb_operators": "on", "kb_publish": "all", "kb_rule_critical": "on",
                                  "kb_rule_revert": "on", "kb_rule_mail": "on"})
r = ro.get(B + f"/kb/{cid}")
check("con «Solo lectura» encendido, lee los casos", "RouterOS 7.16" in r.text and "Editar" not in r.text)
j = op.post(B + "/kb/save", json={**full, "id": None, "mode": "new", "title": f"Caso directo {RUN}", "action": "send"}).json()
check("con «Admin y operadores», el operador publica sin revisión", j["ok"] and j["status"] == "published", j)
adm.post(B + "/kb/access", data={"kb_publish": "admin", "kb_rule_critical": "on", "kb_rule_revert": "on", "kb_rule_mail": "on"})
r = op.get(B + "/kb")
check("con «Operadores» apagado, el operador deja de verla", "Pedir acceso a un admin" in r.text)
set_setting("kb_operators", "1")
set_setting("kb_viewers", "0")
r = adm.post(B + f"/kb/{cid}/archive", allow_redirects=False)
check("archivado: el operador ya no lo abre", r.status_code == 303 and op.get(B + f"/kb/{cid}").status_code == 403, r.status_code)
r = adm.post(B + f"/kb/{cid}/restore", allow_redirects=False)
check("restaurado: vuelve", r.status_code == 303 and op.get(B + f"/kb/{cid}").status_code == 200, r.status_code)

# --- auditoría
with SessionLocal() as db:
    acts = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("kb_%"))))
check("lecturas y ediciones quedan en Auditoría", {"kb_read", "kb_send", "kb_publish", "kb_return", "kb_proposal", "kb_vote",
                                                   "kb_comment", "kb_archive", "kb_settings", "kb_access_request"} <= acts, acts)

# --- ocultar secretos
for text in ("username admin secret 5 $1$abc", "snmp-server community publica RO", "/user set opennccm password=x1",
             "curl -H token=abc123"):
    out, n = kb.mask(text)
    check(f"se oculta: {text}", n >= 1 and "••••••" in out, out)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
