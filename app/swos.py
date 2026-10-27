"""MikroTik SwitchOS (SwOS y SwOS Lite): no tiene SSH, se gestiona por HTTP.

El respaldo es el mismo archivo que descarga su interfaz web («backup.swb»); la versión, el modelo y la serie salen de
«sys.b». SwOS devuelve sus datos como objetos JavaScript con los textos en hexadecimal («ver:'322e3133'» = «2.13»).
La autenticación es HTTP Digest (o Basic, según lo que pida el equipo).
"""
from __future__ import annotations

import re

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from .i18n import _, _f

TIMEOUT = 30


class SwosError(Exception):
    pass


def _base(dev) -> str:
    port = dev.port if dev.port and dev.port not in (22, 23) else 80
    return f"http://{dev.host}:{port}" if port != 80 else f"http://{dev.host}"


def _get(dev, cred, path: str) -> str:
    url = _base(dev) + path
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code == 401:
            scheme = (r.headers.get("WWW-Authenticate") or "").split(" ", 1)[0].lower()
            auth = (HTTPBasicAuth if scheme == "basic" else HTTPDigestAuth)(cred[0], cred[1])
            r = requests.get(url, auth=auth, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise SwosError(_f("No se pudo conectar con SwitchOS en {0}: {1}", url, str(e).splitlines()[0][:200])) from e
    if r.status_code == 401:
        raise SwosError(_("SwitchOS rechazó el usuario o la contraseña de la credencial."))
    if r.status_code != 200:
        raise SwosError(_f("SwitchOS respondió HTTP {0} en {1}.", r.status_code, url))
    return r.text


def _hex(v: str) -> str:
    try:
        return bytes.fromhex(v).decode("utf-8", "replace") if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", v) else v
    except ValueError:
        return v


def inventory_text(sysb: str) -> str:
    """«sys.b» → líneas «Version:/Model:/Serial:/Identity:» para el inventario."""
    vals = {k: _hex(v) for k, v in re.findall(r"([A-Za-z]\w*):'([^']*)'", sysb)}
    out = []
    for label, keys in (("Version", ("ver",)), ("Model", ("brd", "board")), ("Serial", ("sid", "sn", "serial")),
                        ("Identity", ("id", "name"))):
        v = next((vals[k] for k in keys if vals.get(k)), "")
        if v:
            out.append(f"{label}: {v}")
    return "\n".join(out)


def backup(dev, cred) -> tuple[str, str]:
    """(configuración, inventario) del equipo. «cred»: (usuario, contraseña)."""
    cfg = _get(dev, cred, "/backup.swb")
    if not cfg.strip():
        raise SwosError(_("SwitchOS devolvió un respaldo vacío."))
    try:
        inv = inventory_text(_get(dev, cred, "/sys.b"))
    except SwosError:
        inv = ""  # el respaldo es lo importante; el inventario es opcional
    return cfg.replace("\r\n", "\n"), inv
