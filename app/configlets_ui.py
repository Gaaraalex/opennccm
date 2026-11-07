"""Datos de la página Configlets y scripts en tres columnas (como Recetas): a qué plataformas sirve cada uno, en
palabras y no con la regex del filtro, cuántos equipos tuyos lo pueden ejecutar y su sello «Verificada»."""
from __future__ import annotations

import re

from . import consolecore as cc
from . import verified, vendorlogos
from .drivers import PROFILES
from .i18n import _


def _rx(flt: str | None):
    if not flt:
        return None
    try:
        return re.compile(flt)
    except re.error:
        return re.compile(r"(?!x)x")  # filtro roto: no sirve para ningún tipo


def short_name(name: str) -> str:
    """«Agregar VLAN (Huawei)» → «Agregar VLAN»: la plataforma va debajo del nombre."""
    return re.sub(r"\s+\([^()]{2,40}\)$", "", name or "") or name


def serves(flt: str | None) -> tuple[list[str], list]:
    """(familias de comandos, perfiles) a los que sirve un filtro de tipo de equipo; sin filtro, a todos."""
    rx = _rx(flt)
    profs = [p for p in PROFILES if p.key != "generic" and (rx is None or rx.search(p.key))]
    fams = []
    for p in profs:
        o = cc.os_of(p.key)
        if o not in fams:
            fams.append(o)
    return fams, profs


def serves_text(flt: str | None, profs: list) -> str:
    """«Sirve para» en palabras: «Cisco IOS / Cisco IOS-XE», «Huawei VRP y 3 más» o «cualquier equipo»."""
    if not flt:
        return _("cualquier equipo")
    if not profs:
        return _("ningún tipo de equipo (revise el filtro)")
    labels = []
    for p in profs:
        lb = re.sub(r"\s*\(.*?\)\s*", " ", p.label).strip()
        if lb not in labels:
            labels.append(lb)
    return " / ".join(labels) if len(labels) <= 3 else _("{0} y {1} más").format(" / ".join(labels[:2]), len(labels) - 2)


def build(items, devs: list, allowed: set, uses: dict) -> dict:
    """Configlets con su plataforma, sus equipos compatibles y su sello; y las plataformas para la columna izquierda."""
    ver = verified.all_of("configlet")
    mine = [d for d in devs if d.id in allowed]
    dev_fam: dict[str, int] = {}
    for d in mine:
        o = cc.os_of(d.device_type)
        dev_fam[o] = dev_fam.get(o, 0) + 1
    logo_of_fam: dict[str, str] = {}
    for p in PROFILES:
        logo_of_fam.setdefault(cc.os_of(p.key), vendorlogos.key(p.key))
    out, plats = [], {}
    for c in items:
        fams, profs = serves(c.device_type_filter)
        rx = _rx(c.device_type_filter)
        compat = [d for d in mine if rx is None or rx.search(d.device_type)]
        first = profs[0].key if profs else ""
        out.append({"id": c.id, "name": c.name, "short": short_name(c.name), "desc": c.description or "", "kind": c.kind or "config",
                    "body": c.body or "", "filter": c.device_type_filter or "", "fams": fams if c.device_type_filter else ["*"],
                    "serves": serves_text(c.device_type_filter, profs), "compat": len(compat), "uses": uses.get(c.id, 0),
                    "verified_on": ver.get(str(c.id), ""), "vl": vendorlogos.key(compat[0].device_type) if compat else vendorlogos.key(first) if first else "other"})
        for o in (fams if c.device_type_filter else []):
            p = plats.setdefault(o, {"os": o, "label": cc.OS_LABEL.get(o) or _("Genérico"), "vl": logo_of_fam.get(o, "other"), "devs": dev_fam.get(o, 0)})
            p["n"] = p.get("n", 0) + 1
    for o, n in dev_fam.items():  # plataformas de tu inventario aunque no tengan configlets propios (los «para cualquier equipo» sí)
        plats.setdefault(o, {"os": o, "label": cc.OS_LABEL.get(o) or _("Genérico"), "vl": logo_of_fam.get(o, "other"), "devs": n, "n": 0})
    return {"items": out, "plats": sorted(plats.values(), key=lambda p: (-p["devs"], p["label"].lower()))}
