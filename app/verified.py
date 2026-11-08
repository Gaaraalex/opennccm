"""Sello «Verificada» de los configlets y de las correcciones de cumplimiento: la versión del sistema en la que se
probaron todos sus comandos. Igual que en las recetas (app.recipes.verified_on): lo pone o lo quita un administrador
y queda en Auditoría. Se guarda en un ajuste por tipo («configlet_verified», «rule_verified») como {id: versión}."""
from __future__ import annotations

import json
import re

from .db import get_setting, set_setting

KINDS = {"configlet": "configlet_verified", "rule": "rule_verified"}


# Correcciones de cumplimiento probadas enteras en un equipo del laboratorio: (política, regla) → versión del sistema.
# Solo se agrega una cuando su corrección se aplicó y la regla pasó a cumplir en esa versión, tres rondas seguidas.
RULES = {
    # MikroTik RouterOS 6.49.23 y 7.24.5 (CHR en GNS3), 3 rondas seguidas sin fallos, 2026-11-07
    ("MikroTik RouterOS · Seguridad básica", "Acceso por MAC (MAC-Telnet/MAC-Winbox) limitado"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Descubrimiento de vecinos limitado"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "SSH con cifrado fuerte"): "RouterOS 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Sin API sin cifrar"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Sin FTP"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Sin Telnet"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Sin servidor de bandwidth-test"): "RouterOS 6.49.23 / 7.24.5",
    ("MikroTik RouterOS · Seguridad básica", "Sin web HTTP (www)"): "RouterOS 6.49.23 / 7.24.5",
}


def _overrides(kind: str) -> dict:
    try:
        d = json.loads(get_setting(KINDS[kind], "{}") or "{}")
    except ValueError:
        return {}
    return {str(k): str(v or "") for k, v in d.items()} if isinstance(d, dict) else {}


def _builtin_rules() -> dict[str, str]:
    if not RULES:
        return {}
    from .db import Policy, Rule, SessionLocal, select
    with SessionLocal() as db:
        return {str(r.id): RULES[(p.name, r.name)] for r, p in db.execute(select(Rule, Policy).join(Policy, Rule.policy_id == Policy.id))
                if (p.name, r.name) in RULES}


def all_of(kind: str) -> dict[str, str]:
    """{id: versión} con los sellos de fábrica (correcciones) y los cambios del administrador («» quita uno)."""
    out = _builtin_rules() if kind == "rule" else {}
    out.update(_overrides(kind))
    return {k: v for k, v in out.items() if v}


def get(kind: str, oid: int) -> str:
    return all_of(kind).get(str(oid), "")


def put(kind: str, oid: int, version: str) -> str:
    """Pone el sello con esa versión (vacía: lo quita). Devuelve la versión guardada."""
    v = re.sub(r"\s+", " ", str(version or "")).strip()[:60]
    d = _overrides(kind)
    d[str(oid)] = v  # vacío: quitado (también el sello de fábrica)
    set_setting(KINDS[kind], json.dumps(d, ensure_ascii=False))
    return v


def ver_num(s: str) -> str:
    """«VOSS 9.3.0.0» y «9.3.0.0» son la misma versión."""
    m = re.search(r"\d+(?:\.\d+)+", s or "")
    return m.group(0) if m else (s or "").strip()
