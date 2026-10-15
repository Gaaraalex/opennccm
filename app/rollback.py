"""Generación de comandos de reversión (current -> target) según la familia de sintaxis.

No es un "config replace" del fabricante: calcula las diferencias y emite los
comandos de negación/adición necesarios. Siempre se muestran al usuario dentro
de una solicitud de cambio antes de ejecutarse (salvo la reversión automática).
"""
import re

from .drivers import HUAWEI, IOS, MERGE, SET

SKIP = re.compile(
    r"^\s*($|!|#|end$|return$|Building configuration|Current configuration|version \S+$|exit$|quit$)")


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if SKIP.match(line):
            continue
        if line[:1] in (" ", "\t") and blocks:
            blocks[-1][1].append(line)
        elif line[:1] not in (" ", "\t"):
            blocks.append((line.rstrip(), []))
    return blocks


def _negate(line: str, neg: str) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if stripped.startswith(neg):
        return indent + stripped[len(neg):]
    return indent + neg + stripped


def _hierarchical(current: str, target: str, neg: str, exit_cmd: str) -> list[str]:
    cur = dict(_blocks(current))
    tgt = _blocks(target)
    tgt_map = dict(tgt)
    out: list[str] = []
    # 1) eliminar lo que sobra
    for header, children in cur.items():
        if header not in tgt_map:
            out.append(_negate(header, neg))
    # 2) agregar / ajustar lo que falta
    for header, children in tgt:
        if header not in cur:
            out.append(header)
            out.extend(children)
            if children:
                out.append(exit_cmd)
            continue
        cur_children = cur[header]
        removed = [c for c in cur_children if c not in children]
        added = [c for c in children if c not in cur_children]
        if removed or added:
            out.append(header)
            out.extend(_negate(c, neg) for c in removed)
            out.extend(added)
            out.append(exit_cmd)
    return out


def _set_style(current: str, target: str) -> list[str]:
    cur = {l.strip() for l in current.splitlines() if l.strip().startswith("set ")}
    tgt = [l.strip() for l in target.splitlines() if l.strip().startswith("set ")]
    tgt_set = set(tgt)
    out = ["delete " + l[4:] for l in sorted(cur - tgt_set)]
    out += [l for l in tgt if l not in cur]
    return out


def build_rollback(current: str, target: str, family: str) -> list[str]:
    if family == IOS:
        return _hierarchical(current, target, "no ", " exit")
    if family == HUAWEI:
        return _hierarchical(current, target, "undo ", " quit")
    if family == SET:
        return _set_style(current, target)
    # MERGE: re-aplica la configuración destino completa
    return [l for l in target.splitlines() if l.strip() and not SKIP.match(l)]


__all__ = ["build_rollback", "IOS", "HUAWEI", "SET", "MERGE"]
