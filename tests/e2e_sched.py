"""Asistente de programación de tareas (sin escribir cron) y días de la semana correctos.
Requiere haber ejecutado antes e2e.py (usuario admin)."""
import html as htmlmod
import re
import sys
from datetime import datetime

import requests

B = "http://127.0.0.1:8000"
s = requests.Session()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def row(page, name):
    m = re.search(rf"</span>{re.escape(name)}</div></td>([\s\S]*?)</tr>", page)
    return htmlmod.unescape(m.group(1)) if m else ""


s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
page = s.get(B + "/schedules/new").text
check("formulario con frecuencia, hora y días", 'name="freq"' in page and 'type="time"' in page
      and 'name="days" value="mon"' in page)

r = s.post(B + "/schedules", data=[("name", "Semanal LJ"), ("action", "backup"), ("freq", "weekly"), ("time", "02:30"),
                                   ("days", "mon"), ("days", "thu")])
check("tarea semanal creada con descripción", "Cada lunes y jueves a las 02:30" in htmlmod.unescape(r.text))
cells = row(r.text, "Semanal LJ")
check("la tabla muestra «Cada lunes y jueves a las 02:30»", "Cada lunes y jueves a las 02:30" in cells, cells)
nxt = re.search(r"(\d{4}-\d{2}-\d{2}) 02:30", cells)
check("la próxima ejecución cae en lunes o jueves",
      nxt is not None and datetime.strptime(nxt.group(1), "%Y-%m-%d").weekday() in (0, 3), cells)

r = s.post(B + "/schedules", data={"name": "Mensual", "action": "compliance", "freq": "monthly", "time": "06:00",
                                   "dom": "15"})
check("tarea mensual", "El día 15 de cada mes a las 06:00" in row(r.text, "Mensual"))
r = s.post(B + "/schedules", data={"name": "Frecuente", "action": "backup", "freq": "minutes", "every": "15"})
check("cada 15 minutos", "Cada 15 minutos" in row(r.text, "Frecuente"))
r = s.post(B + "/schedules", data={"name": "Sin días", "action": "backup", "freq": "weekly", "time": "01:00"})
check("semanal sin días se rechaza", "elija al menos un día" in htmlmod.unescape(r.text))
r = s.post(B + "/schedules", data={"name": "Cron lunes", "action": "backup", "cron": "0 6 * * 1"})
cells = row(r.text, "Cron lunes")
nxt = re.search(r"(\d{4}-\d{2}-\d{2}) 06:00", cells)
check("cron «0 6 * * 1» se ejecuta el LUNES (antes caía en martes)",
      nxt is not None and datetime.strptime(nxt.group(1), "%Y-%m-%d").weekday() == 0, cells)

# ---- editar una tarea existente
page = s.get(B + "/schedules").text
sid = re.search(r"</span>Semanal LJ</div></td>[\s\S]*?/schedules/(\d+)/edit", page)
check("botón «Editar» en cada tarea", sid is not None)
if sid:
    form = s.get(f"{B}/schedules/{sid.group(1)}/edit").text
    check("la edición abre el asistente con los valores actuales",
          'value="weekly" selected' in form and 'value="02:30"' in form
          and 'value="mon" checked' in form and 'value="thu" checked' in form)
    r = s.post(f"{B}/schedules/{sid.group(1)}/edit",
               data=[("name", "Semanal LJ"), ("action", "compliance"), ("freq", "weekly"), ("time", "07:15"),
                     ("days", "tue"), ("days", "fri")])
    cells = row(r.text, "Semanal LJ")
    check("editar cambia días, hora y acción", "Cada martes y viernes a las 07:15" in cells
          and "Auditoría de cumplimiento" in cells, cells)
    r = s.post(f"{B}/schedules/{sid.group(1)}/edit", data={"name": "Semanal LJ", "action": "backup",
                                                            "freq": "weekly", "time": "07:15"})
    check("editar valida igual que crear (sin días se rechaza)", "elija al menos un día" in htmlmod.unescape(r.text))
cid = re.search(r"</span>Cron lunes</div></td>[\s\S]*?/schedules/(\d+)/edit", s.get(B + "/schedules").text)
if cid:
    form = s.get(f"{B}/schedules/{cid.group(1)}/edit").text
    check("una expresión cron numérica se abre como «días de la semana»",
          'value="weekly" selected' in form and 'value="mon" checked' in form)

for name in ("Semanal LJ", "Mensual", "Frecuente", "Cron lunes"):
    m = re.search(rf"</span>{re.escape(name)}</div></td>[\s\S]*?/schedules/(\d+)/edit", s.get(B + "/schedules").text)
    if m:
        s.post(f"{B}/schedules/{m.group(1)}/delete")

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
