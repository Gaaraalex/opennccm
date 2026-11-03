"""Feature key de WatchGuard con firma: entrada a la Web UI de Fireware (agent/login → auth/login → System › Feature Key
→ auth/logout) contra una Web UI simulada en 127.0.0.1, y el texto que se pega para restaurarla, igual al de un T40 real
(Fireware 12.8.1). Sin red."""
import html
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import fireware_web as fw  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


SIG = "302e0215038419db-48d78b97a8c40107-821b0e916f5dfd21-5a02150213902382-3425a307b388cb82-5fce561e0ee859ca"
FEATS = ["ACCESS_PORTAL@Apr-29-2024", "APP_CONTROL@Apr-29-2024", "AUTHENTICATED_USER#0", "AV@Apr-29-2024",
         "BOVPN_TUNNEL#30", "CLOUD_CONNECT@Apr-29-2024", "CLOUD_VISIBILITY@Apr-29-2024", "DIMENSION_BASIC@Apr-29-2024",
         "DIMENSION_COMMAND@Apr-29-2024", "FIREWARE_XTM", "FW_RULE#0", "FW_SPEED#0", "FW_USERS#0", "IPS@Apr-29-2024",
         "L2TP_USER#30", "LIVESECURITY@Apr-29-2024", "MUVPN_USER#30", "NETWORK_DISCOVERY@Apr-29-2024", "RED@Apr-29-2024",
         "SESSION#250000", "SPAMBLOCKER@Apr-29-2024;UC17Q63WEU2Q2UGD54HB", "SSLVPN_USER#30", "SUPPORT@Apr-29-2024",
         "VLAN#50", "VPN_SPEED#0", "WEBBLOCKER@Apr-29-2024", "XTM_PRO"]
# «show feature-key D02807134F41D» de un T40 real
CLI = ("--\n-- Feature-Key <D02807134F41D>\n--\nFeature-Key id: D02807134F41D\nFeature-Key name: 11-03-2026_08:48\n"
       "expiration date: never\nmodel: T40\nfeatures:\n" + "".join(f"    ({i:2d}): {f}\n" for i, f in enumerate(FEATS, 1)))
# lo que muestra el mismo Firebox para pegarla al restaurarla
EXPECTED = ("Serial Number: D02807134F41D\nLicense ID: D02807134F41D\nName: 11-03-2026_08:48\nModel: T40\nVersion: 2\n"
            + "".join(f"Feature: {f}\n" for f in FEATS) + "Expiration: never\nSignature: " + SIG)

LOG = []


class WebUI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html", headers=()):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _session(self):
        return "session_id=abc123" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        LOG.append(("GET", self.path))
        if self.path == "/login":
            return self._send(200, '<form id="login"><input type="hidden" id="cp_csrf_token" name="cp_csrf_token" value="CP1" />')
        if self.path == "/system/featurekey":
            if not self._session():
                return self._send(302, "", headers=[("Location", "/login")])
            return self._send(200, '<input type="hidden" id="cp_csrf_token" value="CP2"/>'
                                   '<input type="text" id="model" value="T40" readonly/>'
                                   '<input type="text" id="serial" value="D02807134F41D" readonly/>'
                                   f'<input type="text" id="signature" value="{SIG}" readonly/>')
        self._send(404, "")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        LOG.append(("POST", self.path, body))
        if self.path.startswith("/auth/") and not (self.headers.get("Referer") or "").startswith(f"http://127.0.0.1:{PORT}/"):
            return self._send(403, "<h2>403 Forbidden</h2>")  # como Fireware 12.8: formulario sin página de origen
        if self.path == "/agent/login":
            user = html.unescape(re.search(r"<name>user</name><value><string>([^<]*)", body).group(1))
            pw = html.unescape(re.search(r"<name>password</name><value><string>([^<]*)", body).group(1))
            if (user, pw) != ("read", "cl&ve<1>"):
                return self._send(200, "<?xml version='1.0'?><methodResponse><fault><value><struct><member><name>faultString"
                                       "</name><value><string>invalid credentials or user doesn't exist</string></value>"
                                       "</member></struct></value></fault></methodResponse>", "text/xml")
            return self._send(200, "<?xml version='1.0'?><methodResponse><params><param><value><struct>"
                                   # como un T40 con Fireware 12.8.1: valores sin tipo
                                   "<member><name>sid</name><value>S1</value></member>"
                                   "<member><name>csrf_token</name><value>W1</value></member>"
                                   "<member><name>response</name><value></value></member>"
                                   "<member><name>readwrite</name><value><struct><member><name>privilege</name>"
                                   "<value>1</value></member><member><name>peer_name</name><value>error</value>"
                                   "</member></struct></value></member>"
                                   "</struct></value></param></params></methodResponse>", "text/xml")
        if self.path == "/auth/login":
            f = parse_qs(body)
            ok = f.get("sid") == ["S1"] and f.get("wga_csrf_token") == ["W1"] and f.get("cp_csrf_token") == ["CP1"]                 and f.get("privilege") == ["1"]
            return self._send(302, "", headers=[("Location", "/dashboard")] + ([("Set-Cookie", "session_id=abc123; Path=/")] if ok else []))
        if self.path == "/auth/logout":
            return self._send(302, "", headers=[("Location", "/login")])
        self._send(404, "")


srv = ThreadingHTTPServer(("127.0.0.1", 0), WebUI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
BASE_URL = fw.base_url
fw.base_url = lambda dev: f"http://127.0.0.1:{PORT}"  # la de verdad es https; aquí, http local

dev = SimpleNamespace(host="192.0.2.10", web_url=None)
s = fw.read_summary(dev, "read", "cl&ve<1>")
check("lee modelo, serie y firma de System › Feature Key", s == {"serial": "D02807134F41D", "model": "T40", "signature": SIG}, s)
check("entra como la página de ingreso: agent/login y luego auth/login con sid y tokens",
      [x[1] for x in LOG if x[0] == "POST"][:2] == ["/agent/login", "/auth/login"])
check("la contraseña va escapada en el XML-RPC", any("cl&amp;ve&lt;1&gt;" in x[2] for x in LOG if x[1] == "/agent/login"))
check("cierra la sesión al terminar, con su token", LOG[-1][:2] == ("POST", "/auth/logout") and LOG[-1][2] == "cp_csrf_token=CP2")

LOG.clear()
try:
    fw.read_summary(dev, "read", "mala")
    check("contraseña mala: error", False)
except fw.FirewareWebError as e:
    check("contraseña mala: error con el motivo del Firebox", "invalid credentials" in str(e), str(e))
check("contraseña mala: no pide la página ni cierra una sesión que no abrió",
      not any(x[1] in ("/system/featurekey", "/auth/logout") for x in LOG))

check("texto igual, letra por letra, al que muestra el Firebox", fw.build(CLI, s) == EXPECTED, fw.build(CLI, s))
check("sin firma no se arma", fw.build(CLI, {**s, "signature": ""}) is None)
check("salida que no es el detalle: no se arma", fw.build("% Invalid input", s) is None)
two = CLI + "\n" + CLI.replace("D02807134F41D", "ABC123456789")
check("varias claves: se arma la activa (la de la serie que firma la Web UI)",
      fw.build(two, s) == EXPECTED)
check("varias claves y ninguna es de esa serie: no se adivina",
      fw.build(two.replace("D02807134F41D", "ZZZ999999999"), s) is None)

check("Web UI: la «Interfaz web» del equipo si tiene puerto propio",
      BASE_URL(SimpleNamespace(host="10.1.1.1", web_url="https://fw.local:9443/")) == "https://fw.local:9443")
check("Web UI: si no, https://IP:8080", BASE_URL(SimpleNamespace(host="10.1.1.1", web_url=None)) == "https://10.1.1.1:8080"
      and BASE_URL(SimpleNamespace(host="10.1.1.1", web_url="https://10.1.1.1/")) == "https://10.1.1.1:8080")
srv.shutdown()

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
