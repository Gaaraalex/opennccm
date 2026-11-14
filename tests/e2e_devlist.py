"""Lista de equipos y vistas relacionadas (2.43): eliminar los equipos elegidos (uno o varios, con sus versiones), botón
de ping en las acciones de cada equipo, «Copiar todo» en la versión de una copia (en bloques, con los números de línea
por CSS) y los botones de parámetros de Herramientas en la consola. Requiere haber ejecutado antes e2e.py (admin)."""
import hashlib
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Device, SessionLocal, select  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})

# tres equipos de prueba, uno con una copia de 600 líneas
text = "\n".join(f"interface ge-0/0/{i}\n description enlace {i}" for i in range(300)) + "\n"
ids = []
with SessionLocal() as db:
    for n in ("DL-borrar-1", "DL-borrar-2", "DL-queda"):
        d = db.scalar(select(Device).where(Device.name == n))
        if d is None:
            d = Device(name=n, host="127.0.0.9", device_type="cisco_ios", last_status="ok")
            db.add(d)
            db.flush()
        ids.append(d.id)
    v = ConfigVersion(device_id=ids[0], kind="running", content_enc=encrypt(text), sha256=hashlib.sha256(text.encode()).hexdigest(),
                      size=len(text), trigger="manual", username="admin")
    db.add(v)
    db.commit()
    vid = v.id

page = adm.get(B + "/devices").text
check("lista: botón de ping en las acciones de cada equipo", 'data-ping="127.0.0.9"' in page and "#i-pulse" in page)
check("lista: «Eliminar…» en la barra de los elegidos, con su diálogo", 'data-dialog="bulk-del-dlg"' in page
      and 'id="bulk-del-dlg"' in page and 'data-expect="ELIMINAR"' in page)

ver = adm.get(f"{B}/versions/{vid}").text
check("versión: «Copiar todo»", "data-cv-copy" in ver and "Copiar todo" in ver)
check("versión: en bloques de 250 líneas que se dibujan al verse", ver.count('class="cvc"') == 3 and "counter-reset:l 250" in ver)
check("versión: sin el número de línea en el texto (no se copia)", '<span class="n">' not in ver and "<p>interface ge-0/0/0</p>" in ver)

cons = adm.get(B + "/console").text
check("Herramientas: parámetros como botones, por herramienta", 'data-arg="-t"' in cons and 'data-arg="-n 10"' in cons
      and 'class="tl-params" data-for="dns"' in cons and 'data-arg="MX"' in cons)

# eliminar dos de los tres de una vez (la acción de la barra), con sus versiones
r = adm.post(B + "/devices/bulk", data={"action": "delete", "ids": [str(ids[0]), str(ids[1])], "next": "/devices"})
with SessionLocal() as db:
    left = {d.name for d in db.scalars(select(Device).where(Device.id.in_(ids)))}
    vers = db.scalar(select(ConfigVersion).where(ConfigVersion.id == vid))
check("se eliminan los elegidos y queda el otro", left == {"DL-queda"}, left)
check("con sus versiones", vers is None)
check("aviso con cuántos se eliminaron", "2 equipos eliminados" in r.text, re.findall(r"eliminad\w+", r.text)[:3])

# un lector no puede eliminar
lec = requests.Session()
adm.post(B + "/users", data={"username": "dl_lector", "role": "viewer", "password": "Lector-DL-2026", "password2": "Lector-DL-2026"})
lec.post(B + "/login", data={"username": "dl_lector", "password": "Lector-DL-2026"})
r = lec.post(B + "/devices/bulk", data={"action": "delete", "ids": [str(ids[2])]}, allow_redirects=False)
with SessionLocal() as db:
    still = db.get(Device, ids[2]) is not None
check("un lector no puede eliminar equipos", still and r.status_code in (302, 303, 403), r.status_code)
with SessionLocal() as db:
    d = db.get(Device, ids[2])
    if d:
        db.delete(d)
        db.commit()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
