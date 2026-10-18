"""Aviso de versiones nuevas y actualización desde la web.

La búsqueda consulta la última versión publicada en GitHub (una vez al día, desactivable en Ajustes).
La actualización la ejecuta systemd como root (opennccm-webupdate.path) cuando la aplicación crea
update.request: es el mismo «opennccm update» de la consola, con copia previa y vuelta atrás automática.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

import requests

from . import __version__
from .config import UPDATE_LOG, UPDATE_REPO, UPDATE_REQUEST
from .db import get_setting, set_setting

log = logging.getLogger("opennccm")
WEBUPDATE_UNIT = Path("/etc/systemd/system/opennccm-webupdate.path")


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


def check_latest(force: bool = False) -> dict:
    """Consulta la última versión publicada. Devuelve el estado guardado (también si no hay red)."""
    if not force and get_setting("update_check", "1") == "0":
        return status()
    try:
        r = requests.get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", timeout=10,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"OpenNCCM/{__version__}"})
        r.raise_for_status()
        data = r.json()
        set_setting("update_latest", (data.get("tag_name") or "").lstrip("v"))
        set_setting("update_notes", (data.get("body") or "")[:6000])
        set_setting("update_url", data.get("html_url") or "")
        set_setting("update_error", "")
    except Exception as e:  # noqa: BLE001 - sin internet no debe fallar nada más
        set_setting("update_error", f"No se pudo consultar GitHub: {e.__class__.__name__}")
        log.info("Búsqueda de actualizaciones: %s", e)
    set_setting("update_checked_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return status()


def status() -> dict:
    latest = get_setting("update_latest", "")
    return {
        "current": __version__,
        "latest": latest,
        "available": bool(latest) and version_tuple(latest) > version_tuple(__version__),
        "notes": get_setting("update_notes", ""),
        "url": get_setting("update_url", ""),
        "checked_at": get_setting("update_checked_at", ""),
        "error": get_setting("update_error", ""),
        "web_update": WEBUPDATE_UNIT.exists(),
        "running": UPDATE_REQUEST.exists() or _log_running(),
    }


def _log_running() -> bool:
    """El servicio de actualización escribe «FIN=<código>» al terminar."""
    try:
        text = UPDATE_LOG.read_text(errors="replace")
    except OSError:
        return False
    recent = time.time() - UPDATE_LOG.stat().st_mtime < 1800
    return recent and "FIN=" not in text


def request_update() -> None:
    UPDATE_REQUEST.write_text(datetime.now().isoformat())


def changelog_sections(limit: int = 6) -> list[tuple[str, str]]:
    """(versión, texto) de las últimas versiones según el CHANGELOG incluido en la instalación."""
    path = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    parts = re.split(r"(?m)^## ", text)[1:]
    out = []
    for part in parts[:limit]:
        version, _, body = part.partition("\n")
        out.append((version.strip(), body.strip()))
    return out


def md_lite(text: str) -> str:
    """Markdown mínimo (viñetas, negrita, código) a HTML seguro, para mostrar notas de versión."""
    from html import escape
    html, in_list = [], False
    for raw in text.splitlines():
        line = escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        stripped = line.lstrip()
        if stripped.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            depth = (len(line) - len(stripped)) // 2
            html.append(f'<li style="margin-left:{depth * 18}px">{stripped[2:]}</li>')
        elif in_list and stripped and html[-1].startswith("<li"):  # continuación de la viñeta anterior
            html[-1] = html[-1][:-5] + " " + stripped + "</li>"
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            if stripped.startswith("#"):
                html.append(f"<h4>{stripped.lstrip('#').strip()}</h4>")
            elif stripped:
                html.append(f"<p>{stripped}</p>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def log_tail(lines: int = 40) -> str:
    try:
        text = UPDATE_LOG.read_text(errors="replace")
    except OSError:
        return ""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)  # sin colores de la consola
    return "\n".join(text.splitlines()[-lines:])
