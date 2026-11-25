"""Fallos vistos al grabar el curso (2.45.4/2.45.5), contra el servidor de prueba (tests/run-tests.sh): páginas públicas
con estilo y pestañas, la página 403 con el menú, la consola con su aviso que bloquea, importar CSV con las mismas
comprobaciones que la vista previa, tareas programadas con configlets, el aviso de privacidad con un modelo local, la
marca KEV en la ficha y los textos de firmware."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.db import (Configlet, Device, FirmwareImage, Schedule, SessionLocal, Vulnerability, select,  # noqa: E402
                    set_setting)

B = "http://127.0.0.1:8000"
FAILS = []
PW = "Curso245-Clave!"


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})

# --- 25 y 27: páginas públicas sin sesión
anon = requests.Session()
p = anon.get(B + "/certificado").text
check("/certificado sin sesión: cabecera con la marca y «Iniciar sesión»", 'class="pubh"' in p and "/login" in p)
check("/certificado sin sesión: contenido con el diseño (main.view)", 'class="view"' in p)
check("/certificado sin sesión: las pestañas se arman (script propio)", "sin sesión no se carga shell.js" in p and "data-tabs" in p)
p = anon.get(B + "/acerca").text
check("/acerca sin sesión: con cabecera y diseño", 'class="pubh"' in p and 'class="view"' in p)
p = adm.get(B + "/acerca").text
main = p[p.index('<main id="contenido"'):]
check("/acerca: Accesibilidad queda dentro de <main> (sin </div> de más)",
      main.index('id="accesibilidad"') < main.index("</main>"))
check("/acerca con sesión: sin la cabecera pública", 'class="pubh"' not in p)

# --- 26: 403 con el menú
adm.post(B + "/users", data={"username": "curso-lector", "role": "viewer", "password": PW, "password2": PW})
lec = requests.Session()
lec.post(B + "/login", data={"username": "curso-lector", "password": PW})
r = lec.get(B + "/users")
check("403: sigue siendo 403", r.status_code == 403, r.status_code)
check("403: con el menú y los estilos de siempre", 'class="rail"' in r.text and "/static/style.css" in r.text
      and "No tiene permisos para esta acción" in r.text, r.text[:200])
r = lec.get(B + "/api/devices", headers={"Accept": "application/json"})
check("403 de /api sigue en JSON", r.status_code in (401, 403) and "<html" not in r.text.lower(), r.text[:120])

# --- 5: la consola carga warn.js (la casilla «Leí el aviso» bloquea Insertar y Enviar como solicitud)
with SessionLocal() as db:
    did = db.scalar(select(Device.id).order_by(Device.id))
page = adm.get(B + f"/devices/{did}/console").text
check("Consola: carga warn.js", "/static/warn.js" in page and "/static/console.js" in page)
check("Consola: tiene la plantilla del aviso con su casilla", 'id="oc-warn-tpl"' in page)
check("Consola: el texto del bloque de varias líneas", "Bloque de {0} líneas" in page)
page = adm.get(B + "/devices").text
check("Las páginas normales cargan warn.js una sola vez", page.count("/static/warn.js") == 1, page.count("/static/warn.js"))

# --- 7: importar CSV con las mismas comprobaciones que la vista previa
csv = ("name,host,device_type,credential,group,port,protocol,location\n"
       "CSV-OK,192.0.2.50,cisco_ios,,,22,ssh,\n"
       "CSV-CRED,192.0.2.51,cisco_ios,NoExiste,,22,ssh,\n"
       "CSV-PROTO,192.0.2.52,cisco_ios,,,22,sssh,\n"
       "CSV-TIPO,192.0.2.53,cisco_iosx,,,22,ssh,\n"
       "CSV-OK,192.0.2.54,cisco_ios,,,22,ssh,\n"
       ",192.0.2.55,cisco_ios,,,22,ssh,\n")
r = adm.post(B + "/devices/import", files={"file": ("equipos.csv", csv.encode())})
with SessionLocal() as db:
    names = set(db.scalars(select(Device.name).where(Device.name.like("CSV-%"))))
check("Importar: solo entra la fila válida (credencial inexistente, «sssh», tipo, repetido y sin nombre se omiten)",
      names == {"CSV-OK"}, names)
check("Importar: dice por qué se omitió cada una", all(x in r.text for x in ("NoExiste", "sssh", "repetido", "falta name")),
      re.findall(r"línea \d+: [^<]+", r.text))

# --- 10 y 11: tareas programadas con configlets
with SessionLocal() as db:
    c = Configlet(name="Curso: apagar puerto", kind="config", body="interface {{ puerto }}\n shutdown")
    db.add(c)
    db.commit()
    cid = c.id
page = adm.get(B + "/schedules/new?action=configlet").text
check("Tarea: el configlet con variables aparece desactivado y dice por qué",
      re.search(rf'<option value="{cid}"[^>]*disabled[^>]*>Curso: apagar puerto — necesita valores para puerto', page) is not None)
r = adm.post(B + "/schedules", data={"name": "Curso", "action": "configlet", "configlet_id": str(cid), "freq": "daily",
                                     "time": "03:00"})
with SessionLocal() as db:
    made = db.scalar(select(Schedule).where(Schedule.name == "Curso"))
check("Tarea: no se guarda con un configlet que necesita valores", made is None and "necesita valores para puerto" in r.text)

# --- 20: aviso de privacidad con modelo local
set_setting("ai_provider", "compat")
set_setting("ai_base_url", "http://127.0.0.1:11434/v1")
from app.auth import clear_settings_cache  # noqa: E402
clear_settings_cache()
page = adm.get(B + "/settings").text
check("IA local: «no salen de tu red», no «fuera de tu red»", "no salen de ella" in page and "fuera de tu red." not in page)
set_setting("ai_base_url", "https://api.ejemplo.com/v1")
page = adm.get(B + "/settings").text
check("IA en internet: sigue el aviso «fuera de tu red»", "fuera de tu red" in page)
check("IA: «Cargar de la cuenta» ya no elige el primero de la lista", "aiCur = j.models[0]" not in page and "Elija un modelo" in page)
set_setting("ai_provider", "anthropic")
set_setting("ai_base_url", "")

# --- 12: marca KEV en la ficha
with SessionLocal() as db:
    db.add(Vulnerability(device_id=did, cve_id="CVE-2023-20198", score=10.0, severity="CRITICAL", description="x"))
    db.commit()
from app import services  # noqa: E402
kev_file = services._kev_file()
kev_file.parent.mkdir(parents=True, exist_ok=True)
old = kev_file.read_text(encoding="utf-8") if kev_file.exists() else None
kev_file.write_text(json.dumps(["CVE-2023-20198"]), encoding="utf-8")
page = adm.get(B + f"/devices/{did}").text
check("Ficha: «explotado activamente» en un CVE del catálogo KEV", "explotado activamente" in page)

# --- 13 y 14: textos de firmware
with SessionLocal() as db:
    dev = db.get(Device, did)
    dtype = dev.device_type
    db.add(FirmwareImage(filename="curso.bin", version="99.1", size=1, md5="x", device_type_filter=f"^{dtype}$"))
    db.commit()
page = adm.get(B + f"/devices/{did}").text
check("Firmware: dice la espera real (unos 22 minutos), no 10", "unos 22 minutos" in page and "en 10 minutos" not in page)
check("Firmware: nunca «SCP a ,»", "SCP a ," not in page)
with SessionLocal() as db:
    mt = Device(name="CURSO-MT", host="192.0.2.60", device_type="mikrotik_routeros")
    db.add(mt)
    db.add(FirmwareImage(filename="routeros-7.20-arm64.npk", version="7.20", size=1, md5="x", device_type_filter="^mikrotik_routeros$"))
    db.commit()
    mid = mt.id
page = adm.get(B + f"/devices/{mid}").text
check("Firmware MikroTik: dice SFTP y RouterOS, no «SCP a ,»", "Se copia por SFTP" in page and "SCP a ," not in page)

# --- 15: «con imagen corregida» solo cuenta imágenes más nuevas
with SessionLocal() as db:
    d = db.get(Device, did)
    d.os_version = "100.0"   # más nueva que la imagen 99.1 cargada
    db.commit()
page = adm.get(B + "/vulns").text
m = re.search(r"(\d+)\s*</b>\s*[^<]*con imagen corregida", page) or re.search(r"con imagen corregida[^0-9]{0,80}(\d+)", page)
with SessionLocal() as db:
    db.get(Device, did).os_version = None
    db.commit()
check("Vulnerabilidades: una imagen más vieja que la instalada no cuenta como corregida",
      "curso.bin" not in page, re.findall(r"[^<>]{0,40}curso\.bin[^<>]{0,40}", page))

if old is None:
    kev_file.unlink()
else:
    kev_file.write_text(old, encoding="utf-8")
print("\n" + ("RESULTADO: TODO OK" if not FAILS else f"RESULTADO: {len(FAILS)} FALLOS: {FAILS}"))
sys.exit(1 if FAILS else 0)
