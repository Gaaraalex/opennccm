"""Feature key de WatchGuard (Fireware) en el formato que se pega para restaurarla.

La CLI («show feature-key ID») trae el ID, el nombre, el modelo, el vencimiento y cada función, pero no la firma: esa
solo la muestra la Web UI (System › Feature Key). Aquí se entra a la Web UI con la misma credencial del respaldo
(basta un usuario Device Monitor, de solo lectura), se lee la firma, se cierra la sesión y se arma el texto como lo
muestra el Firebox:

    Serial Number: …
    License ID: …
    Name: …
    Model: …
    Version: 2
    Feature: …            (una línea por función, en el orden de la CLI)
    Expiration: …
    Signature: …

La entrada de la Web UI es la misma que hace su página de ingreso: «agent/login» (XML-RPC) da el sid y el token, y
«auth/login» abre la sesión; «auth/logout» la cierra. El certificado de la Web UI es autofirmado de fábrica, así que
no se valida (igual que el SSH de los respaldos, que tampoco fija la clave del equipo).
"""
from __future__ import annotations

import html
import re
import warnings
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import requests

TIMEOUT = 20
PORT = 8080  # puerto de fábrica de la Web UI de Fireware
DOMAIN = "Firebox-DB"
VERSION = "2"  # formato de las feature keys de Fireware; la Web UI no lo muestra y la CLI tampoco


class FirewareWebError(Exception):
    pass


def base_url(dev) -> str:
    """La dirección de la Web UI: la «Interfaz web» del equipo si tiene puerto propio, si no https://host:8080."""
    u = urlsplit(dev.web_url or "")
    if u.scheme == "https" and u.hostname and u.port:
        return f"https://{u.hostname}:{u.port}"
    return f"https://{dev.host}:{PORT}"


def _input(page: str, name: str) -> str:
    m = re.search(r'<input[^>]*\bid="%s"[^>]*\bvalue="([^"]*)"' % re.escape(name), page)
    return html.unescape(m.group(1)).strip() if m else ""


def _xmlrpc_login(user: str, password: str, domain: str) -> bytes:
    def member(k, v):
        return f"<member><name>{k}</name><value><string>{html.escape(v)}</string></value></member>"
    body = "".join(member(k, v) for k, v in (("password", password), ("user", user), ("domain", domain), ("uitype", "2")))
    return (f"<?xml version=\"1.0\"?><methodCall><methodName>login</methodName><params><param><value><struct>{body}"
            "</struct></value></param></params></methodCall>").encode()


def _xmlrpc_values(text: str) -> dict:
    """sid, csrf_token y privilege de la respuesta de «agent/login», o el mensaje del fallo."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise FirewareWebError("La Web UI respondió algo que no es XML-RPC al iniciar sesión.") from e
    fault = root.find(".//fault")
    if fault is not None:
        msg = " ".join(s.text or "" for s in fault.iter("string")).strip()
        raise FirewareWebError(f"La Web UI rechazó el inicio de sesión: {msg or 'sin motivo'}")
    out = {}
    for m in root.iter("member"):
        name, val = m.findtext("name"), m.find("value")
        if val is None or not name or (len(val) and val[0].tag == "struct"):
            continue
        # Fireware manda los valores sin tipo («<value>…</value>», que en XML-RPC es texto); se aceptan los dos
        out.setdefault(name, ((val[0].text if len(val) else val.text) or "").strip())
    return out


def read_summary(dev, user: str, password: str) -> dict:
    """Modelo, serie y firma de System › Feature Key. Siempre cierra la sesión que abre."""
    base = base_url(dev)
    s = requests.Session()
    s.verify = False
    logged = False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # certificado autofirmado de fábrica
        try:
            r = s.get(f"{base}/login", timeout=TIMEOUT)
            cp = _input(r.text, "cp_csrf_token")
            r = s.post(f"{base}/agent/login", data=_xmlrpc_login(user, password, DOMAIN), timeout=TIMEOUT,
                       headers={"Content-Type": "text/xml"})
            if r.status_code != 200:
                raise FirewareWebError(f"La Web UI respondió HTTP {r.status_code} al iniciar sesión.")
            v = _xmlrpc_values(r.text)
            if not v.get("sid"):
                raise FirewareWebError("La Web UI no devolvió una sesión.")
            # los formularios de la Web UI se rechazan (403) si no dicen de qué página vienen, como un navegador
            r = s.post(f"{base}/auth/login", timeout=TIMEOUT, headers={"Referer": f"{base}/login", "Origin": base}, data={
                "username": user, "password": password, "domain": DOMAIN, "sid": v["sid"],
                "wga_csrf_token": v.get("csrf_token", ""), "cp_csrf_token": cp, "privilege": v.get("privilege", "0"),
                "from_page": "/"}, allow_redirects=False)
            if r.status_code not in (200, 302, 303):
                raise FirewareWebError(f"La Web UI respondió HTTP {r.status_code} al abrir la sesión.")
            logged = True
            r = s.get(f"{base}/system/featurekey", timeout=TIMEOUT)
            page = r.text
            cp = _input(page, "cp_csrf_token") or cp  # el token de la sesión abierta, para cerrarla
            out = {"serial": _input(page, "serial"), "model": _input(page, "model"), "signature": _input(page, "signature")}
            if not re.fullmatch(r"[0-9a-f]+(?:-[0-9a-f]+)*", out["signature"]):
                raise FirewareWebError("la página System › Feature Key no trajo la firma (¿el usuario puede entrar a la Web UI?)")
            return out
        except requests.RequestException as e:
            raise FirewareWebError(f"No se pudo conectar con la Web UI en {base}: {str(e).splitlines()[0][:200]}") from e
        finally:
            if logged:
                try:
                    s.post(f"{base}/auth/logout", data={"cp_csrf_token": cp}, timeout=TIMEOUT, allow_redirects=False,
                           headers={"Referer": f"{base}/system/featurekey", "Origin": base})
                except requests.RequestException:
                    pass


def parse_detail(text: str) -> list[dict]:
    """Cada bloque de «show feature-key ID»: id, nombre, vencimiento, modelo y funciones (en su orden)."""
    keys, cur = [], None
    for line in text.splitlines():
        m = re.match(r"\s*Feature-Key id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1), "name": "", "expiration": "", "model": "", "features": []}
            keys.append(cur)
            continue
        if cur is None:
            continue
        if (m := re.match(r"\s*Feature-Key name:\s*(.*?)\s*$", line)):
            cur["name"] = m.group(1)
        elif (m := re.match(r"\s*expiration date:\s*(.*?)\s*$", line, re.I)):
            cur["expiration"] = m.group(1)
        elif (m := re.match(r"\s*model:\s*(.*?)\s*$", line, re.I)):
            cur["model"] = m.group(1)
        elif (m := re.match(r"\s*\(\s*\d+\):\s*(\S.*?)\s*$", line)):
            cur["features"].append(m.group(1))
    return [k for k in keys if k["features"]]


def build(detail: str, summary: dict) -> str | None:
    """El texto restaurable de la feature key activa (la que firma la Web UI), o None si no se puede armar."""
    keys = parse_detail(detail)
    if not keys or not summary.get("signature"):
        return None
    serial = summary.get("serial") or ""
    key = next((k for k in keys if k["id"] == serial), keys[0] if len(keys) == 1 else None)
    if key is None:
        return None
    lines = [f"Serial Number: {serial or key['id']}", f"License ID: {key['id']}", f"Name: {key['name']}",
             f"Model: {key['model'] or summary.get('model', '')}", f"Version: {VERSION}"]
    lines += [f"Feature: {f}" for f in key["features"]]
    lines += [f"Expiration: {key['expiration'] or 'never'}", f"Signature: {summary['signature']}"]
    return "\n".join(lines)  # la descarga agrega el salto final
