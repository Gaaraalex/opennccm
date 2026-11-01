"""Consola nueva (v2), Operar › Consola y asistente IA, con el Cisco simulado (R1 = id 1 en el puerto 2222, de e2e.py)
y un proveedor de IA «compatible con OpenAI» falso que corre en esta misma prueba (puerto 8765)."""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from websockets.sync.client import connect

B = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/devices/1/console/ws?v=2&cols=120&rows=30"
ORIGIN = "http://127.0.0.1:8000"
fails = []
SUF = str(int(time.time()))[-6:]   # usuarios nuevos en cada vuelta: la prueba no depende de las anteriores
OPU, AIU = "opc" + SUF, "ia" + SUF
BASE = {"require_approval": "on", "allow_self_approval": "on", "console_access": "admin", "update_check": "on"}


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def login(user, pw):
    s = requests.Session()
    s.post(B + "/login", data={"username": user, "password": pw})
    return s


def cookie(s):
    return "; ".join(f"{k}={v}" for k, v in s.cookies.items())


def recv_until(ws, pred, timeout=15):
    """Mensajes JSON hasta que pred(lista) sea verdadero."""
    msgs, end = [], time.time() + timeout
    while time.time() < end:
        try:
            msgs.append(json.loads(ws.recv(timeout=max(0.1, end - time.time()))))
        except TimeoutError:
            break
        except Exception:  # noqa: BLE001
            break
        if pred(msgs):
            break
    return msgs


def text(msgs):
    return "".join(m.get("d", "") for m in msgs if m.get("t") in ("o", "n"))


def cmd(ws, c, **kw):
    ws.send(json.dumps({"t": "c", "d": c, **kw}))


adm = login("admin", "Admin12345")
adm.post(B + "/settings", data={**BASE, "console_autologin": "on"})

# ---- la consola nueva
page = adm.get(B + "/devices/1/console").text
check("consola nueva: línea de comando, frecuentes y solo lectura", all(x in page for x in ('id="cx-in"', 'id="cx-freq"', 'id="cx-ro"')))
check("consola: autocompletado de Cisco IOS en la página", "show ip interface brief" in page and "Cisco IOS" in page)
check("consola: tres temas y pie con cierre e Auditoría", "Sala de control" in page and "Alto contraste" in page and "sesión en Auditoría" in page)

H = {"Cookie": cookie(adm), "Origin": ORIGIN}
with connect(WS, additional_headers=H) as ws:
    m = recv_until(ws, lambda ms: re.search(r"R1#\s*$", text(ms)) is not None)
    st = next((x for x in m if x.get("t") == "s"), None)
    check("v2: se conecta y avisa la sesión", st is not None and st.get("sid"), m[:3])
    sid = st["sid"] if st else 0
    check("v2: el paginado se apaga sin mostrarlo", "terminal length" not in text(m), text(m)[-200:])
    cmd(ws, "show version")
    m = recv_until(ws, lambda ms: "FTX1234ABCD" in text(ms))
    check("v2: ejecuta comandos y devuelve la salida", "Cisco IOS Software" in text(m), text(m)[-200:])
    cmd(ws, "reload")
    m = recv_until(ws, lambda ms: any(x.get("t") == "g" for x in ms), 5)
    g = next((x for x in m if x.get("t") == "g"), None)
    check("guarda: «reload» pide confirmación antes de enviarse", g is not None and g["cmd"] == "reload" and g["why"], m)
    cmd(ws, "configure terminal", ro=True)
    m = recv_until(ws, lambda ms: any(x.get("t") == "n" for x in ms), 5)
    check("solo lectura: bloquea un cambio y lo avisa", any(x.get("t") == "n" and "solo lectura" in x["d"] for x in m), m)
    cmd(ws, "configure terminal")
    m = recv_until(ws, lambda ms: any(x.get("t") == "n" and "cambio detectado" in x.get("d", "") for x in ms), 5)
    cmd(ws, "end")
    recv_until(ws, lambda ms: re.search(r"R1#\s*$", text(ms)) is not None, 5)
    check("cambio desde la consola: línea verde con el respaldo en cola", any(x.get("k") == "ok" and "respaldo v" in x["d"] and "se puede revertir" in x["d"] for x in m), m)
    ws.send(json.dumps({"t": "d", "d": "show running-config\n"}))  # por «d» solo pasan teclas sueltas
    m = recv_until(ws, lambda ms: "Building configuration" in text(ms), 3)
    check("v2: por el canal de teclas no pasan órdenes enteras (no se saltan las reglas)", "Building configuration" not in text(m))

    # compartir en solo lectura
    r = adm.post(B + f"/console/sessions/{sid}/share").json()
    check("compartir: enlace de solo lectura", r.get("url", "").startswith("/console/watch/"), r)
    viewer = login("admin", "Admin12345")
    check("compartir: la página del enlace abre la consola en modo mirar", "watching" in viewer.get(B + r["url"]).text)
    with connect("ws://127.0.0.1:8000" + r["url"] + "/ws", additional_headers={"Cookie": cookie(viewer), "Origin": ORIGIN}) as wv:
        first = recv_until(wv, lambda ms: len(ms) >= 1, 5)
        check("quien mira recibe lo que ya se vio", "Cisco IOS Software" in text(first), first[:1])
        m = recv_until(ws, lambda ms: any(x.get("t") == "w" for x in ms), 5)
        check("el dueño ve quién mira", any(x.get("t") == "w" and "admin" in x["who"] for x in m), m)
        cmd(ws, "show clock")
        m = recv_until(wv, lambda ms: "show clock" in text(ms), 5)
        check("quien mira ve en vivo lo que se escribe", "show clock" in text(m), text(m)[-200:])
time.sleep(1)

# ---- grabación
j = adm.get(B + f"/console/rec/{sid}").json()
ins = [e[2] for e in j["events"] if e[1] == "i"]
check("grabación: guarda lo escrito", "show version" in ins and "configure terminal" in ins, ins)
check("grabación: guarda lo visto", any("Cisco IOS Software" in e[2] for e in j["events"] if e[1] == "o"))
exp = adm.get(B + f"/console/rec/{sid}/export").text
check("grabación: se exporta como texto", "show version" in exp and "R1" in exp.splitlines()[0])
center = adm.get(B + "/console?tab=grabaciones&q=configure").text
check("grabaciones: la búsqueda encuentra lo escrito", f'data-rec="{sid}"' in center)
check("grabaciones: la búsqueda filtra", f'data-rec="{sid}"' not in adm.get(B + "/console?tab=grabaciones&q=noexisteesto").text)
audit = adm.get(B + "/audit").text
check("auditoría: compartir y mirar la sesión", "console_share" in audit and "console_watch" in audit)

# ---- centro de consola
check("menú Operar › Consola con «nuevo»", 'href="/console"' in adm.get(B + "/").text and "newb" in adm.get(B + "/").text)
for sec in ("t-sesiones", "t-multi", "t-recetas", "t-grabaciones", "t-herramientas", "t-acceso"):
    check(f"centro de consola: sección {sec[2:]}", f'id="{sec}"' in center)
r = adm.post(B + "/console/multi", json={"device_ids": [1], "command": "show version"}).json()
check("multi-equipo: ejecuta una consulta y agrupa las respuestas", r.get("groups") and r["groups"][0]["devs"] == ["R1"] and r["same"] == 1, r)
r = adm.post(B + "/console/multi", json={"device_ids": [1], "command": "configure terminal"}).json()
check("multi-equipo: un cambio no se ejecuta, propone la solicitud", r.get("change") is True, r)
r = adm.post(B + "/console/change-request", json={"device_ids": [1], "body": "configure terminal\nntp server 10.9.9.9\nend", "title": "NTP"}).json()
check("solicitud de cambio desde la consola (sin las órdenes de entrar y salir de configuración)", r.get("url", "").startswith("/changes/"), r)
if r.get("url"):
    check("la solicitud lleva solo los comandos del cambio", "ntp server 10.9.9.9" in adm.get(B + r["url"]).text)
    adm.post(B + r["url"] + "/review", data={"decision": "reject", "comment": "prueba de la consola"})  # no queda pendiente
r = adm.post(B + "/console/tools", json={"tool": "tcp", "target": "127.0.0.1", "port": 2222}).json()
check("herramientas: puerto TCP abierto", "abierto" in r.get("out", "") and r.get("device", {}).get("name") == "R1", r)
r = adm.post(B + "/console/tools", json={"tool": "dns", "target": "localhost"}).json()
check("herramientas: DNS", "localhost" in r.get("out", ""), r)
r = adm.post(B + "/console/tools", json={"tool": "ping", "target": "-c 1 x"})
check("herramientas: no aceptan opciones como destino", r.status_code == 400)
check("recetas: incluidas, revisadas contra la documentación del fabricante",
      "4145-ssh.html" in center and "manual.mikrotik.com" in center and "documentation.extremenetworks.com" in center)
r = adm.post(B + "/console/recipes", json={"name": "Ver NTP", "vendor": "Cisco", "os": "ios", "cat": "diagnostico",
                                           "vars": [{"n": "srv", "l": "Servidor", "v": "10.0.0.5"}],
                                           "steps": [{"t": "Ver", "c": "show ntp associations"}]}).json()
check("recetas: se guarda una propia", r.get("ok"), r)
check("recetas: la propia aparece como «mía»", '"Ver NTP"' in adm.get(B + "/console").text)
adm.post(B + f"/console/recipes/{r['id']}/delete")

# ---- acceso: operador en solo lectura, grupo con aprobación y comandos prohibidos
adm.post(B + "/users", data={"username": OPU, "role": "operator", "password": "Operador2026x", "password2": "Operador2026x"})
adm.post(B + "/console/access", data={"role_admin": "rw", "role_operator": "ro", "role_viewer": "none", "record": "on", "deny": "on",
                                      "idle": "15", "days": "180", "g1_mode": "approval", "g1_deny": "show running"})
op = login(OPU, "Operador2026x")
pg = op.get(B + "/devices/1/console").text
check("grupo con aprobación: el operador ve «Pedir acceso»", "Pedir acceso" in pg and 'id="cx-in"' not in pg)
with connect(WS, additional_headers={"Cookie": cookie(op), "Origin": ORIGIN}) as ws:
    m = recv_until(ws, lambda ms: len(ms) >= 1, 5)
check("grupo con aprobación: sin aprobar no abre la consola", any("aprobación" in x.get("d", "") for x in m), m)
op.post(B + "/devices/1/console/request", data={"reason": "revisar el enlace", "hours": "1"})
acc = adm.get(B + "/console").text
rid = re.search(r'/console/access/(\d+)/approve', acc)
check("Acceso: el administrador ve la solicitud con su motivo", rid and "revisar el enlace" in acc)
if rid:
    adm.post(B + f"/console/access/{rid.group(1)}/approve")
with connect(WS, additional_headers={"Cookie": cookie(op), "Origin": ORIGIN}) as ws:
    m = recv_until(ws, lambda ms: re.search(r"R1#\s*$", text(ms)) is not None)
    st = next((x for x in m if x.get("t") == "s"), {})
    check("aprobada: el operador entra, en solo lectura por su rol", st.get("mode") == "ro", m[:3])
    cmd(ws, "configure terminal")
    m = recv_until(ws, lambda ms: any(x.get("t") == "n" for x in ms), 5)
    check("rol en solo lectura: el servidor bloquea los cambios aunque el navegador no lo pida", any("solo lectura" in x.get("d", "") for x in m), m)
    cmd(ws, "show running-config")
    m = recv_until(ws, lambda ms: any(x.get("t") == "n" for x in ms), 5)
    check("comando prohibido del grupo: bloqueado", any(x.get("k") == "bad" and "prohibido" in x.get("d", "") for x in m), m)
check("auditoría: comando prohibido y aprobación", "console_denied" in adm.get(B + "/audit").text)
adm.post(B + "/console/access", data={"role_admin": "rw", "role_operator": "none", "role_viewer": "none", "record": "on",
                                      "deny": "on", "idle": "15", "days": "180"})
uid = re.search(r"<b>" + OPU + r"</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
if uid:
    adm.post(f"{B}/users/{uid.group(1)}/delete")

# ---- asistente IA con un proveedor falso «compatible con OpenAI»
SEEN = []


class FakeAI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer sk-prueba":
            return self._send({"error": {"message": "bad key"}}, 401)
        self._send({"data": [{"id": "modelo-falso"}, {"id": "text-embedding-x"}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(data)
        answer = "Para ver las interfaces:\n```\nshow ip interface brief\n```\nY para reiniciar:\n```\nreload\n```"
        self._send({"choices": [{"message": {"content": answer}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30}})


srv = HTTPServer(("127.0.0.1", 8765), FakeAI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
check("asistente desactivado por omisión: sin botón en la barra", "data-ai=\"page\"" not in adm.get(B + "/").text)
AI = {**BASE, "ai_form": "1", "ai_enabled": "on", "ai_provider": "compat", "ai_base_url": "http://127.0.0.1:8765/v1",
      "ai_key": "sk-prueba", "ai_model": "", "ai_role_admin": "on", "ai_daily_limit": "3", "ai_include_backup": "on",
      "ai_include_console": "on", "ai_price_in": "3", "ai_price_out": "15"}
r = adm.post(B + "/settings/ai/models", data=AI).json()
check("Ajustes: carga los modelos de la cuenta (sin los de embeddings)", r.get("models") == ["modelo-falso"], r)
AI["ai_model"] = "modelo-falso"
r = adm.post(B + "/settings/ai/test", data=AI).json()
check("Ajustes: «Probar conexión» con el tiempo de respuesta", r.get("ok") and " ms" in r.get("msg", ""), r)
adm.post(B + "/settings", data=AI)
st = adm.get(B + "/settings").text
check("Ajustes: la clave queda guardada (cifrada, no se muestra)", "sk-prueba" not in st and "clave guardada" in st)
check("Ajustes: vista previa con los secretos ocultos", "******" in st)
check("asistente activo: botón en la barra superior", 'data-ai="page"' in adm.get(B + "/").text)
before = int((re.findall(r'class="ai-big">(\d+)<', st) or ["0"])[0])
adm.post(B + "/users", data={"username": AIU, "role": "admin", "password": "Asistente2026x", "password2": "Asistente2026x"})
ia = login(AIU, "Asistente2026x")
cpage = adm.get(B + "/devices/1/console").text
check("asistente en la consola", 'id="ai-panel"' in cpage)
mt = re.search(r"data-t='([^']*)'", cpage)
try:
    json.loads(__import__("html").unescape(mt.group(1)) if mt else "")
    okt = True
except ValueError:
    okt = False
check("panel del asistente: sus textos llegan enteros al navegador (JSON válido)", okt)
r = ia.post(B + "/api/ai/ask", json={"q": "¿Cómo veo las interfaces?", "device_id": 1, "kind": "console",
                                       "console": ["R1#show run", "snmp-server community public RO"]}).json()
segs = r.get("segments", [])
check("respuesta: comandos con su riesgo", [s.get("risk") for s in segs if s["k"] == "cmd"] == ["read", "outage"], segs)
check("respuesta: fuente = respaldo del equipo", "respaldo de R1" in r.get("source", ""), r)
sent = json.dumps(SEEN[-1]) if SEEN else ""
check("se envía el contexto del equipo y la consola", "R1" in sent and "show run" in sent)
check("nunca salen los secretos: contraseñas y comunidad SNMP ocultas", "$1$abc$xyz" not in sent and "community public" not in sent and "******" in sent)
r = ia.post(B + "/api/ai/ask", json={"q": "Explica", "kind": "diff", "a": 1, "b": 2}).json()
check("«✦ Explicar este cambio» con las diferencias", r.get("segments") and "Diferencias" in json.dumps(SEEN[-1]), r)
check("botón «✦ Explicar este cambio» en la comparación", "Explicar este cambio" in adm.get(B + "/diff?a=1&b=2").text)
ia.post(B + "/api/ai/ask", json={"q": "otra"})
r = ia.post(B + "/api/ai/ask", json={"q": "una más"})
check("límite de preguntas por día", r.status_code == 400 and "límite" in r.json().get("error", ""), r.text)
st = adm.get(B + "/settings").text
after = int((re.findall(r'class="ai-big">(\d+)<', st) or ["0"])[0])
check("uso del mes: preguntas, tokens y costo estimado", after - before == 3 and "US$" in st, (before, after))
check("auditoría: cada pregunta con proveedor y equipo", "ai_ask" in adm.get(B + "/audit").text)
adm.post(B + "/settings", data={**BASE, "ai_form": "1", "ai_provider": "compat"})  # desactivado otra vez
check("asistente desactivado: desaparece el botón", 'data-ai="page"' not in adm.get(B + "/").text)
srv.shutdown()
uid = re.search(r"<b>" + AIU + r"</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
if uid:
    adm.post(f"{B}/users/{uid.group(1)}/delete")

adm.post(B + "/settings", data=BASE)
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
