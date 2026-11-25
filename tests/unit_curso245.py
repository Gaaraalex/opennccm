"""Fallos vistos al grabar el curso (2.45.4/2.45.5), sin red ni servidor: cada arreglo probado por su lado.
Seguridad (contraseñas en la base de conocimiento, máscara de la licencia, tareas del asistente sobre todos los equipos),
firmware (respaldo previo en todas las marcas, espera real), impacto con variables, tareas con configlets, CVE (KEV,
alarmas repetidas, Aruba CX, Huawei, imagen más nueva), ciclo de vida, asistente (modelo local, severidad), panel,
análisis, nombres de tareas e instalador."""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import aichat, assistant, eol, firmware, fwverify, health, impact, licencia, reports, scheduler, services  # noqa: E402
from app.db import (ChangeRequest, ConfigVersion, Configlet, Device, DeviceGroup, EolEntry, Event, Schedule,  # noqa: E402
                    SessionLocal, User, UserGroup, Vulnerability, init_db, now, select, set_setting)
from app.drivers import get_profile  # noqa: E402
from app.fwupdates import device_cpe_version  # noqa: E402
from app.kb import mask  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


init_db()

# ------------------------------------------------------------------ 1. contraseñas en la base de conocimiento
for text, secret in [("R1(config)#username X privilege 15 secret Temporal2026", "Temporal2026"),
                     ("no username X privilege 15 secret 5 $1$abc$xyz", "$1$abc$xyz"),
                     ("Se detectó «secret Intruso2026» en RT-Central", "Intruso2026"),
                     ("enable password 7 0822455D0A16", "0822455D0A16"),
                     ("username admin password 0 Clave1", "Clave1"),
                     ("password cipher %^%#abc%^%#", "%^%#abc%^%#"),
                     (" password irreversible-cipher $1a$xx$", "$1a$xx$")]:
    out, n = mask(text)
    check(f"KB: oculta «{secret}» en «{text[:40]}»", secret not in out and n >= 1, out)
out, n = mask("username X privilege 15 secret 5 ******")
check("KB: lo ya oculto no se cuenta dos veces", n == 0 and out.count("••••••") == 1, (out, n))
for text in ("service password-encryption", "la password es nueva", "no secret"):
    check(f"KB: no toca «{text}»", mask(text)[0] == text, mask(text))
from app.gitexport import mask as gmask  # noqa: E402
check("Git: «password cipher X» oculta la clave, no la palabra «cipher»",
      gmask("password cipher Abc123") == "password cipher <oculto>", gmask("password cipher Abc123"))

# ------------------------------------------------------------------ 3. máscara de la licencia
k = licencia.mask_key("OPNC-7F3K-ABCD-EFGH-Q2LM")
check("Licencia: solo los 4 últimos caracteres", k == "OPNC-····-····-····-Q2LM" and "7F3K" not in k, k)

# ------------------------------------------------------------------ datos comunes
with SessionLocal() as db:
    g1, g2 = DeviceGroup(name="Core"), DeviceGroup(name="Sucursal")
    db.add_all([g1, g2])
    db.flush()
    devs = [Device(name="R1", host="192.0.2.1", device_type="cisco_ios", group_id=g1.id, os_version="15.2(7)E8"),
            Device(name="R2", host="192.0.2.2", device_type="cisco_ios", group_id=g1.id),
            Device(name="S1", host="192.0.2.3", device_type="cisco_ios", group_id=g2.id),
            Device(name="VIEJO", host="192.0.2.4", device_type="cisco_ios", retired_at=now())]
    db.add_all(devs)
    admin = User(username="adm", password_hash="x", role="admin")
    op = User(username="op", password_hash="x", role="operator")
    db.add_all([admin, op])
    db.flush()
    db.add(UserGroup(user_id=op.id, group_id=g1.id))
    db.commit()
    ids = {d.name: d.id for d in db.scalars(select(Device))}
    G = {"Core": g1.id, "Sucursal": g2.id}
    for u in (admin, op):
        db.expunge(u)

# ------------------------------------------------------------------ 4. tarea «Respaldar» del asistente
with SessionLocal() as db:
    gid, txt = aichat._schedule_scope(db, admin, {"accion_tarea": "backup", "grupo": "core"})
    check("Asistente: tarea con grupo → ese grupo y lo dice", gid == G["Core"] and "Core" in txt and "2" in txt, (gid, txt))
    gid, txt = aichat._schedule_scope(db, admin, {"accion_tarea": "backup"})
    check("Asistente: sin grupo → «Todos los equipos (3)» en el resumen, no a escondidas",
          gid is None and "Todos los equipos (3)" in txt, (gid, txt))
    check("Asistente: grupo que no existe → no se ofrece (nunca cae en «todos»)",
          aichat._schedule_scope(db, admin, {"accion_tarea": "backup", "grupo": "Nadie"}) == (None, ""))
    check("Asistente: usuario limitado a Core, sin grupo → no se ofrece",
          aichat._schedule_scope(db, op, {"accion_tarea": "backup"}) == (None, ""))
    check("Asistente: usuario limitado a Core, sobre Sucursal → no se ofrece",
          aichat._schedule_scope(db, op, {"accion_tarea": "cve", "grupo": "Sucursal"}) == (None, ""))
    check("Asistente: usuario limitado a Core, sobre Core → sí",
          aichat._schedule_scope(db, op, {"accion_tarea": "cve", "grupo": "Core"})[0] == G["Core"])
acts = aichat._resolve(admin, [{"tipo": "tarea", "etiqueta": "Respaldar", "accion_tarea": "backup", "cron": "0 * * * *",
                                "grupo": "Core"}])
check("Asistente: la acción preparada lleva el grupo", acts and acts[0]["group_id"] == G["Core"] and "Core" in acts[0]["summary"],
      acts)
check("Asistente: el esquema de la respuesta tiene «grupo»", "grupo" in aichat.ANSWER["properties"]["acciones"]["items"]["properties"])
# confirmarla crea la tarea solo sobre ese grupo
with SessionLocal() as db:
    from app.db import AiChat
    db.add(AiChat(username="adm", messages=json.dumps([{"role": "assistant", "actions": acts}])))
    db.commit()
scheduler.reload_jobs = lambda: None
res = aichat.act(admin, acts[0]["id"])
with SessionLocal() as db:
    s = db.scalar(select(Schedule).order_by(Schedule.id.desc()))
check("Asistente: «Crear la tarea» la deja con su grupo", res.get("ok") and s and s.group_id == G["Core"], (res, s and s.group_id))

# ------------------------------------------------------------------ 6 y 13. firmware: respaldo previo y espera
calls = []
services.backup_device = lambda i, trig, who: calls.append((i, trig, who)) or "changed"


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


services.connect = lambda dev: _Conn()
firmware._scp_boot = lambda conn, img, fw, reload, log, progress: log.append("scp")
firmware.ensure_sha256 = lambda i: None
from app.db import FirmwareImage  # noqa: E402
with SessionLocal() as db:
    img = FirmwareImage(filename="c2960x.bin", version="15.2(7)E10", size=1, md5="x")
    db.add(img)
    db.commit()
    img_id = img.id
fwverify.verify_after_upgrade = lambda *a, **k: {"ok": True, "applied": True}
try:
    firmware.upgrade_device(ids["R1"], img_id, reload=False)
except Exception as e:  # noqa: BLE001
    print("   (upgrade:", e, ")")
check("Firmware: Cisco (SCP) hace el respaldo previo, como dice la pantalla",
      any(c[0] == ids["R1"] and c[1] == "push" for c in calls), calls)
check("Firmware: la pantalla dice la espera real (90 s + 20 min ≈ 22 min)", fwverify.wait_minutes() == 22, fwverify.wait_minutes())

# ------------------------------------------------------------------ 8. impacto con la plantilla sin sustituir
with SessionLocal() as db:
    cr = ChangeRequest(title="x", body="interface {{ puerto }}\n shutdown", variables=json.dumps({"puerto": "Gi0/1"}),
                       device_ids=json.dumps([ids["R1"]]), requested_by="adm")
    ports, unk = impact._ports_per_device(cr.body, [ids["R1"]], {"puerto": "Gi0/1"})
check("Impacto: «interface {{ puerto }}» con su valor → Gi0/1", ports.get(ids["R1"]) == ["Gi0/1"] and not unk, ports)
ports, unk = impact._ports_per_device("interface {{ puerto }}\n shutdown", [ids["R1"]], {})
check("Impacto: variable sin valor → desconocido (no «Sin impacto»)", unk, (ports, unk))
cr2 = ChangeRequest(title="x", body="interface {{ puerto }}\n shutdown", variables="{}", device_ids=json.dumps([ids["R1"]]))
r = impact.for_change(cr2)
check("Impacto: la solicitud con variable sin valor sale «Impacto desconocido»", r and r["level"] == "nodata", r and r["level"])
check("Impacto: sin variables sigue igual", impact._ports_per_device("interface Gi0/2\n shutdown", [1], {})[0] == {1: ["Gi0/2"]})

# ------------------------------------------------------------------ 10 y 11. tareas con configlets
with SessionLocal() as db:
    c1 = Configlet(name="Apagar puerto", kind="config", body="interface {{ puerto }}\n shutdown")
    c2 = Configlet(name="Ver", kind="exec", body="show version")
    c3 = Configlet(name="NTP", kind="config", body="ntp server 192.0.2.10\nsnmp-server location {{ device.location }}")
    db.add_all([c1, c2, c3])
    db.flush()
    s1 = Schedule(name="Apagar", action="configlet", cron="0 3 * * *", configlet_id=c1.id)
    s2 = Schedule(name="Ver todo", action="configlet", cron="0 3 * * *", configlet_id=c2.id)
    db.add_all([s1, s2])
    db.commit()
    sids = (s1.id, s2.id)
    cids = (c1.id, c2.id, c3.id)
check("Configlet: variables propias detectadas (sin «device»)", services.template_vars("interface {{ puerto }} {{ device.name }}") == ["puerto"])
check("Configlet: «device.location» no cuenta como variable propia",
      services.template_vars("snmp-server location {{ device.location }}") == [])
executed = []
services.execute_change_request = lambda cid: executed.append(cid)
pushed = []
services.push_commands = lambda *a, **k: pushed.append(a)
before = {e.id for e in SessionLocal().scalars(select(Event))}
scheduler.run_schedule(sids[0])
scheduler.run_schedule(sids[1])
with SessionLocal() as db:
    new = [e for e in db.scalars(select(Event)) if e.id not in before and e.category == "Tarea programada sin aplicar"]
check("Tarea: configlet con variables sin valor → no se aplica y queda la alarma",
      not executed and any("puerto" in e.message for e in new), [e.message for e in new])
check("Tarea: configlet de ejecución → no se envía a ciegas y queda la alarma",
      not pushed and any("ejecución" in e.message for e in new), [e.message for e in new])

# ------------------------------------------------------------------ 12, 16 y 17. CVE
src = (ROOT / "app/templates/device.html").read_text(encoding="utf-8")
check("KEV: la ficha usa la lista KEV (no «v.kev», que no existe)", "v.cve_id in kev" in src and "v.kev" not in src)
check("Aruba CX: «FL.10.10.1000» se busca en NVD como «10.10.1000»",
      device_cpe_version(get_profile("aruba_aoscx"), "FL.10.10.1000") == "10.10.1000",
      device_cpe_version(get_profile("aruba_aoscx"), "FL.10.10.1000"))
check("Huawei VRP y VRP v8: aviso de que un 0 no es fiable",
      (get_profile("huawei").cve_note or ("",))[0] == "huawei" and (get_profile("huawei_vrpv8").cve_note or ("",))[0] == "huawei")
check("Huawei: el aviso tiene su texto en la ficha", "'huawei': _(" in src)
crit = [{"cve": {"id": f"CVE-2026-000{i}", "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]},
                 "descriptions": [{"lang": "en", "value": "x"}]}} for i in range(2)]
services.refresh_kev = lambda: None
services.nvd_searches = lambda *a, **k: [{}]
services.nvd_query = lambda p: list(crit)
services.send_alert = lambda *a, **k: []


def cve_alarms():
    with SessionLocal() as db:
        return [e for e in db.scalars(select(Event).where(Event.category == "Vulnerabilidad de firmware",
                                                          Event.device_id == ids["R1"]))]


services.scan_cves(ids["R1"])
services.scan_cves(ids["R1"])
check("CVE: dos búsquedas iguales → una sola alarma", len(cve_alarms()) == 1, len(cve_alarms()))
crit.append({"cve": {"id": "CVE-2026-0009", "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.1, "baseSeverity": "CRITICAL"}}]},
                     "descriptions": [{"lang": "en", "value": "x"}]}})
services.scan_cves(ids["R1"])
al = cve_alarms()
check("CVE: si cambia la lista se actualiza la misma alarma", len(al) == 1 and "3 CVE" in al[0].message, [a.message for a in al])
with SessionLocal() as db:
    db.get(Event, al[0].id).acknowledged = True
    db.commit()
services.scan_cves(ids["R1"])
check("CVE: reconocida la anterior, la siguiente búsqueda abre otra", len(cve_alarms()) == 2, len(cve_alarms()))

# ------------------------------------------------------------------ 18. ciclo de vida: importar dos veces
CSV = ("vendor,model_pattern,end_of_sale,end_of_sw_maintenance,end_of_support,replacement,url\n"
       "Cisco,^WS-C2960-,2016-10-30,2017-10-30,2021-10-31,C9200,\n")
eol.import_csv(CSV)
eol.import_csv(CSV.replace("C9200", "C9200L"))
with SessionLocal() as db:
    rows = db.scalars(select(EolEntry).where(EolEntry.model_pattern == "^WS-C2960-")).all()
check("Ciclo de vida: importar dos veces no duplica y actualiza", len(rows) == 1 and rows[0].replacement == "C9200L",
      [(r.id, r.replacement) for r in rows])

# ------------------------------------------------------------------ 20 y 21. asistente
check("IA local: Ollama en 127.0.0.1", assistant.is_local({"provider": "compat", "base": "http://127.0.0.1:11434/v1"}))
check("IA local: Ollama en 10.0.0.5", assistant.is_local({"provider": "compat", "base": "http://10.0.0.5:11434/v1"}))
check("IA local: nombre sin dominio (docker «ollama»)", assistant.is_local({"provider": "compat", "base": "http://ollama:11434/v1"}))
check("IA nube: compatible en internet", not assistant.is_local({"provider": "compat", "base": "https://api.groq.com/openai/v1"}))
check("IA nube: Anthropic", not assistant.is_local({"provider": "anthropic", "base": "https://api.anthropic.com/v1"}))
check("IA: el texto «local» existe", assistant.texts().get("local"))
with SessionLocal() as db:
    for i in range(70):
        db.add(Event(severity="warning", category="Prueba", message=f"aviso {i}"))
    db.add(Event(severity="critical", category="Prueba", message="la crítica vieja"))
    db.commit()
    # la crítica queda detrás de 70 avisos más nuevos
    for i in range(70):
        db.add(Event(severity="minor", category="Prueba", message=f"menor {i}"))
    db.commit()
r = aichat.f_alarmas(admin, 60, "critical")
check("IA: «alarmas críticas» las busca en la base, no entre las últimas",
      r["alarmas"] and all(a["severidad"] == "critical" for a in r["alarmas"]), r["alarmas"][:2])
check("IA: la función declara «severidad»", "severidad" in aichat.FUNCS["alarmas"][2])

# ------------------------------------------------------------------ 15. imagen corregida: solo más nueva
from app.fwupdates import vkey  # noqa: E402
check("Firmware: 15.2(7)E10 es más nueva que 15.2(7)E8", vkey("15.2(7)E10") > vkey("15.2(7)E8"))
check("Firmware: 17.9.4a no es más nueva que 17.12.1", not vkey("17.9.4a") > vkey("17.12.1"))
msrc = (ROOT / "app/main.py").read_text(encoding="utf-8")
check("Vulnerabilidades: «con imagen corregida» exige versión más nueva", "vkey(img.version) > vkey(d.os_version)" in msrc)

# ------------------------------------------------------------------ 28. panel: equipos sin respaldo
h = health.summary(None, is_admin=True)
check("Panel: equipos sin respaldo en 24 h piden atención", any("sin respaldo" in q["title"] or "no tiene respaldo" in q["title"]
                                                               for q in h["queue"]), [q["title"] for q in h["queue"]])
dsrc = (ROOT / "app/templates/dashboard.html").read_text(encoding="utf-8")
check("Panel: «Todo respaldado» solo si todos lo están", "h.backed == h.total %}Todo respaldado" in dsrc)

# ------------------------------------------------------------------ 29. análisis sin retirados
with SessionLocal() as db:
    for n in ("R1", "R2", "VIEJO"):
        db.add(ConfigVersion(device_id=ids[n], kind="running", content_enc="x", sha256=n, trigger="manual",
                             created_at=now() - timedelta(days=1)))
    db.commit()
a = reports.analytics(None, 30)
check("Análisis: equipos con cambios no cuenta el retirado", a["devices_changed"] == 2 and a["total"] == 3,
      (a["devices_changed"], a["total"]))

# ------------------------------------------------------------------ 32. nombres de tareas sin frecuencia
from app.main import SCHEDULE_NAMES  # noqa: E402
check("Tareas: ningún nombre propuesto dice la frecuencia",
      not any(w in v.lower() for v in SCHEDULE_NAMES.values() for w in ("semanal", "nocturno", "diario", "mensual")), SCHEDULE_NAMES)

# ------------------------------------------------------------------ 9. Ajustes fuera del bucle de eventos
check("Ajustes: las escrituras van en un hilo (run_in_threadpool)",
      re.search(r"async def settings_save.*?run_in_threadpool\(_settings_apply", msrc, re.S) is not None)

# ------------------------------------------------------------------ 26 y 27. plantillas
check("403: con la plantilla de siempre", "_error_page(request, 403" in msrc and (ROOT / "app/templates/error.html").is_file())
asrc = (ROOT / "app/templates/acerca.html").read_text(encoding="utf-8")
check("Acerca de: <div> equilibrados", len(re.findall(r"<div\b", asrc)) == asrc.count("</div>"))
bsrc = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
check("Consola (compact): carga warn.js", re.search(r"\{% elif user %\}.*?warn\.js", bsrc, re.S) is not None)

# ------------------------------------------------------------------ 2, 33, 34 y 35. instalador y comando opennccm
ins = (ROOT / "install.sh").read_text(encoding="utf-8")
check("Instalador: guarda/muestra la contraseña solo si la generó él",
      "GEN_PASS=1" in ins and "if [[ $GEN_PASS -eq 1 ]]; then\n  cat > /root/opennccm-credenciales.txt" in ins
      and "la que usted eligió" in ins)
check("Instalador: no reinstala dependencias si requirements.txt no cambió", ".opennccm-req" in ins)
sh = (ROOT / "scripts/opennccm.sh").read_text(encoding="utf-8")
check("opennccm restore: no pide reiniciar a mano", "--by-wrapper" in sh and '"--by-wrapper" not in args' in
      (ROOT / "app/cli.py").read_text(encoding="utf-8"))
check("opennccm: espera configurable (240 s) y lo avisa", "HEALTH_WAIT=${NCM_HEALTH_WAIT:-240}" in sh and "puede tardar unos minutos" in sh)
for script in ("install.sh", "scripts/opennccm.sh"):
    src = (ROOT / script).read_text(encoding="utf-8")
    line = next(x for x in src.splitlines() if x.startswith("if [[ ${TERM:-} == linux"))
    try:
        out = subprocess.run(["bash", "-c", line + '\necho "$CK $CX"'], capture_output=True, text=True,
                             env={**os.environ, "TERM": "linux"}, timeout=20).stdout.strip()
        check(f"{script}: en la consola de Linux, «OK»/«X» en vez de ✓/✗", out == "OK X", out)
    except FileNotFoundError:
        print("   (sin bash: no se prueba", script, ")")
    rest = src.replace(line, "")
    check(f"{script}: ningún ✓/✗ suelto fuera de la detección", "✓" not in rest.replace("«✓»/«✗»", "") and
          "✗" not in rest.replace("«✓»/«✗»", ""))

print("\n" + ("RESULTADO: TODO OK" if not fails else f"RESULTADO: {len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
