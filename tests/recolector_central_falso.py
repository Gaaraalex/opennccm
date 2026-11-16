"""Un servidor central falso para probar el modo recolector del núcleo (la CI de Windows y de Linux) sin el módulo Pro.

Firma con una clave de prueba propia (NCM_LICENSE_KEYS=ci=…, nunca una de producción) un módulo «recolectores» mínimo
que solo hace lo que el núcleo necesita ver: alta con la clave, latido cada 2 s con su pid, y detenerse con código 0
cuando el central lo revoca (401). Así se prueba el .exe, la tarea programada y el servicio, no el agente de verdad.

    python tests/recolector_central_falso.py PUERTO CARPETA
Escribe en CARPETA/opennccm.env la clave pública de prueba y la clave de alta en CARPETA/clave.txt.
Control para la prueba: GET /prueba/estado (JSON) · POST /prueba/revocar"""
import base64
import io
import json
import secrets
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULO = '''"""Módulo recolectores de PRUEBA (tests/recolector_central_falso.py): alta y latido, nada más."""
import json
import os
import time
from pathlib import Path


def recolector(accion, carpeta, central=None, clave=None):
    import requests
    est = Path(carpeta) / "recolector.json"
    if accion == "alta":
        r = requests.post(central + "/api/recolector/alta", json={"clave_alta": clave}, timeout=30)
        r.raise_for_status()
        Path(carpeta).mkdir(parents=True, exist_ok=True)
        est.write_text(json.dumps({"central": central, **r.json()}), encoding="utf-8")
        print("Listo: alta de prueba hecha.")
        return 0
    st = json.loads(est.read_text(encoding="utf-8"))
    if accion == "estado":
        print("Sede: prueba (" + st["recolector"] + ")")
        return 0
    while True:
        try:
            r = requests.post(st["central"] + "/api/recolector/latido", json={"pid": os.getpid()}, timeout=10,
                              headers={"Authorization": "Bearer " + st["recolector"] + "." + st["secreto"]})
            if r.status_code == 401:
                print("revocado")
                return 0
        except requests.RequestException:
            pass
        time.sleep(2)
'''


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def main() -> None:
    puerto, carpeta = int(sys.argv[1]), Path(sys.argv[2])
    carpeta.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import __version__
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("recolectores/manifest.json", json.dumps({"id": "recolectores", "version": "0.0.0-prueba",
                                                                        "nucleo": __version__}).encode()),
                           ("recolectores/modulo/__init__.py", MODULO.encode())):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    pkg = buf.getvalue()
    firma = f"OPNC1.ci.{b64(key.sign(pkg))}"
    clave = "ra_" + secrets.token_urlsafe(32)
    st = {"altas": 0, "latidos": 0, "pids": [], "revocado": False, "secreto": secrets.token_urlsafe(16), "clave": clave}
    (carpeta / "opennccm.env").write_text(f"# SOLO PRUEBA\nNCM_LICENSE_KEYS=ci={b64(pub)}\n", encoding="utf-8")
    (carpeta / "clave.txt").write_text(clave, encoding="utf-8")
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):  # noqa: N802
            if self.path == "/api/recolector/paquete":
                if self.headers.get("X-OPNC-Alta") != st["clave"]:
                    return self._json(410, {"mensaje": "La clave de alta venció, ya se usó o no existe."})
                self.send_response(200)
                self.send_header("X-OPNC-Firma", firma)
                self.end_headers()
                return self.wfile.write(pkg)
            if self.path == "/prueba/estado":
                with lock:
                    return self._json(200, {k: v for k, v in st.items() if k not in ("secreto", "clave")})
            self._json(404, {})

        def do_POST(self):  # noqa: N802
            body = self._body()
            with lock:
                if self.path == "/api/recolector/alta":
                    if body.get("clave_alta") != st["clave"]:
                        return self._json(410, {"mensaje": "La clave de alta venció, ya se usó o no existe."})
                    st["clave"] = None
                    st["altas"] += 1
                    return self._json(200, {"recolector": "rc_1", "secreto": st["secreto"], "sede": "Prueba"})
                if self.path == "/api/recolector/latido":
                    if st["revocado"] or self.headers.get("Authorization") != f"Bearer rc_1.{st['secreto']}":
                        return self._json(401, {"error": "revocado"})
                    st["latidos"] += 1
                    st["ultimo"] = time.time()
                    if body.get("pid") not in st["pids"]:
                        st["pids"].append(body.get("pid"))
                    return self._json(200, {"ok": True})
                if self.path == "/prueba/revocar":
                    st["revocado"] = True
                    return self._json(200, {"ok": True})
            self._json(404, {})

        def log_message(self, *a):
            pass

    print(f"central falso en http://127.0.0.1:{puerto}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", puerto), H).serve_forever()


if __name__ == "__main__":
    main()
