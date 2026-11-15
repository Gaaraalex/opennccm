"""Estadística de uso en la interfaz: Administración › Sistema la explica (sin interruptor), muestra el último aviso y el
JSON real; «Privacidad y licencias» lo cuenta; y sin salida a internet (NCM_USO_URL a un puerto cerrado, como en todas
las pruebas) el sistema funciona igual, sin mostrar errores. Requiere e2e.py (admin)."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import get_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
page = adm.get(B + "/system").text
check("Sistema: «Estadística de uso» con el texto y la frase obligatoria", "Estadística de uso" in page
      and "esa información nos ayuda a seguir mejorando el sistema" in page and "comunidad.opennccm.com" in page)
check("…«Ver exactamente qué se envía» y el último aviso", "Ver exactamente qué se envía" in page
      and ("Último aviso:" in page or "Todavía no se envió ningún aviso." in page))
m = re.search(r'<pre class="uso-js">(.*?)</pre>', page, re.S)
sent = json.loads(m.group(1).replace("&#34;", '"').replace("&quot;", '"')) if m else {}
check("…el JSON real: exactamente {instalacion, version, pro} con el número u_ de esta instalación",
      set(sent) == {"instalacion", "version", "pro"} and sent.get("instalacion") == get_setting("uso_id"), sent)
card = page.split('id="estadistica"', 1)[-1].split("</section>", 1)[0]
check("…sin interruptor para apagarlo", 'type="checkbox"' not in card and "<form" not in card)
check("sin internet: ningún error a la vista y el panel funciona", adm.get(B + "/").status_code == 200
      and "No se pudo avisar" not in page and "uso/aviso" not in page.split('id="estadistica"')[0])
page = adm.get(B + "/acerca").text
check("Privacidad y licencias: explica la estadística (siempre activa) y que no lleva datos de la red",
      "Estadística de uso (siempre activa)" in page and "no guarda la dirección IP" in page
      and "no hay analíticas" not in page)
r = requests.get(B + "/system", allow_redirects=False)
check("Sistema sigue siendo solo para administradores", r.status_code in (302, 303, 401, 403))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
