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
    m0 = recv_until(ws, lambda ms: "(config)#" in text(ms), 5)
    check("en modo configuración todavía no se avisa del cambio", not any("cambio detectado" in x.get("d", "") for x in m0), m0)
    cmd(ws, "end")
    m = recv_until(ws, lambda ms: any(x.get("t") == "n" and "cambio detectado" in x.get("d", "") for x in ms), 5)
    check("cambio desde la consola: línea verde con el respaldo en cola", any(x.get("k") == "ok" and "respaldo v" in x["d"] and "se puede revertir" in x["d"] for x in m), m)
    i_note = next((i for i, x in enumerate(m) if x.get("t") == "n" and "cambio detectado" in x.get("d", "")), -1)
    i_cmd = next((i for i, x in enumerate(m) if x.get("t") == "o" and "end" in x.get("d", "")), 99)
    check("el aviso verde sale después del comando, nunca antes", i_cmd < i_note, [(x.get("t"), x.get("d", "")[:30]) for x in m])
    cmd(ws, "show ip interface brief")
    m2 = recv_until(ws, lambda ms: "administratively down" in text(ms), 5)
    check("el equipo simulado responde como uno real (para ver los colores)", "GigabitEthernet0/2" in text(m2))
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
with connect(WS, additional_headers=H) as ws:  # una sesión vacía: se abre y se cierra sin escribir
    m = recv_until(ws, lambda ms: any(x.get("t") == "s" for x in ms))
    empty_sid = next((x["sid"] for x in m if x.get("t") == "s"), 0)
time.sleep(1)
check("grabaciones: las sesiones vacías no se muestran por omisión", f'data-rec="{empty_sid}"' not in adm.get(B + "/console").text)
check("grabaciones: «Mostrar sesiones vacías» las muestra", f'data-rec="{empty_sid}"' in adm.get(B + "/console?empty=1").text)
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
def tool(sess, **kw):
    """La salida llega por partes; tras «\x1e», el resumen en JSON."""
    r = sess.post(B + "/console/tools", json=kw)
    if "json" in r.headers.get("content-type", ""):
        return {"status": r.status_code, **r.json()}
    out, _sep, tail = r.text.partition("\x1e")
    return {"status": r.status_code, "out": out, **(json.loads(tail) if tail else {})}


r = tool(adm, tool="tcp", target="127.0.0.1", port=2222)
check("herramientas: puerto TCP abierto", "abierto" in r.get("out", "") and (r.get("device") or {}).get("name") == "R1", r)
r = tool(adm, tool="tcp", target="127.0.0.1:2222 -c 2")
check("herramientas con parámetros: «host:puerto -c 2» prueba dos veces", r.get("out", "").count("abierto") == 2
      and ["Intentos", "2/2"] in r.get("summary", []), r)
r = tool(adm, tool="dns", target="localhost")
check("herramientas: DNS", "localhost" in r.get("out", ""), r)
r = tool(adm, tool="ping", target="-c 1")
check("herramientas: sin destino (solo opciones) no se ejecuta", r["status"] == 400)
r = tool(adm, tool="ping", target="127.0.0.1; reboot")
check("herramientas: no se puede encadenar órdenes", r["status"] == 400, r)
r = tool(adm, tool="ping", target="127.0.0.1 --flood")
check("herramientas: opción no permitida, con el motivo", r["status"] == 400 and "--flood" in r.get("error", ""), r)
check("recetas: incluidas, revisadas contra la documentación del fabricante",
      "4145-ssh.html" in center and "manual.mikrotik.com" in center and "documentation.extremenetworks.com" in center)
r = adm.post(B + "/console/recipes", json={"name": "Ver NTP", "vendor": "Cisco", "os": "ios", "cat": "diagnostico",
                                           "vars": [{"n": "srv", "l": "Servidor", "v": "10.0.0.5"}],
                                           "steps": [{"t": "Ver", "c": "show ntp associations"}]}).json()
check("recetas: se guarda una propia", r.get("ok"), r)
check("recetas: la propia aparece como «mía»", '"Ver NTP"' in adm.get(B + "/console").text)
adm.post(B + f"/console/recipes/{r['id']}/delete")
check("recetas: las VOSS probadas llevan el sello con la versión", "VOSS 9.3.0.0" in center and "Revisa cada receta antes de usarla" in center)
r = adm.post(B + "/console/recipes/verify", json={"key": "ios-ntp", "verified_on": "IOS 15.2"}).json()
check("recetas: el administrador marca una como verificada", r.get("ok") and "IOS 15.2" in adm.get(B + "/console").text, r)
adm.post(B + "/console/recipes/verify", json={"key": "ios-ntp", "verified_on": ""})
check("recetas: y le quita el sello", "IOS 15.2" not in adm.get(B + "/console").text)
check("recetas: el cambio del sello queda en Auditoría", "recipe_verified" in adm.get(B + "/audit").text)

# ---- configlets: tres columnas, aviso, sello y la vista previa de «Ejecutar» sin código suelto
cf = adm.get(B + "/configlets").text
check("configlets: columnas y el aviso completo (foro, GitHub y casilla que queda en Auditoría)", 'id="cfl-data"' in cf and "Revisa los configlets antes de usarlos" in cf and 'class="wl-full"' in cf and "Foro de la comunidad" in cf and "data-warn-log" in cf)
cdata = json.loads(re.search(r'<script type="application/json" id="cfl-data">(.*?)</script>', cf, re.S).group(1))
cid = cdata["items"][0]["id"]
vid = next((c["id"] for c in cdata["items"] if "{{" in c["body"] and c["kind"] != "exec"), cid)
run = adm.get(f"{B}/configlets/{vid}/run").text
check("ejecutar configlet: la vista previa ya no muestra código JavaScript", "x.dataset.var +" not in run and 'data-j="' in run, run[-600:])
check("ejecutar configlet: aviso completo y casilla obligatoria antes de «Crear solicitud»",
      "Revisa los comandos antes de usarlos en producción" in run and "Foro de la comunidad" in run and "data-ack" in run
      and "data-needs-ack disabled" in run)
r = adm.post(B + "/warn/ack", json={"title": "Revisa los comandos antes de usarlos en producción.", "where": "R1", "page": "/configlets/1/run"})
check("marcar la casilla del aviso queda en Auditoría (quién, qué aviso y dónde)", r.json().get("ok")
      and "warn_ack" in adm.get(B + "/audit").text and "en R1" in adm.get(B + "/audit").text)
r = adm.post(f"{B}/configlets/{cid}/verify", json={"verified_on": "IOS 15.2(7)E"}).json()
check("configlets: el administrador pone el sello", r.get("ok") and "IOS 15.2(7)E" in adm.get(B + "/configlets").text, r)
adm.post(f"{B}/configlets/{cid}/verify", json={"verified_on": ""})
check("configlets: y lo quita", "IOS 15.2(7)E" not in adm.get(B + "/configlets").text)
check("configlets: el sello queda en Auditoría", "configlet_verified" in adm.get(B + "/audit").text)
dup = adm.get(f"{B}/configlets/new?from={cid}").text
check("configlets: «Duplicar» abre uno nuevo con los datos y «(copia)»", "(copia)" in dup and "Nuevo configlet" in dup and 'data-dialog="cfl-del"' not in dup)

# ---- acceso: operador en solo lectura, grupo con aprobación y comandos prohibidos
adm.post(B + "/users", data={"username": OPU, "role": "operator", "password": "Operador2026x", "password2": "Operador2026x"})
adm.post(B + "/console/access", data={"role_admin": "rw", "role_operator": "ro", "role_viewer": "none", "record": "on", "deny": "on",
                                      "idle": "15", "days": "180", "g1_mode": "approval", "g1_deny": "show running"})
op = login(OPU, "Operador2026x")
check("recetas: un operador no puede poner el sello",
      op.post(B + "/console/recipes/verify", json={"key": "ios-ntp", "verified_on": "x"}).status_code == 403)
check("configlets: un operador no puede poner el sello",
      op.post(f"{B}/configlets/{cid}/verify", json={"verified_on": "x"}).status_code == 403)
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
        self._send({"data": [{"id": "modelo-falso"}, {"id": "modelo-grande"}, {"id": "text-embedding-x"}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(data)
        if data.get("tools") and "documenta" in json.dumps(data["messages"][-1]):  # configlet y caso de la KB
            args = {"texto": "Te dejo el configlet y el caso.", "fuente": "Pedido",
                    "acciones": [{"tipo": "configlet", "etiqueta": "Crear el configlet", "nombre": "VLAN de invitados",
                                  "descripcion": "Agrega una VLAN", "tipo_configlet": "config", "tipos_de_equipo": "cisco_ios,no_existe",
                                  "comandos": "vlan {{ id }}\n name {{ nombre }}"},
                                 {"tipo": "caso_kb", "etiqueta": "Crear el caso", "equipos": ["R1"],
                                  "caso": {"titulo": "VLAN de invitados en R1", "categoria": "cambios",
                                           "sintoma": "Faltaba la VLAN", "causa": "No estaba creada",
                                           "pasos": [{"texto": "Crear la VLAN", "comando": "vlan 30"}],
                                           "verificar": "show vlan brief", "prevenir": "Usar el configlet"}}]}
            return self._send({"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c3", "type": "function", "function": {"name": "responder", "arguments": json.dumps(args)}}]}}],
                "usage": {"prompt_tokens": 60, "completion_tokens": 30}})
        if data.get("tools"):  # chat de la barra superior: primero consulta, después responde con «responder»
            if not any(m.get("role") == "tool" for m in data["messages"]):
                return self._send({"choices": [{"message": {"content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "equipos", "arguments": json.dumps({"estado": "todos"})}}]}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 5}})
            res = json.loads(next(m for m in data["messages"] if m.get("role") == "tool")["content"])
            names = [e["nombre"] for e in res.get("equipos", [])]
            args = {"texto": f"Tienes {len(names)} equipos.", "fuente": "Equipos",
                    "equipos": [{"nombre": n, "estado": "ok", "motivo": "respaldo al día"} for n in names[:3]]
                    + [{"nombre": "EQUIPO-INVENTADO", "estado": "error", "motivo": "no existe"}],
                    "acciones": [{"tipo": "ir", "etiqueta": "Ver en Dispositivos", "url": "/devices"},
                                 {"tipo": "respaldar", "etiqueta": "Respaldar de nuevo"},
                                 {"tipo": "ir", "etiqueta": "Sitio externo", "url": "https://malo.example/x"},
                                 {"tipo": "tarea", "etiqueta": "Crear la tarea", "accion_tarea": "backup", "cron": "0 2 * * *",
                                  "nombre_tarea": "Respaldo diario"}]}
            return self._send({"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "responder", "arguments": json.dumps(args)}}]}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 40}})
        answer = "Para ver las interfaces:\n```\nshow ip interface brief\n```\nY para reiniciar:\n```\nreload\n```"
        self._send({"choices": [{"message": {"content": answer}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30}})


srv = HTTPServer(("127.0.0.1", 8765), FakeAI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
check("asistente desactivado por omisión: sin botón en la barra", "data-chat" not in adm.get(B + "/").text)
AI = {**BASE, "ai_form": "1", "ai_enabled": "on", "ai_provider": "compat", "ai_base_url": "http://127.0.0.1:8765/v1",
      "ai_key": "sk-prueba", "ai_model": "", "ai_role_admin": "on", "ai_daily_limit": "3", "ai_include_backup": "on",
      "ai_include_console": "on", "ai_price_in": "3", "ai_price_out": "15"}
r = adm.post(B + "/settings/ai/models", data=AI).json()
check("Ajustes: carga los modelos de la cuenta (sin los de embeddings)", r.get("models") == ["modelo-falso", "modelo-grande"], r)
AI["ai_model"] = "modelo-falso"
r = adm.post(B + "/settings/ai/test", data=AI).json()
check("Ajustes: «Probar conexión» con el tiempo de respuesta", r.get("ok") and " ms" in r.get("msg", ""), r)
adm.post(B + "/settings", data=AI)
st = adm.get(B + "/settings").text
check("Ajustes: la clave queda guardada (cifrada, no se muestra)", "sk-prueba" not in st and "clave guardada" in st)
check("Ajustes: vista previa con los secretos ocultos", "******" in st)
check("Ajustes: el modelo se elige en un desplegable con los de la cuenta",
      '<select id="ai-model" name="ai_model">' in st and '"modelo-grande"' in st)
check("asistente activo: botón en la barra superior", 'data-chat' in adm.get(B + "/").text)
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
r = tool(adm, tool="ping", target="127.0.0.1 -n 2")
check("herramientas: ping con parámetros devuelve su salida o explica cómo arreglarlo", r.get("out"), r)
ia.post(B + "/api/ai/ask", json={"q": "otra"})
r = ia.post(B + "/api/ai/ask", json={"q": "una más"})
check("límite de preguntas por día", r.status_code == 400 and "límite" in r.json().get("error", ""), r.text)
AI["ai_daily_limit"] = "100"
adm.post(B + "/settings", data=AI)
st = adm.get(B + "/settings").text
after = int((re.findall(r'class="ai-big">(\d+)<', st) or ["0"])[0])
check("uso del mes: preguntas, tokens y costo estimado", after - before == 3 and "US$" in st, (before, after))
check("auditoría: cada pregunta con proveedor y equipo", "ai_ask" in adm.get(B + "/audit").text)
sysmsg = json.dumps(SEEN[-1]["messages"][0]) if SEEN else ""
check("solo temas de red y de OpenNCCM: la regla va en las instrucciones", "Solo ayudas con redes" in sysmsg, sysmsg[-300:])
info = ia.get(B + "/api/ai/info?device_id=1").json()
check("consola: lista de modelos para elegir", info.get("models") == ["modelo-falso", "modelo-grande"] and info.get("model") == "modelo-falso", info)
r = ia.post(B + "/api/ai/model", json={"model": "modelo-grande"}).json()
ia.post(B + "/api/ai/ask", json={"q": "¿Y las rutas?", "device_id": 1})
check("cambiar de modelo en el chat: se usa en la pregunta siguiente", r.get("model") == "modelo-grande" and SEEN[-1].get("model") == "modelo-grande", r)
check("cambiar de modelo: el de los demás usuarios sigue igual", adm.get(B + "/api/ai/info").json().get("model") == "modelo-falso")
r = ia.post(B + "/api/ai/model", json={"model": "otro-que-no-existe"})
check("cambiar de modelo: solo uno de la cuenta", r.status_code == 400, r.text)
ia.post(B + "/api/ai/model", json={"model": "modelo-falso"})
check("Operar › Consola: cada sesión tiene su botón «Asistente»", '"ai": true' in adm.get(B + "/console").text
      and 'data-pm="ai"' in adm.get(B + "/static/center.js").text)
cjs = adm.get(B + "/static/center.js").text
check("Operar › Consola: los mismos tamaños que la consola de la ficha (letra, ampliar, ventana aparte, pantalla completa)",
      all(x in cjs for x in ('data-pm="fs-"', 'data-pm="fs+"', "data-big=", "data-win=", "data-full=")))
cc = json.loads(re.search(r'<script type="application/json" id="cc-data">(.*?)</script>', adm.get(B + "/console").text, re.S).group(1))
check("Operar › Consola: el botón «Asistente» lleva el logo del proveedor", "ai-tile" in cc.get("ailogo", ""), cc.get("ailogo"))
check("asistente: la cabecera se oculta desde ella misma («Ocultar»)",
      json.loads(__import__("html").unescape(mt.group(1))).get("hide") == "Ocultar" and "data-headtog" in adm.get(B + "/static/assistant.js").text)
r = ia.post(B + "/api/ai/chat", json={"q": "documenta esto y crea un configlet", "path": "/kb", "title": "Base"}).json()
acts = {a["kind"]: a for a in r.get("actions", [])}
check("chat: prepara un configlet y un borrador de caso para confirmar", set(acts) >= {"configlet", "kb"}
      and "body" not in acts.get("configlet", {}), r)
j = ia.post(B + "/api/ai/chat/act", json={"id": acts.get("configlet", {}).get("id", "")}).json()
cl = adm.get(B + "/configlets").text
check("chat: al confirmar crea el configlet (sin tipos de equipo inventados)", j.get("ok") and "VLAN de invitados" in cl
      and "/configlets/" in j.get("url", ""), j)
run = adm.get(B + j.get("url", "/configlets").replace("/edit", "/run")).text if j.get("ok") else ""
check("chat: el configlet aplica a sus tipos de equipo (expresión regular, no una lista con comas): R1 es compatible",
      "^(cisco_ios)$" in adm.get(B + j.get("url", "/configlets")).text and "R1" in run and "No hay equipos compatibles" not in run)
j = ia.post(B + "/api/ai/chat/act", json={"id": acts.get("kb", {}).get("id", "")}).json()
ed = adm.get(B + j.get("url", "/kb")).text if j.get("ok") else ""
check("chat: al confirmar deja el caso como borrador en la Base de conocimiento", j.get("ok") and "/edit" in j.get("url", "")
      and "VLAN de invitados en R1" in ed, j)
# ---- chat de la barra superior: funciones de solo lectura, respuesta simple y acciones con confirmación
r = ia.post(B + "/api/ai/chat", json={"q": "¿Qué equipos tengo?", "path": "/", "title": "Panel"}).json()
check("chat: consulta con una función y responde con texto y equipos", "equipos" in r.get("text", "") and r.get("items"), r)
kinds = [a["kind"] for a in r.get("actions", [])]
check("chat: «ir» solo a páginas de OpenNCCM (el enlace externo se descarta)", kinds[:1] == ["go"] and "https://malo.example/x" not in json.dumps(r), r)
check("chat: acciones que cambian algo quedan para confirmar", "backup" in kinds and all(a.get("id") for a in r["actions"] if a["kind"] != "go"), r)
check("chat: fuente en una línea", r.get("source") == "Equipos")
check("chat: solo equipos que existen en el inventario (los inventados no se muestran)",
      r.get("items") and all(i["name"] != "EQUIPO-INVENTADO" for i in r["items"]), r.get("items"))
bk0 = next((a for a in r.get("actions", []) if a["kind"] == "backup"), {})
check("chat: la confirmación lista exactamente los equipos de la respuesta, con su número",
      bk0.get("summary") == ", ".join(i["name"] for i in r.get("items", [])) and str(len(r.get("items", []))) in bk0.get("title", "")
      or (len(r.get("items", [])) == 1 and bk0.get("title") == "Respaldar 1 equipo"), bk0)
bk = next((a for a in r.get("actions", []) if a["kind"] == "backup"), None)
if bk:
    j = ia.post(B + "/api/ai/chat/act", json={"id": bk["id"]}).json()
    check("chat: al confirmar, se hace con los permisos del usuario", j.get("ok"), j)
    j = ia.post(B + "/api/ai/chat/act", json={"id": bk["id"]}).json()
    check("chat: una acción no se repite", j.get("error"), j)
g = ia.get(B + "/api/ai/chat?path=/").json()
check("chat: la conversación se guarda y se retoma", len(g.get("messages", [])) >= 2 and g.get("suggest"), g)
ia.post(B + "/api/ai/chat/new")
check("chat: «+» empieza otra conversación", ia.get(B + "/api/ai/chat").json().get("messages") == [])
check("chat: auditoría con las funciones usadas", "funciones: equipos" in adm.get(B + "/audit").text)
check("chat: botón en la barra superior", "data-chat" in ia.get(B + "/").text and "/static/chat.js" in ia.get(B + "/").text)
adm.post(B + "/settings", data={**BASE, "ai_form": "1", "ai_provider": "compat"})  # desactivado otra vez
st = adm.get(B + "/settings").text
check("asistente: sin ningún rol marcado quedan administradores y operadores",
      'name="ai_role_admin" checked' in st and 'name="ai_role_operator" checked' in st and 'name="ai_role_viewer" checked' not in st)
check("asistente desactivado: desaparece el botón", 'data-chat' not in adm.get(B + "/").text)
srv.shutdown()
uid = re.search(r"<b>" + AIU + r"</b>[\s\S]*?/users/(\d+)/edit", adm.get(B + "/users").text)
if uid:
    adm.post(f"{B}/users/{uid.group(1)}/delete")

adm.post(B + "/settings", data=BASE)
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
