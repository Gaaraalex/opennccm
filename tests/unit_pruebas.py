"""Pruebas gratis de los módulos Pro (app/pruebas.py y app/licencia.py, contrato «CONTRATO-PRUEBAS.md» v1.1): huella
estable «h_» + SHA-256, tipo de cada módulo (prueba sin gracia, manual y compra con 15 días), 409 prueba_usada al
activar, pruebas_usadas en actualizar y sin licencia, pedir (201, descartados, 409 prueba_usada, pedido_abierto,
licencia_en_otro_servidor, 503), preguntar el estado (en revisión, aprobado, rechazado; 14 días), cancelar, «Activar
prueba» con y sin clave, y el aviso 3 días antes. Contra una web simulada: nunca llama a comunidad.opennccm.com."""
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

tmp = tempfile.mkdtemp()
os.environ.update({"NCM_DATA_DIR": tmp, "NCM_KEY_FILE": os.path.join(tmp, "secret.key"), "NCM_USO_URL": "http://127.0.0.1:9/x"})
PRIV = Ed25519PrivateKey.generate()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
os.environ["NCM_LICENSE_KEYS"] = "k0=" + b64(PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
srv = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)  # solo para el puerto: la web va abajo
PORT = srv.server_address[1]
srv.server_close()
os.environ["NCM_LICENSE_URL"] = f"http://127.0.0.1:{PORT}"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import licencia, pruebas  # noqa: E402
from app.db import Event, SessionLocal, get_setting, init_db, select, set_setting  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


now = datetime.now(timezone.utc)


def lic(mods, **extra):
    data = {"v": 1, "id": "lic_1", "emitida": iso(now), "comprobar_antes": iso(now + timedelta(days=7)), "cliente": {"nombre": "Municipio Norte"},
            "instalacion": licencia.install_id(), "gracia_dias": 15, "modulos": mods, **extra}
    raw = json.dumps(data, separators=(",", ":")).encode()
    return f"OPNC1.k0.{b64(raw)}.{b64(PRIV.sign(raw))}"


# ------------------------------------------------------------------ huella
h = licencia.fingerprint()
mid = licencia._machine_id()
check("huella: «h_» + SHA-256 en hex, siempre la misma", h.startswith("h_") and len(h) == 66 and licencia.fingerprint() == h)
check("…de \"opennccm-huella:\" + el id de la máquina (o el UUID guardado en los datos)",
      h == "h_" + hashlib.sha256(("opennccm-huella:" + (mid or open(os.path.join(tmp, "huella.id")).read().strip())).encode()).hexdigest())
check("…no lleva el id de la máquina a la vista", not mid or mid not in h)

# ------------------------------------------------------------------ tipo de cada módulo
d10 = iso(now - timedelta(days=1))
set_setting("licencia", lic([{"id": "informes_pro", "hasta": d10, "tipo": "prueba"},
                             {"id": "recolectores", "hasta": d10, "tipo": "manual"},
                             {"id": "alta_disponibilidad", "hasta": d10, "tipo": "compra"}]))
set_setting("licencia_validada", now.isoformat())
st = licencia.state()
check("prueba vencida ayer: sin gracia, el módulo queda vencido (pausado) ese día", st["modules"]["informes_pro"]["state"] == "vencido")
check("Pro manual y compra vencidos ayer: 15 días de gracia", st["modules"]["recolectores"]["state"] == "gracia"
      and st["modules"]["alta_disponibilidad"]["state"] == "gracia" and st["modules"]["recolectores"]["tipo"] == "manual")
set_setting("licencia", lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=15)), "tipo": "prueba"}]))
st = licencia.state()
check("prueba activa: quedan 15 días (calendario) y la licencia se ve «de prueba»", st["modules"]["informes_pro"]["days_cal"] == 15
      and st["prueba"] and st["modules"]["informes_pro"]["tipo"] == "prueba")
set_setting("licencia", lic([{"id": "informes_pro", "hasta": d10}], prueba=True))
check("licencias de antes con «prueba»: true a nivel licencia siguen siendo pruebas", licencia.state()["modules"]["informes_pro"]["tipo"] == "prueba")

# aviso 3 días antes (y no el de 30 días de las compradas)
set_setting("licencia", lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=2, hours=1)), "tipo": "prueba"}]))
set_setting("licencia_clave", "")
licencia.refresh = lambda: "sin_clave"  # noqa: E731  (el aviso no depende de la red)
licencia.daily()
licencia.daily()
with SessionLocal() as db:
    ev = [e.message for e in db.scalars(select(Event).where(Event.category == "Licencia"))]
check("aviso en la campana 3 días antes de que venza la prueba, una sola vez", sum("La prueba de Informes profesionales vence" in m for m in ev) == 1
      and not any("Renuévelo" in m for m in ev), ev)
import importlib  # noqa: E402

importlib.reload(licencia)
pruebas.licencia = licencia

# ------------------------------------------------------------------ web simulada
WEB = {"calls": [], "pedir": (201, {"pedido": "pp_1", "secreto": "s3cr3t", "estado": "en_revision"}), "estado": {"estado": "en_revision"},
       "activar": None, "usadas": {"pruebas_usadas": [{"modulo": "recolectores", "fecha": "2026-03-02"}]}, "lic_mods": []}


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        WEB["calls"].append((u.path, parse_qs(u.query), self.headers.get("X-OPNC-Secreto")))
        if u.path == "/api/pruebas/estado":
            return self._send(200, WEB["estado"]) if self.headers.get("X-OPNC-Secreto") == "s3cr3t" else self._send(403, {})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        WEB["calls"].append((self.path, body, self.headers.get("X-OPNC-Secreto")))
        if self.path == "/api/pruebas/pedir":
            return self._send(*WEB["pedir"])
        if self.path == "/api/pruebas/usadas":
            return self._send(200, WEB["usadas"])
        if self.path == "/api/pruebas/cancelar":
            return self._send(204) if self.headers.get("X-OPNC-Secreto") == "s3cr3t" else self._send(403, {})
        if self.path == "/api/licencias/activar":
            if WEB["activar"]:
                return self._send(*WEB["activar"])
            return self._send(200, {"licencia": lic(WEB["lic_mods"]), "sello": "sello-1", "pruebas_usadas": ["informes_pro"]})
        if self.path == "/api/licencias/actualizar":
            return self._send(200, {"licencia": lic(WEB["lic_mods"]), "sello": "sello-2",
                                    "pruebas_usadas": {"informes_pro": "2026-11-15", "alta_disponibilidad": "2026-11-15"}})
        self._send(404, {})


web = ThreadingHTTPServer(("127.0.0.1", PORT), Web)
threading.Thread(target=web.serve_forever, daemon=True).start()
licencia.clear()
set_setting("licencia_pruebas_usadas", "")

# ------------------------------------------------------------------ pruebas ya usadas
licencia.fetch_used()
check("sin licencia: POST /api/pruebas/usadas con la huella", WEB["calls"][-1][0] == "/api/pruebas/usadas"
      and WEB["calls"][-1][1] == {"huella": licencia.fingerprint()})
check("…y queda qué módulo ya probó este servidor, con su fecha", licencia.used_trials() == {"recolectores": "2026-03-02"})

# ------------------------------------------------------------------ pedir
try:
    pruebas.ask("redes@acme.com", "ACME", [], "x")
    ok = False
except pruebas.TrialError as e:
    ok = "al menos un módulo" in str(e)
check("pedir sin módulos: lo dice", ok)
p = pruebas.ask("compras@municipionorte.gob.do", "Municipio Norte", ["informes_pro", "alta_disponibilidad"], "Evaluar para el concejo")
sent = WEB["calls"][-1][1]
check("pedir: envía exactamente lo del contrato, con origen «sistema», huella e instalación",
      set(sent) == {"correo", "organizacion", "modulos", "motivo", "origen", "huella", "instalacion", "version"}
      and sent["origen"] == "sistema" and sent["huella"] == licencia.fingerprint() and sent["instalacion"] == licencia.install_id(), sent)
check("…guarda el pedido y su secreto, cifrado en la base", pruebas.current()["pedido"] == "pp_1" and pruebas.current()["secreto"] == "s3cr3t"
      and "s3cr3t" not in str(SessionLocal().execute(select(1)).all()) and "licencia_pedido" in __import__("app.db", fromlist=["x"]).SECRET_SETTINGS)
try:
    pruebas.ask("x@y.com", "X", ["informes_pro"], "")
    ok = False
except pruebas.TrialError as e:
    ok = "en curso" in str(e)
check("con un pedido en curso no se pide otro", ok)

# estado
pruebas.poll()
check("en revisión: pregunta con la cabecera X-OPNC-Secreto", WEB["calls"][-1][0] == "/api/pruebas/estado"
      and WEB["calls"][-1][1] == {"pedido": ["pp_1"]} and WEB["calls"][-1][2] == "s3cr3t" and pruebas.current()["estado"] == "en_revision")
p = pruebas.current()
p["enviado"] = iso(now - timedelta(days=15))
set_setting("licencia_pedido", json.dumps(p))
n = len(WEB["calls"])
pruebas.poll()
check("a los 14 días deja de preguntar", len(WEB["calls"]) == n)
p["enviado"] = iso(now)
set_setting("licencia_pedido", json.dumps(p))

# cancelar
pruebas.cancel()
check("cancelar: POST /api/pruebas/cancelar con el secreto y se olvida el pedido", WEB["calls"][-1][0] == "/api/pruebas/cancelar"
      and WEB["calls"][-1][1] == {"pedido": "pp_1"} and WEB["calls"][-1][2] == "s3cr3t" and pruebas.current() is None)

# errores del pedido
for code, body, frag in ((409, {"error": "prueba_usada", "modulos": ["informes_pro"], "fecha": "2026-03-02"},
                          "Este servidor ya usó una prueba de Informes profesionales (2 mar 2026)"),
                         (409, {"error": "pedido_abierto"}, "Ya hay un pedido de prueba en revisión"),
                         (409, {"error": "licencia_en_otro_servidor"}, "Tu equipo ya usa su licencia en otro servidor"),
                         (503, {"error": "pruebas_cerradas"}, "no se aceptan pedidos de prueba"),
                         (429, {}, "Demasiados pedidos")):
    WEB["pedir"] = (code, body)
    try:
        pruebas.ask("a@b.com", "B", ["informes_pro"], "")
        msg = ""
    except pruebas.TrialError as e:
        msg = str(e)
    check(f"pedir: {code} {body.get('error', '')} se explica en palabras", frag in msg and pruebas.current() is None, msg)
check("…y lo ya probado queda anotado", "informes_pro" in licencia.used_trials())
WEB["pedir"] = (201, {"pedido": "pp_2", "secreto": "s3cr3t", "estado": "en_revision", "descartados": ["recolectores"]})
p = pruebas.ask("a@b.com", "B", ["alta_disponibilidad", "recolectores"], "")
check("pedido con un módulo ya probado: sigue con los demás («descartados»)", p["modulos"] == ["alta_disponibilidad"] and p["descartados"] == ["recolectores"])

# rechazado
WEB["estado"] = {"estado": "rechazado", "motivo": "Falta el nombre de la organización"}
pruebas.poll()
check("rechazado: guarda el motivo", pruebas.current()["estado"] == "rechazado" and "Falta el nombre" in pruebas.current()["motivo"])
pruebas.dismiss()
check("…y el recuadro se cierra", pruebas.current() is None)

# aprobado sin clave en este servidor: activa la clave de la prueba
WEB["pedir"] = (201, {"pedido": "pp_3", "secreto": "s3cr3t", "estado": "en_revision"})
pruebas.ask("a@b.com", "B", ["alta_disponibilidad"], "")
WEB["estado"] = {"estado": "aprobado", "clave": "OPNC-4M8R-AAAA-BBBB-H6QE", "dias": 15, "vence": iso(now + timedelta(days=15))}
pruebas.poll()
check("aprobado: guarda la clave y los días, y avisa en la campana", pruebas.current()["estado"] == "aprobado"
      and pruebas.current()["clave"] == "OPNC-4M8R-AAAA-BBBB-H6QE")
WEB["lic_mods"] = [{"id": "alta_disponibilidad", "hasta": iso(now + timedelta(days=15)), "tipo": "prueba"}]
pruebas.activate()
check("«Activar prueba» sin clave: activa la clave de la prueba con la huella", WEB["calls"][-1][0] == "/api/licencias/activar"
      and WEB["calls"][-1][1]["clave"] == "OPNC-4M8R-AAAA-BBBB-H6QE" and WEB["calls"][-1][1]["huella"] == licencia.fingerprint()
      and licencia.state()["modules"]["alta_disponibilidad"]["state"] == "activo" and pruebas.current() is None)
check("…y activar trae pruebas_usadas", "informes_pro" in licencia.used_trials())

# aprobado con clave: «Activar prueba» es un actualizar
WEB["pedir"] = (201, {"pedido": "pp_4", "secreto": "s3cr3t", "estado": "en_revision"})
pruebas.ask("a@b.com", "B", ["informes_pro"], "")
WEB["estado"] = {"estado": "aprobado", "dias": 15}
pruebas.poll()
WEB["lic_mods"] += [{"id": "informes_pro", "hasta": iso(now + timedelta(days=15)), "tipo": "prueba"}]
pruebas.activate()
check("«Activar prueba» con clave: es un actualizar inmediato que trae el módulo nuevo", WEB["calls"][-1][0] == "/api/licencias/actualizar"
      and licencia.state()["modules"]["informes_pro"]["state"] == "activo" and pruebas.current() is None)
check("…actualizar guarda pruebas_usadas", licencia.used_trials().get("alta_disponibilidad") == "2026-11-15")

# 409 prueba_usada al pegar una clave de prueba
licencia.clear()
WEB["activar"] = (409, {"error": "prueba_usada", "modulos": ["informes_pro"], "fecha": "2026-03-02"})
try:
    licencia.activate("OPNC-9TQZ-12WA-3RMB-K8PD")
    msg = ""
except licencia.TrialUsed as e:
    msg = str(e)
check("409 prueba_usada al activar: el texto de la sección 3 y no se activa nada",
      msg == "Este servidor ya usó una prueba de Informes profesionales (2 mar 2026). Cada servidor puede probar un módulo una sola vez. "
             "Para seguir usándolo, compra la licencia." and not get_setting("licencia_clave"), msg)
check("…y la pantalla lo puede mostrar con la clave", json.loads(get_setting("licencia_prueba_usada"))["clave"] == "OPNC-9TQZ-12WA-3RMB-K8PD")
WEB["activar"] = (409, {"error": "actualiza_el_sistema"})
try:
    licencia.activate("OPNC-9TQZ-12WA-3RMB-K8PD")
    msg = ""
except licencia.LicenseError as e:
    msg = str(e)
check("409 actualiza_el_sistema: lo dice", "2.45" in msg, msg)
web.shutdown()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
