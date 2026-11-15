"""Cargador de los módulos Pro: los baja con la clave de licencia, comprueba la firma y los carga al arrancar.

El código de cada módulo vive en el repositorio privado opennccm-pro y no viaja en este paquete. Lo entrega
comunidad.opennccm.com (POST /api/modulos/descargar, contrato v1) como un .tar.gz firmado: la cabecera X-OPNC-Firma es
la firma Ed25519 de los bytes exactos del archivo, con la misma clave (kid) que firma la licencia. Sin internet, el
cliente sube el .tar.gz y su .firma en Licencia y módulos.

Se guarda en DATA_DIR/pro/: <módulo>.tar.gz y <módulo>.tar.gz.firma (lo que llegó, tal cual) y la carpeta <módulo>/ ya
descomprimida. Al arrancar se vuelve a comprobar la firma del .tar.gz: si alguien cambió el archivo, no se carga.

Formato del paquete: una carpeta <módulo>/ con manifest.json {"id", "version", "nucleo"} («nucleo»: la versión de
OpenNCCM para la que se armó) y el paquete Python modulo/ con register(app, ctx). ctx da lo que el módulo necesita del
núcleo sin tocar su código: licencia, scheduler y render."""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import shutil
import sys
import tarfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import licencia
from .config import DATA_DIR
from .db import get_setting
from .i18n import _, _f

log = logging.getLogger("opennccm")
DIR = DATA_DIR / "pro"
MAX_SIZE = 20 * 2 ** 20
LOADED: dict[str, object] = {}     # módulo -> su paquete Python ya cargado
PROBLEMS: dict[str, str] = {}      # módulo -> por qué no se cargó (se muestra en Licencia y módulos)


class ModuleError(Exception):
    pass


def _core() -> str:
    from . import __version__
    return __version__


def _kid() -> str:
    token = get_setting("licencia", "")
    parts = token.split(".")
    if len(parts) != 4:
        raise ModuleError(_("Primero active la licencia."))
    return parts[1]


def verify_package(data: bytes, signature: str, kid: str | None = None) -> None:
    """Firma Ed25519 del .tar.gz: la de la cabecera X-OPNC-Firma (con X-OPNC-Kid, o el kid de la licencia) o la del
    archivo .firma de Mi cuenta, de una línea «OPNC1.<kid>.<firma>»."""
    sig = (signature or "").strip()
    if sig.startswith("OPNC1."):
        _p, kid, sig = sig.split(".", 2)
    elif "." in sig:
        kid, sig = sig.split(".", 1)
    kid = kid or _kid()
    pub = licencia.public_keys().get(kid)
    if not pub:
        raise ModuleError(_f("El módulo está firmado con una clave que esta versión no conoce ({0}).", kid))
    try:
        Ed25519PublicKey.from_public_bytes(licencia._b64d(pub)).verify(licencia._b64d(sig), data)
    except (InvalidSignature, ValueError) as e:
        raise ModuleError(_("La firma del módulo no es válida: no se instaló.")) from e


def _extract(mid: str, data: bytes) -> dict:
    """Descomprime en una carpeta nueva (sin rutas que salgan de ella) y devuelve su manifest."""
    tmp = DIR / f".{mid}.nuevo"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            name = m.name.lstrip("./")
            if not name.startswith(f"{mid}/") and name != mid or ".." in name.split("/") or m.issym() or m.islnk() \
                    or not (m.isfile() or m.isdir()):
                raise ModuleError(_f("El paquete trae algo que no corresponde: {0}", m.name))
        tar.extractall(tmp, filter="data")
    src = tmp / mid
    try:
        man = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ModuleError(_("El paquete no trae su manifest.json.")) from e
    if man.get("id") != mid or not (src / "modulo" / "__init__.py").is_file():
        raise ModuleError(_("El paquete no es de este módulo."))
    dest = DIR / mid
    shutil.rmtree(dest, ignore_errors=True)
    src.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return man


def compatible(nucleo: str | None, core: str | None = None) -> bool:
    """La web entrega el paquete de la misma versión mayor.menor que no pase la del núcleo (2.45.0 para 2.45.3)."""
    def parts(v):
        try:
            return tuple(int(x) for x in (v or "").split(".")[:3])
        except ValueError:
            return ()
    n, c = parts(nucleo), parts(core or _core())
    return len(n) == 3 and len(c) == 3 and n[:2] == c[:2] and n <= c


def install(mid: str, data: bytes, signature: str, kid: str | None = None) -> dict:
    """Verifica la firma, guarda el paquete tal cual llegó y lo descomprime. Devuelve el manifest."""
    if mid not in licencia.MODULES:
        raise ModuleError(_f("Módulo desconocido: {0}", mid))
    if len(data) > MAX_SIZE:
        raise ModuleError(_("El paquete es demasiado grande."))
    verify_package(data, signature, kid)
    DIR.mkdir(parents=True, exist_ok=True)
    man = _extract(mid, data)
    (DIR / f"{mid}.tar.gz").write_bytes(data)
    sig = signature.strip()
    if kid and not sig.startswith("OPNC1."):
        sig = f"OPNC1.{kid}.{sig}"  # se guarda con su kid, para volver a comprobarla al arrancar
    (DIR / f"{mid}.tar.gz.firma").write_text(sig, encoding="utf-8")
    PROBLEMS.pop(mid, None)
    log.info("Módulo Pro %s %s instalado (para OpenNCCM %s)", mid, man.get("version"), man.get("nucleo"))
    return man


def download(mid: str) -> dict:
    """Baja el módulo para esta versión de OpenNCCM con la clave guardada y lo instala."""
    import requests
    key = get_setting("licencia_clave", "")
    if not key:
        raise ModuleError(_("No hay una clave de licencia."))
    try:
        r = requests.post(licencia.LICENSE_URL + "/api/modulos/descargar", timeout=60,
                          json={"clave": key, "instalacion": licencia.install_id(), "modulo": mid, "version": _core()})
    except requests.RequestException as e:
        raise ModuleError(_f("No se pudo conectar con {0}: {1}", licencia.LICENSE_URL, str(e).splitlines()[0][:160])) from e
    msgs = {403: _("La licencia no incluye este módulo o ya venció."),
            404: _("Todavía no hay una versión de este módulo para esta versión de OpenNCCM."),
            409: _("Esta clave se liberó desde Mi cuenta; vuelve a activarla."),
            410: _("La licencia fue revocada."), 429: _("Demasiadas descargas. Pruebe en una hora.")}
    if r.status_code in msgs:
        raise ModuleError(msgs[r.status_code])
    if r.status_code != 200:
        raise ModuleError(_f("comunidad.opennccm.com respondió {0}. Pruebe más tarde.", r.status_code))
    return install(mid, r.content, r.headers.get("X-OPNC-Firma", ""), r.headers.get("X-OPNC-Kid") or None)


def installed(mid: str) -> dict | None:
    try:
        return json.loads((DIR / mid / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def info(mid: str) -> dict:
    """Para Licencia y módulos: versión instalada, si está cargado y el problema, si lo hay."""
    man = installed(mid) or {}
    return {"version": man.get("version"), "nucleo": man.get("nucleo"), "loaded": mid in LOADED,
            "problem": PROBLEMS.get(mid, ""), "outdated": bool(man) and not compatible(man.get("nucleo"))}


def _check_stored(mid: str) -> None:
    pkg, sig = DIR / f"{mid}.tar.gz", DIR / f"{mid}.tar.gz.firma"
    if not pkg.is_file() or not sig.is_file():
        raise ModuleError(_("Falta el paquete firmado del módulo."))
    verify_package(pkg.read_bytes(), sig.read_text(encoding="utf-8"))


def load(app, mid: str) -> bool:
    """Carga un módulo instalado si la licencia lo incluye, su firma sigue bien y es para esta versión."""
    if mid in LOADED:
        return True
    man = installed(mid)
    if not man:
        return False
    if licencia.module(mid)["state"] == "no_incluido":
        return False
    try:
        if not compatible(man.get("nucleo")):
            raise ModuleError(_f("El módulo instalado es para OpenNCCM {0}; se baja el de esta versión.", man.get("nucleo")))
        _check_stored(mid)
        name = f"opennccm_pro_{mid}"
        spec = importlib.util.spec_from_file_location(name, DIR / mid / "modulo" / "__init__.py",
                                                      submodule_search_locations=[str(DIR / mid / "modulo")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        mod.register(app, context())
    except Exception as e:  # noqa: BLE001  (un módulo que falla nunca tumba OpenNCCM)
        PROBLEMS[mid] = str(e)[:300]
        log.warning("Módulo Pro %s no se cargó: %s", mid, e)
        return False
    LOADED[mid] = mod
    PROBLEMS.pop(mid, None)
    log.info("Módulo Pro %s %s cargado", mid, man.get("version"))
    return True


def context() -> dict:
    """Lo que el núcleo le ofrece a un módulo."""
    from . import scheduler
    from .web import render
    return {"licencia": licencia, "scheduler": scheduler.scheduler, "render": render, "data_dir": DIR, "core": _core()}


def load_all(app) -> None:
    if not DIR.is_dir():
        return
    for mid in licencia.MODULES:
        load(app, mid)


def page(mid: str):
    """La función del módulo cargado que dibuja su página (o None si no está cargado)."""
    mod = LOADED.get(mid)
    return getattr(mod, "page", None) if mod else None


def sync(app=None) -> list[str]:
    """Tarea diaria y botón «Actualizar»: baja los módulos de la licencia que falten o sean de otra versión."""
    done = []
    for mid in licencia.MODULES:
        if licencia.module(mid)["state"] not in ("activo", "gracia"):
            continue
        man = installed(mid)
        if man and compatible(man.get("nucleo")):
            continue
        try:
            download(mid)
            done.append(mid)
            if app is not None:
                load(app, mid)
        except ModuleError as e:
            PROBLEMS[mid] = str(e)
    return done
