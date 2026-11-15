"""Modo recolector: este OpenNCCM respalda los equipos de una sede para otro OpenNCCM (el servidor central).

Es una puerta del núcleo, como app/prohooks.py: el trabajo del recolector lo hace el módulo «recolectores», que esta
instalación baja del servidor central en el alta, firmado. Antes de usarlo se comprueba su firma con las claves
públicas que trae el núcleo (app/licencia.py), igual que cualquier módulo. Sin interfaz web: solo el servicio
(«python -m app.cli recolector correr», con NCM_MODO=recolector) y «opennccm recolector estado».

    python -m app.cli recolector alta <dirección del central> <clave de alta>
    python -m app.cli recolector correr
    python -m app.cli recolector estado
"""
from __future__ import annotations

import hashlib
import logging
import os
import ssl
import sys
from urllib.parse import urlparse

from . import promod

MID = "recolectores"
log = logging.getLogger("opennccm.recolector")


class RecolectorError(Exception):
    pass


def activo() -> bool:
    """¿Esta instalación corre en modo recolector? (lo escribe install.sh --recolector en opennccm.env)"""
    return os.environ.get("NCM_MODO", "") == "recolector"


def carpeta():
    """Donde el módulo guarda el estado del recolector (su id, su secreto cifrado y la cola)."""
    return promod.DIR.parent / "recolector"


def huella(url: str) -> str | None:
    """SHA-256 del certificado que presenta el central por HTTPS (None si la dirección es http)."""
    u = urlparse(url)
    if u.scheme != "https":
        return None
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=15)
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def _sesion(fp: str | None):
    """Una sesión HTTP que solo habla con el certificado de esa huella (sirve con certificados propios del central)."""
    import requests
    from requests.adapters import HTTPAdapter

    class _Pin(HTTPAdapter):
        def init_poolmanager(self, *a, **kw):
            kw["assert_fingerprint"] = fp
            kw["cert_reqs"] = ssl.CERT_NONE
            return super().init_poolmanager(*a, **kw)

    s = requests.Session()
    if fp:
        s.mount("https://", _Pin())
    return s


def bajar_modulo(central: str, clave: str) -> dict:
    """Baja del central el módulo firmado (la clave de alta lo autoriza; no se gasta) y lo instala si la firma vale."""
    import requests
    central = central.rstrip("/")
    if urlparse(central).scheme not in ("http", "https"):
        raise RecolectorError(f"La dirección del servidor central no es válida: {central}")
    try:
        fp = huella(central)
        r = _sesion(fp).get(central + "/api/recolector/paquete", headers={"X-OPNC-Alta": clave}, timeout=120)
    except (OSError, requests.RequestException) as e:
        raise RecolectorError(f"No se pudo conectar con {central}: {str(e).splitlines()[0][:200]}") from e
    if r.status_code != 200:
        try:
            msg = r.json().get("mensaje")
        except ValueError:
            msg = None
        raise RecolectorError(msg or f"El servidor central respondió {r.status_code}.")
    try:
        return promod.install(MID, r.content, r.headers.get("X-OPNC-Firma", ""), r.headers.get("X-OPNC-Kid") or None)
    except promod.ModuleError as e:
        raise RecolectorError(str(e)) from e


def modulo():
    """El código del módulo instalado, con su firma comprobada. No se registra en la web: aquí no hay web."""
    if not promod.installed(MID):
        raise RecolectorError("Este recolector todavía no se dio de alta: sudo opennccm recolector alta <central> <clave>")
    try:
        mod = promod.code(MID)
    except promod.ModuleError as e:
        raise RecolectorError(str(e)) from e
    if not hasattr(mod, "recolector"):
        raise RecolectorError("El módulo instalado no trae el recolector.")
    return mod


def main(args: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    accion = args[0] if args else ""
    try:
        if accion == "alta" and len(args) >= 3:
            bajar_modulo(args[1], args[2])
            return int(modulo().recolector("alta", central=args[1].rstrip("/"), clave=args[2], carpeta=carpeta()) or 0)
        if accion in ("correr", "estado"):
            return int(modulo().recolector(accion, carpeta=carpeta()) or 0)
    except RecolectorError as e:
        print(e, file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[-1].rstrip(), file=sys.stderr)
    return 2
