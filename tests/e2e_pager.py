"""Paginador de 15/25/50/100 en Cumplimiento, Vulnerabilidades, Alarmas, Usuarios, Credenciales, Grupos y Auditoría.
Alarmas pagina en el servidor (?per=&page=); el resto en el navegador (tbody data-pager, shell.js). El tamaño no se
guarda: sin «per» en el enlace siempre son 15. Requiere haber ejecutado antes e2e.py."""
import re
import sys

import requests

sys.path.insert(0, ".")
from app.db import Event, SessionLocal  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
for path in ("/compliance", "/vulns", "/users", "/credentials", "/groups", "/audit"):
    html = s.get(B + path).text
    # sin datos la página muestra un aviso en lugar de la tabla: no hay nada que paginar
    check(f"{path}: la lista tiene paginador", "<tbody data-pager>" in html or "<tbody" not in html, path)
js = s.get(B + "/static/shell.js").text
check("el paginador del navegador empieza en 15 con opciones 25/50/100 y no guarda la elección",
      "size = 15" in js and "[15, 25, 50, 100]" in js and "localStorage" not in js[js.find("function pager"):js.find("$$('[data-pager]')")])

# Alarmas: 40 eventos de prueba para tener varias páginas
with SessionLocal() as db:
    for i in range(40):
        db.add(Event(severity="info", category="Prueba paginador", message=f"evento {i}"))
    db.commit()


def count(html):
    return len(re.findall(r"<li[\s>][\s\S]*?</li>", html[html.find('class="queue'):html.find("</ul>", html.find('class="queue'))]))


h = s.get(B + "/events?unack=0").text
check("Alarmas: 15 por página por omisión", count(h) == 15 and 'class="pager"' in h, count(h))
check("Alarmas: indica cuántas hay en total", re.search(r"Mostrando 1–15 de \d+", h) is not None)
h25 = s.get(B + "/events?unack=0&per=25").text
check("Alarmas: 25 por página", count(h25) == 25, count(h25))
h100 = s.get(B + "/events?unack=0&per=100").text
check("Alarmas: 50 y 100 por página", count(s.get(B + "/events?unack=0&per=50").text) == 50 and count(h100) >= 40)
check("Alarmas: un tamaño no permitido vuelve a 15", count(s.get(B + "/events?unack=0&per=7").text) == 15)
p2 = s.get(B + "/events?unack=0&per=25&page=2").text
check("Alarmas: la página 2 continúa donde acaba la 1", "Mostrando 26–50" in p2)
check("Alarmas: los enlaces de página conservan el filtro y el tamaño", 'unack=0&amp;per=25&amp;page=3' in p2)
check("Alarmas: al volver a entrar sin el enlace son 15 otra vez", count(s.get(B + "/events?unack=0").text) == 15)

with SessionLocal() as db:
    db.query(Event).filter(Event.category == "Prueba paginador").delete()
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
