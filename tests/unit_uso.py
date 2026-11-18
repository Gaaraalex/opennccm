"""Estadística de uso (app/uso.py, contrato «CONTRATO-USO.md»): número propio u_ que no cambia y no es el inst_ de la
licencia, el contenido exacto {instalacion, version} (sin «pro», contrato v2), el aviso contra una web simulada
y, sobre todo, que sin red o con la web caída nada falla, no se muestra ningún error y no se reintenta en bucle.
Nunca llama a comunidad.opennccm.com."""
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
os.environ.update({"NCM_DATA_DIR": tmp, "NCM_KEY_FILE": os.path.join(tmp, "secret.key"),
                   "NCM_USO_URL": "http://127.0.0.1:9/api/uso/aviso", "NCM_LICENSE_URL": "http://127.0.0.1:9"})
PRIV = Ed25519PrivateKey.generate()
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
os.environ["NCM_LICENSE_KEYS"] = "k0=" + b64(PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import __version__, licencia, uso  # noqa: E402
from app.db import get_setting, init_db, set_setting  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# el formato que exige la web (CONTRATO-USO.md): «u_» + UUID v4 estándar, con guiones y en minúsculas; si no, 400
import re  # noqa: E402
WEB_ID = re.compile(r"^u_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
WEB_VER = re.compile(r"^\d+\.\d+\.\d+$")
uid = uso.usage_id()
check("número propio «u_» + UUID v4 con guiones, como lo acepta la web, siempre el mismo",
      bool(WEB_ID.match(uid)) and uso.usage_id() == uid, uid)
# instalaciones de 2.45.2 o anteriores: se guardó sin guiones (la web lo rechazaba); se corrige conservando el número
old = "u_" + uid[2:].replace("-", "")
set_setting("uso_id", old)
check("número viejo sin guiones (≤ 2.45.2): pasa a la forma con guiones, mismo UUID", uso.usage_id() == uid
      and get_setting("uso_id") == uid)
set_setting("uso_id", "u_no-es-un-uuid")
nuevo = uso.usage_id()
check("número inválido guardado: se crea uno nuevo válido", bool(WEB_ID.match(nuevo)) and nuevo != "u_no-es-un-uuid")
set_setting("uso_id", uid)
check("…distinto del número de la licencia (inst_)", uid[2:] != licencia.install_id()[5:] and not uid.startswith("inst_"))
p = uso.payload()
check("se envía exactamente {instalacion, version}", set(p) == {"instalacion", "version"}
      and p["instalacion"] == uid and p["version"] == __version__, p)

now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
raw = json.dumps({"v": 1, "id": "l", "emitida": iso(now), "cliente": {"nombre": "X"}, "instalacion": licencia.install_id(),
                  "gracia_dias": 15, "modulos": [{"id": "informes_pro", "hasta": iso(now + timedelta(days=30))}]},
                 separators=(",", ":")).encode()
set_setting("licencia", f"OPNC1.k0.{b64(raw)}.{b64(PRIV.sign(raw))}")
check("con un módulo Pro activo tampoco se envía «pro»", set(uso.payload()) == {"instalacion", "version"})

# sin red: ni excepción ni error, y no insiste
check("sin red: no falla y devuelve False", uso.send() is False)
check("…no anota un aviso que no llegó", not get_setting("uso_ultimo") and uso.last_sent() is None)
check("…y no reintenta en bucle: no toca hasta mañana", not uso.due() and not uso.due(uso.MIN_GAP))
check("…Sistema lo muestra como «sin conexión» (información, no error)", uso.offline())

# web simulada
GOT = []
CODE = [204]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        GOT.append((self.path, self.headers.get("Content-Type"), body))
        try:  # valida como la web real: solo {instalacion, version}, u_ + UUID v4 con guiones, versión X.Y.Z
            d = json.loads(body)
            valid = set(d) == {"instalacion", "version"} and WEB_ID.match(d["instalacion"]) and WEB_VER.match(d["version"])
        except Exception:  # noqa: BLE001
            valid = False
        self.send_response(CODE[0] if valid else 400)
        self.end_headers()


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
uso.URL = f"http://127.0.0.1:{srv.server_address[1]}/api/uso/aviso"
set_setting("uso_intento", "")
uso.at_startup()
check("al arrancar sin aviso previo: avisa", len(GOT) == 1 and GOT[0][0] == "/api/uso/aviso" and GOT[0][1] == "application/json")
check("…con el cuerpo exacto", len(GOT) == 1 and json.loads(GOT[0][2]) == uso.payload(), GOT[:1])
check("…y anota el último aviso, ya no «sin conexión»", uso.last_sent() is not None and not uso.offline())
uso.daily()
uso.at_startup()
check("una vez al día: la tarea diaria y un reinicio no repiten", len(GOT) == 1)
set_setting("uso_intento", (now - timedelta(days=2)).isoformat(timespec="seconds"))
uso.at_startup()
check("al arrancar si pasó más de un día: avisa", len(GOT) == 2)
CODE[0] = 400
set_setting("uso_intento", "")
ok_before = uso.last_sent()
check("si la web responde un error: no falla y no lo anota como aviso", uso.send() is False)
check("…Sistema sigue mostrando el último aviso que salió bien, con «sin conexión»", uso.last_sent() == ok_before
      and uso.offline())
srv.shutdown()

h1 = uso.hour()
check("hora al azar propia de este servidor, siempre la misma", h1 == uso.hour() and 0 <= h1[0] < 24 and 0 <= h1[1] < 60)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
