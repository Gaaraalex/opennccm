"""Logos de marca de los equipos (diseño de Diseño UX, app/vendor_logos.json y static/vendor-logos/*.svg).

Cada perfil de app/drivers.py tiene la clave de su logo («extreme_vsp» → «extreme»). Se dibujan desde un solo archivo,
static/vendor-logos.svg (un símbolo «vl-<clave>» por marca, armado con scripts/build_vendor_logos.py), que el navegador
guarda en caché. Una marca o un perfil que no está en la tabla usa «other».
"""
from __future__ import annotations

import json
from pathlib import Path

from markupsafe import Markup, escape

from . import __version__

_DATA = json.loads((Path(__file__).parent / "vendor_logos.json").read_text(encoding="utf-8"))
BRANDS: dict[str, dict] = _DATA["marcas"]
PROFILES: dict[str, str] = _DATA["perfiles"]
SPRITE = f"/static/vendor-logos.svg?v={__version__}"
# marcas cuyo logo es su nombre escrito: por debajo de 40 px se usa su versión chica («vl-<clave>-chico»)
SMALL: list[str] = sorted(k for k, b in BRANDS.items() if b.get("archivo_chico"))
SMALL_BELOW = 40


def small_on() -> bool:
    """Administración › Ajustes › «Logos de marca abreviados en tamaño chico» (apagado por omisión)."""
    from .auth import cached_setting
    return cached_setting("logos_small", "0") == "1"


def small_keys() -> list[str]:
    """Las marcas que usan la versión chica ahora (ninguna con el ajuste apagado), para el JavaScript y el mapa."""
    return SMALL if small_on() else []


def symbol(k: str, size: int) -> str:
    """Id del símbolo para ese tamaño: con el ajuste encendido, la versión chica por debajo de 40 px si la marca la tiene."""
    k = k if k in BRANDS else "other"
    return f"vl-{k}-chico" if size < SMALL_BELOW and k in SMALL and small_on() else f"vl-{k}"
# familias del mapa de la red (topology.VENDOR_FAMILY / family()) → logo
FAMILY = {"mikrotik": "mikrotik", "cisco": "cisco", "ubiquiti": "ubiquiti", "juniper": "juniper", "huawei": "huawei",
          "aruba": "aruba", "extreme": "extreme", "other": "other"}


def key(device_type: str | None) -> str:
    """Clave del logo de un perfil de equipo («other» si no se conoce)."""
    return PROFILES.get(device_type or "", "other")


def name(k: str) -> str:
    """Nombre visible de la marca de un logo («Extreme», «MikroTik»…)."""
    b = BRANDS.get(k)
    return b["nombre"] if b else "—"


def brand(device_type: str | None) -> str:
    return name(key(device_type))


def svg(k: str, size: int = 30, cls: str = "", label: str = "") -> Markup:
    """El logo de una clave, del tamaño pedido (nunca menos de 14 px). Decorativo salvo que se pase label."""
    a11y = f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"'
    s = max(14, int(size))
    return Markup(f'<svg class="vlg {escape(cls)}" width="{s}" height="{s}" viewBox="0 0 24 24" {a11y} focusable="false">'
                  f'<use href="{SPRITE}#{escape(symbol(k, s))}"/></svg>')


def logo(device_type: str | None, size: int = 30, cls: str = "", label: str = "") -> Markup:
    """Logo de la marca de un equipo por su perfil (device_type)."""
    return svg(key(device_type), size, cls, label)
