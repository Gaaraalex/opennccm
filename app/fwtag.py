"""Versión de firmware de cada copia de seguridad (ConfigVersion.fw_version).

Se lee en el mismo respaldo y en la misma conexión: del inventario del perfil (show version, /system resource print…)
o, si no lo da, de la propia configuración («! System Version» del EdgeSwitch, la cabecera de RouterOS, «version …» de
Cisco y Junos, «#config-version» de FortiGate…). Se guarda con esa copia y no se recalcula: si el equipo se actualiza,
las copias viejas siguen con su versión. Si no se puede leer, la copia queda «sin registrar» (None).
"""
from __future__ import annotations

import re

# versión escrita en la configuración: (expresión, grupo) por orden de preferencia
CONFIG_RE = [
    re.compile(r"(?m)^!\s*System Version:\s*v?(\S+)"),                       # Ubiquiti EdgeSwitch / FastPath
    re.compile(r"(?m)^#.*\bby RouterOS\s+(\S+)"),                            # MikroTik «# 2026-… by RouterOS 7.20.4»
    re.compile(r"(?m)^#\s*software id = .*\n#.*?RouterOS\s+(\S+)"),
    re.compile(r"(?m)^version\s+([\w.()\-]+);?\s*$"),                         # Cisco IOS «version 15.2» y Junos «version 22.4R2;»
    re.compile(r"(?m)^#config-version=[\w]+-([\d.]+)-"),                     # FortiGate «#config-version=FGT60F-7.2.8-FW-…»
    re.compile(r"(?mi)^!\s*(?:Software|Firmware)\s+Version[:\s]+v?(\S+)"),    # otras cabeceras «! Software version …»
    re.compile(r"(?mi)^#\s*(?:Software|Firmware)\s+Version[:\s]+v?(\S+)"),
]
# líneas que suelen cambiar con una actualización (para «por el firmware» en la comparación)
FW_WORDS = re.compile(r"(?i)\b(version|firmware|software|build|boot|image|release)\b")


def config_fw(text: str | None) -> str | None:
    for rx in CONFIG_RE:
        m = rx.search(text or "")
        if m:
            return m.group(1).strip()[:64]
    return None


def norm(v: str | None) -> str:
    return (v or "").strip().lstrip("vV")


def same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and norm(a) == norm(b)


def label(v: str | None) -> str:
    """«v1.11.1» (o el texto tal cual si ya trae la v)."""
    v = (v or "").strip()
    return v if not v or v[:1] in "vV" else "v" + v


def source_line(text: str, fw: str | None) -> int | None:
    """Número de línea (1…) de donde sale la versión en la configuración, para resaltarla."""
    if not fw:
        return None
    lines = (text or "").splitlines()
    # lo que dice la propia configuración («version 15.2» para un 15.2(4)M7), si es parte de esa versión
    cfg = config_fw(text)
    keys = [norm(fw)] + ([norm(cfg)] if cfg and (norm(fw).startswith(norm(cfg)) or norm(cfg).startswith(norm(fw))) else [])
    for k in keys:
        for i, line in enumerate(lines, 1):
            if k and k in line and (FW_WORDS.search(line) or "RouterOS" in line):
                return i
    return None


def mark_rows(rows: list[tuple], fwa: str | None, fwb: str | None) -> set[int]:
    """Filas de la comparación (side_by_side) que trajo la actualización: solo si A y B tienen firmware distinto."""
    if not fwa or not fwb or same(fwa, fwb):
        return set()
    va, vb = norm(fwa), norm(fwb)
    out = set()
    for i, r in enumerate(rows):
        if r[0] not in "cda":
            continue
        t = f"{r[2] or ''} {r[4] or ''}"
        if (va and va in t) or (vb and vb in t) or FW_WORDS.search(t):
            out.add(i)
    return out


def file_tag(profile_label: str, vendor: str, fw: str | None) -> str:
    """«EdgeSwitch-1.11.1» para el nombre del archivo descargado (vacío si no hay versión)."""
    if not fw:
        return ""
    words = [w for w in re.sub(r"\(.*?\)", "", profile_label or "").split() if w.lower() != (vendor or "").lower()]
    plat = re.sub(r"[^\w.-]+", "", "-".join(words[-1:])) or "fw"
    ver = re.sub(r"[^\w.-]+", "", norm(fw))  # (fuera del f-string: Python 3.10 no admite «\» en sus expresiones)
    return f"{plat}-{ver}"
