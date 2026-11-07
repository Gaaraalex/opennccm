"""Sello «Verificada» de los configlets y de las correcciones de cumplimiento: la versión del sistema en la que se
probaron todos sus comandos. Igual que en las recetas (app.recipes.verified_on): lo pone o lo quita un administrador
y queda en Auditoría. Se guarda en un ajuste por tipo («configlet_verified», «rule_verified») como {id: versión}."""
from __future__ import annotations

import json
import re

from .db import get_setting, set_setting

KINDS = {"configlet": "configlet_verified", "rule": "rule_verified"}


def all_of(kind: str) -> dict[str, str]:
    try:
        d = json.loads(get_setting(KINDS[kind], "{}") or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in d.items() if v} if isinstance(d, dict) else {}


def get(kind: str, oid: int) -> str:
    return all_of(kind).get(str(oid), "")


def put(kind: str, oid: int, version: str) -> str:
    """Pone el sello con esa versión (vacía: lo quita). Devuelve la versión guardada."""
    v = re.sub(r"\s+", " ", str(version or "")).strip()[:60]
    d = all_of(kind)
    if v:
        d[str(oid)] = v
    else:
        d.pop(str(oid), None)
    set_setting(KINDS[kind], json.dumps(d, ensure_ascii=False))
    return v


def ver_num(s: str) -> str:
    """«VOSS 9.3.0.0» y «9.3.0.0» son la misma versión."""
    m = re.search(r"\d+(?:\.\d+)+", s or "")
    return m.group(0) if m else (s or "").strip()
