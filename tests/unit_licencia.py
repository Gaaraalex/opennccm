"""Licencia de los módulos Pro (app/licencia.py) según el contrato v1 con comunidad.opennccm.com: firma Ed25519 con
un par de claves de prueba hecho aquí, id de instalación, estados (activo, gracia, vencido, no incluido), otra
instalación, archivo para servidores sin internet, y activar/actualizar contra un servidor de licencias simulado
(404, 409, 410, 429). Sin internet: nunca llama a comunidad.opennccm.com."""
import base64
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
PRIV = Ed25519PrivateKey.generate()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
PUB = b64(PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
os.environ["NCM_LICENSE_KEYS"] = f"k0={PUB}"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import licencia  # noqa: E402
from app.db import Event, SessionLocal, get_setting, init_db, select  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def lic(modules, inst="mine", key=PRIV, kid="k0", prueba=False):
    data = {"v": 1, "id": "lic_1", "emitida": "2026-11-14T19:00:00Z", "cliente": {"equipo_id": "eq_1", "nombre": "Valnet S.A."},
            "instalacion": licencia.install_id() if inst == "mine" else inst, "prueba": prueba, "gracia_dias": 15,
            "modulos": modules}
    raw = json.dumps(data, separators=(",", ":")).encode()
    return f"OPNC1.{kid}.{b64(raw)}.{b64(key.sign(raw))}"


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


now = datetime.now(timezone.utc)
iid = licencia.install_id()
check("id de instalación inst_ + UUID, siempre el mismo", iid.startswith("inst_") and len(iid) == 37 and licencia.install_id() == iid)
check("clave: normaliza y enmascara", licencia.normalize_key("opnc 7f3k-abcd efgh q2km") == "OPNC-7F3K-ABCD-EFGH-Q2KM"
      and licencia.mask_key("OPNC-7F3K-ABCD-EFGH-Q2LM") == "OPNC-7F3K-····-····-Q2LM")
check("clave: base32 Crockford (O→0, I y L→1)", licencia.normalize_key("OPNC-OOOO-IIII-LLLL-AAAA") == "OPNC-0000-1111-1111-AAAA")

st = licencia.state()
check("sin licencia: todo «no incluido», sin problema", all(m["state"] == "no_incluido" for m in st["modules"].values())
      and not st["problem"] and set(st["modules"]) == {"informes_pro", "recolectores", "alta_disponibilidad"})

tok = lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=100)), "limites": {"equipos": 500}},
           {"id": "recolectores", "hasta": iso(now - timedelta(days=5)), "limites": {"sedes": 5}},
           {"id": "alta_disponibilidad", "hasta": iso(now - timedelta(days=20)), "limites": {"pares": 1}}])
data = licencia.upload(tok)
st = licencia.state()
check("archivo sin internet: se carga y da el cliente", data["cliente"]["nombre"] == "Valnet S.A." and st["cliente"] == "Valnet S.A.")
check("activo dentro de la fecha", st["modules"]["informes_pro"]["state"] == "activo" and licencia.usable("informes_pro"))
check("en gracia los 15 días siguientes", st["modules"]["recolectores"]["state"] == "gracia" and licencia.usable("recolectores"))
check("vencido después de la gracia (no se usa, no se borra)", st["modules"]["alta_disponibilidad"]["state"] == "vencido"
      and not licencia.usable("alta_disponibilidad"))
check("límites comprados (0 = sin límite)", licencia.limit("informes_pro", "equipos") == 500 and licencia.limit("informes_pro", "sedes") is None)

# firma, clave desconocida y otra instalación
bad = tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")
for name, t, word in (("firma alterada", bad, "firma"), ("firmada con otra clave privada", lic([], key=Ed25519PrivateKey.generate()), "firma"),
                      ("kid que esta versión no conoce", lic([], kid="k7"), "k7"), ("no es una licencia", "hola", "licencia")):
    try:
        licencia.upload(t)
        check(f"rechaza: {name}", False)
    except licencia.LicenseError as e:
        check(f"rechaza: {name}", word in str(e), str(e))
try:
    licencia.upload(lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=9))}], inst="inst_otra"))
    check("rechaza una licencia de otra instalación", False)
except licencia.LicenseError as e:
    check("rechaza una licencia de otra instalación", "otra instalación" in str(e))
check("la licencia buena sigue guardada tras los rechazos", licencia.state()["modules"]["informes_pro"]["state"] == "activo")
licencia.upload(lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=9))}], inst=None))
check("archivo sin instalación (null) sirve en cualquiera", licencia.state()["modules"]["informes_pro"]["state"] == "activo")

# avisos en la campana: 30 días antes, una sola vez
licencia.daily()
licencia.daily()
with SessionLocal() as db:
    ev = [e.message for e in db.scalars(select(Event).where(Event.category == "Licencia"))]
check("aviso 30 días antes en la campana, una sola vez", len(ev) == 1 and "vence el" in ev[0], ev)

# servidor de licencias simulado
CALLS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CALLS.append((self.path, body))
        code, out = 404, {}
        key = body.get("clave")
        if self.path == "/api/licencias/activar":
            code = {"OPNC-AAAA-AAAA-AAAA-AAAA": 200, "OPNC-BBBB-BBBB-BBBB-BBBB": 409, "OPNC-CCCC-CCCC-CCCC-CCCC": 410,
                    "OPNC-DDDD-DDDD-DDDD-DDDD": 429}.get(key, 404)
            if code == 200:
                out = {"licencia": lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=300))}]), "cliente": "Valnet S.A."}
        elif self.path == "/api/licencias/actualizar":
            code = 410 if REVOKE else 200
            out = {} if REVOKE else {"licencia": lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=400))},
                                                      {"id": "recolectores", "hasta": iso(now + timedelta(days=400)), "limites": {"sedes": 3}}])}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


REVOKE = False
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
licencia.LICENSE_URL = f"http://127.0.0.1:{srv.server_address[1]}"
licencia.clear()
for k, word in (("OPNC-ZZZZ-ZZZZ-ZZZZ-ZZZZ", "no existe"), ("OPNC-BBBB-BBBB-BBBB-BBBB", "otra instalación"),
                ("OPNC-CCCC-CCCC-CCCC-CCCC", "revocada"), ("OPNC-DDDD-DDDD-DDDD-DDDD", "Demasiados"), ("12345", "OPNC-XXXX")):
    try:
        licencia.activate(k)
        check(f"activar {k}: error claro", False)
    except licencia.LicenseError as e:
        check(f"activar {k}: error claro", word in str(e), str(e))
data = licencia.activate("opnc-aaaa-aaaa-aaaa-aaaa")
st = licencia.state()
sent = [c for c in CALLS if c[0] == "/api/licencias/activar"][-1][1]
check("activar: guarda la licencia y la clave (cifrada) y da el cliente", st["modules"]["informes_pro"]["state"] == "activo"
      and get_setting("licencia_clave") == "OPNC-AAAA-AAAA-AAAA-AAAA" and data["cliente"]["nombre"] == "Valnet S.A.")
check("activar: solo envía clave, instalación y versión", set(sent) == {"clave", "instalacion", "version"} and sent["instalacion"] == iid)
check("actualizar: trae la licencia más nueva (renovación, más sedes)", licencia.refresh() == "ok"
      and licencia.state()["modules"]["recolectores"]["state"] == "activo" and licencia.limit("recolectores", "sedes") == 3)
sent = [c for c in CALLS if c[0] == "/api/licencias/actualizar"][-1][1]
check("actualizar: solo envía clave e instalación", set(sent) == {"clave", "instalacion"})
REVOKE = True
check("actualizar con 410: revocada, módulos no incluidos y el motivo", licencia.refresh() == "revocada"
      and licencia.state()["modules"]["informes_pro"]["state"] == "no_incluido" and "revocada" in licencia.state()["problem"])
REVOKE = False
licencia.refresh()
check("si vuelve a validar, deja de estar revocada", licencia.state()["modules"]["informes_pro"]["state"] == "activo")
srv.shutdown()
check("sin red: se sigue con la licencia guardada", "No se pudo conectar" in licencia.refresh()
      and licencia.state()["modules"]["informes_pro"]["state"] == "activo")
licencia.clear()
check("cambiar clave: quita clave y licencia", not get_setting("licencia_clave") and licencia.state()["modules"]["informes_pro"]["state"] == "no_incluido")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
