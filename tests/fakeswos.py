"""MikroTik SwitchOS simulado (HTTP con autenticación Digest): fakeswos.py PUERTO
Usuario admin / contraseña Clave-SwOS. Sirve /sys.b (textos en hexadecimal, como SwOS) y /backup.swb."""
import hashlib
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
USER, PASS, REALM, NONCE = "admin", "Clave-SwOS", "CRS326-24G-2S+", "6c3f2a9e1b"


def hx(s):
    return s.encode().hex()


SYS_B = (f"{{id:'{hx('SW-Pasillo')}',ver:'{hx('2.17')}',brd:'{hx('CRS326-24G-2S+')}',"
         f"sid:'{hx('HCN08Q4XYZ')}',mac:'48a98a000001',upt:0x0012d687}}")
BACKUP = ("sys.b:{id:'53572d50617369 6c6c6f',dtrp:0x01,ainf:0x01}\nvlan.b:[{vid:0x000a,nm:'4461746f73',mbr:0x0000000f},"
          "{vid:0x0014,nm:'566f7a',mbr:0x00000030}]\nlink.b:{en:0x03ffffff,nm:['506f727431','506f727432']}\n")


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _challenge(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Digest realm="{REALM}", qop="auth", nonce="{NONCE}", opaque="0"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Digest "):
            return self._challenge()
        p = dict(re.findall(r'(\w+)="?([^",]+)"?', auth))
        ha1, ha2 = md5(f"{USER}:{REALM}:{PASS}"), md5(f"GET:{p.get('uri')}")
        ok = p.get("username") == USER and p.get("response") == md5(
            f"{ha1}:{p.get('nonce')}:{p.get('nc')}:{p.get('cnonce')}:{p.get('qop')}:{ha2}")
        if not ok:
            return self._challenge()
        body = {"/sys.b": SYS_B, "/backup.swb": BACKUP}.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
