"""Proveedor OpenID Connect simulado (como Microsoft Entra ID) para las pruebas: descubrimiento, autorización con PKCE,
canje del código y claves públicas. La identidad que devolverá se fija antes con GET /set?…"""
import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # para firmar tokens falsos
CLIENT_ID, SECRET = "app-opennccm", "secreto-de-prueba"


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


class Provider:
    def __init__(self, port: int):
        self.port, self.base = port, f"http://127.0.0.1:{port}"
        self.identity = {}
        self.codes = {}
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _handler(self):
        prov = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/.well-known/openid-configuration":
                    return self._json({"issuer": prov.base, "authorization_endpoint": prov.base + "/authorize",
                                       "token_endpoint": prov.base + "/token", "jwks_uri": prov.base + "/keys"})
                if u.path == "/keys":
                    pub = KEY.public_key().public_numbers()
                    return self._json({"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256",
                                                 "n": _b64(pub.n), "e": _b64(pub.e)}]})
                if u.path == "/set":
                    prov.identity = {"user": q.get("user", ""), "groups": [g for g in q.get("groups", "").split(",") if g],
                                     "mode": q.get("mode", "")}
                    return self._json({"ok": True})
                if u.path == "/authorize":
                    if q.get("client_id") != CLIENT_ID or q.get("code_challenge_method") != "S256":
                        return self._json({"error": "invalid_request"}, 400)
                    code = secrets.token_urlsafe(16)
                    prov.codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"],
                                        "redirect": q["redirect_uri"], **prov.identity}
                    self.send_response(302)
                    self.send_header("Location", q["redirect_uri"] + "?" + urlencode({"code": code, "state": q["state"]}))
                    self.end_headers()
                    return None
                return self._json({"error": "not_found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                if urlparse(self.path).path != "/token":
                    return self._json({"error": "not_found"}, 404)
                c = prov.codes.pop(f.get("code", ""), None)
                verifier_ok = c and base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()
                                                             ).rstrip(b"=").decode() == c["challenge"]
                if not c or f.get("client_id") != CLIENT_ID or f.get("client_secret") != SECRET or not verifier_ok \
                        or f.get("redirect_uri") != c["redirect"]:
                    return self._json({"error": "invalid_grant"}, 400)
                now = int(time.time())
                claims = {"iss": prov.base, "aud": "otra-app" if c["mode"] == "aud" else CLIENT_ID, "sub": "s-" + c["user"],
                          "iat": now, "exp": now + 600, "nonce": "otro" if c["mode"] == "nonce" else c["nonce"],
                          "preferred_username": c["user"], "email": c["user"], "name": c["user"].split("@")[0],
                          "groups": c["groups"], "tid": "t1"}
                key = OTHER if c["mode"] == "sig" else KEY
                token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
                return self._json({"id_token": token, "access_token": "x", "token_type": "Bearer"})
        return H
