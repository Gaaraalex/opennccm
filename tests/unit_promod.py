"""Cargador de módulos Pro (app/promod.py): un módulo de prueba firmado con una clave de prueba hecha aquí se instala,
se guarda en la carpeta de datos y se carga en una app; uno modificado, firmado con otra clave, con rutas que salen de
su carpeta, de otra versión de OpenNCCM o sin licencia no se instala o no se carga. También la descarga contra un
servidor simulado (cabecera X-OPNC-Firma, 403/404). Sin internet."""
import base64
import io
import json
import os
import sys
import tarfile
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
os.environ["NCM_LICENSE_KEYS"] = "k0=" + b64(PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi import FastAPI  # noqa: E402

from app import __version__, licencia, promod  # noqa: E402
from app.db import init_db, set_setting  # noqa: E402

init_db()
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def lic(mods):
    data = {"v": 1, "id": "lic_1", "cliente": {"nombre": "Valnet S.A."}, "instalacion": licencia.install_id(),
            "gracia_dias": 15, "modulos": mods}
    raw = json.dumps(data, separators=(",", ":")).encode()
    return f"OPNC1.k0.{b64(raw)}.{b64(PRIV.sign(raw))}"


CODE = '''
from fastapi.responses import PlainTextResponse
from fastapi import APIRouter
router = APIRouter()

@router.get("/pro/informes/hola")
def hola():
    return PlainTextResponse("hola desde el módulo")

def register(app, ctx):
    app.include_router(router)

def page(request, user, m):
    return PlainTextResponse("página del módulo: " + m["state"])
'''


def package(mid="informes_pro", nucleo=__version__, extra=None, manifest_id=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {f"{mid}/manifest.json": json.dumps({"id": manifest_id or mid, "version": "1.0.0", "nucleo": nucleo}),
                 f"{mid}/modulo/__init__.py": CODE}
        files.update(extra or {})
        for name, text in files.items():
            data = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def sign(data, key=PRIV):
    return b64(key.sign(data))


now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
try:
    promod.install("informes_pro", package(), "x")
    check("sin licencia activada no se instala nada", False)
except promod.ModuleError as e:
    check("sin licencia activada no se instala nada", "active la licencia" in str(e))
set_setting("licencia", lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=30))}]))

pkg = package()
for name, data, sig, word in (
        ("firma de otra clave", pkg, sign(pkg, Ed25519PrivateKey.generate()), "firma"),
        ("paquete modificado después de firmar", pkg + b"x", sign(pkg), "firma"),
        ("ruta que sale de su carpeta", package(extra={"informes_pro/../../fuera.py": "x"}), None, "no corresponde"),
        ("archivos de otro módulo", package(extra={"otro/x.py": "x"}), None, "no corresponde"),
        ("manifest de otro módulo", package(manifest_id="recolectores"), None, "no es de este módulo")):
    try:
        promod.install("informes_pro", data, sig or sign(data))
        check(f"rechaza: {name}", False)
    except promod.ModuleError as e:
        check(f"rechaza: {name}", word in str(e), str(e))
check("nada quedó instalado tras los rechazos", promod.installed("informes_pro") is None
      and not os.path.exists(os.path.join(tmp, "fuera.py")))

man = promod.install("informes_pro", pkg, sign(pkg))
check("paquete firmado: se instala y se guarda tal cual con su firma", man["version"] == "1.0.0"
      and (promod.DIR / "informes_pro.tar.gz").read_bytes() == pkg and (promod.DIR / "informes_pro" / "modulo").is_dir())
app = FastAPI()
loaded = promod.load(app, "informes_pro")
def _paths(routes):  # las rutas de los routers incluidos (FastAPI nuevo los guarda sin desplegar)
    for r in routes:
        sub = getattr(getattr(r, "original_router", None), "routes", None) or getattr(r, "routes", None)
        yield from (_paths(sub) if sub else [getattr(r, "path", "")])


paths = list(_paths(app.routes))
check("se carga: registra sus rutas y su página", loaded and "/pro/informes/hola" in paths
      and promod.page("informes_pro") is not None, (loaded, paths, promod.PROBLEMS))
check("la página del módulo recibe el estado de la licencia",
      promod.page("informes_pro")(None, None, {"state": "activo"}).body == "página del módulo: activo".encode())

# al arrancar otra vez: el paquete guardado se vuelve a comprobar
promod.LOADED.clear()
(promod.DIR / "informes_pro.tar.gz").write_bytes(pkg + b"cambiado")
check("paquete guardado cambiado: no se carga y lo dice", not promod.load(FastAPI(), "informes_pro")
      and "firma" in promod.info("informes_pro")["problem"])
(promod.DIR / "informes_pro.tar.gz").write_bytes(pkg)
old = package(nucleo="1.0.0")
promod.install("informes_pro", old, sign(old))
check("módulo armado para otra versión de OpenNCCM: no se carga", not promod.load(FastAPI(), "informes_pro")
      and promod.info("informes_pro")["outdated"])
promod.install("informes_pro", pkg, sign(pkg))
set_setting("licencia", lic([]))
check("licencia sin el módulo: no se carga aunque esté instalado", not promod.load(FastAPI(), "informes_pro"))
set_setting("licencia", lic([{"id": "informes_pro", "hasta": iso(now + timedelta(days=30))}]))

# descarga contra un servidor simulado
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        if body["modulo"] == "recolectores":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-OPNC-Firma", sign(pkg))
        self.send_header("Content-Length", str(len(pkg)))
        self.end_headers()
        self.wfile.write(pkg)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
licencia.LICENSE_URL = f"http://127.0.0.1:{srv.server_address[1]}"
set_setting("licencia_clave", "OPNC-AAAA-AAAA-AAAA-AAAA")
promod.LOADED.clear()
(promod.DIR / "informes_pro" / "manifest.json").write_text(json.dumps({"id": "informes_pro", "version": "0.9", "nucleo": "1.0.0"}))
app = FastAPI()
got = promod.sync(app)
check("sync: baja el módulo de otra versión y lo carga", got == ["informes_pro"] and "informes_pro" in promod.LOADED)
check("la descarga solo envía clave, instalación, módulo y versión", set(SEEN[-1]) == {"clave", "instalacion", "modulo", "version"}
      and SEEN[-1]["version"] == __version__)
check("sync: no vuelve a bajar lo que ya está al día", promod.sync(app) == [])
try:
    promod.download("recolectores")
    check("404: lo dice", False)
except promod.ModuleError as e:
    check("404: lo dice", "Todavía no hay" in str(e))
srv.shutdown()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
