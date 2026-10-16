"""Generación de comandos de reversión (current -> target) según la familia de sintaxis.

No es un "config replace" del fabricante: calcula las diferencias y emite los
comandos de negación/adición necesarios. Siempre se muestran al usuario dentro
de una solicitud de cambio antes de ejecutarse (salvo la reversión automática).
"""
import re

from .drivers import FASTPATH, HUAWEI, IOS, MERGE, SET

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


# ------------------------------------------------------------------ Broadcom FastPath (Ubiquiti EdgeSwitch)
# La configuración no usa sangría: los submodos se abren con una línea conocida y se cierran con «exit».
# Tiene tres ámbitos: modo privilegiado (network parms...), «vlan database» y «configure».
FP_OPENER = re.compile(
    r"^(interface |line |router |ipv6 router |class-map |policy-map |class |ip dhcp pool |ipv6 dhcp pool |"
    r"spanning-tree mst configuration|ip access-list |ipv6 access-list |mac access-list |"
    r"management access-list |ip vrf |vrf |aaa |radius server |tacacs-server host |monitor session )")
# administración del propio switch: cambiarla en una reversión cortaría la conexión
FP_MGMT = re.compile(r"^(network (parms|protocol|mgmt_vlan|ipv6)|serviceport )")
# ajustes de un solo valor: la línea nueva reemplaza a la anterior sin borrarla primero
FP_SINGLE = [("description",), ("snmp-server", "sysname"), ("snmp-server", "location"), ("snmp-server", "contact"),
             ("hostname",), ("vlan", "pvid"), ("mtu",), ("speed",), ("clock", "timezone"), ("set", "prompt")]


def _fp_key(line: str):
    t = line.split()
    for pref in FP_SINGLE:
        if tuple(t[:len(pref)]) == pref:
            return pref
    if t[:1] == ["username"] and len(t) > 1:
        return ("username", t[1])
    if t[:2] == ["vlan", "name"] and len(t) > 2:
        return ("vlan", "name", t[2])
    if t[:2] == ["vlan", "participation"] and len(t) > 3:  # include/exclude/auto de una misma VLAN
        return ("vlan", "participation", t[3])
    return None


def _fp_negate(line: str) -> str:
    t = line.split()
    if t[:1] == ["no"]:
        return line[3:].strip()
    if t[:2] == ["vlan", "participation"] and len(t) > 3 and t[2] in ("include", "exclude"):
        return f"vlan participation auto {t[3]}"
    if t[:1] == ["username"] and len(t) > 1:
        return f"no username {t[1]}"
    if t[:2] == ["vlan", "name"] and len(t) > 2:
        return f"no vlan name {t[2]}"
    for pref in FP_SINGLE:
        if tuple(t[:len(pref)]) == pref:
            return "no " + " ".join(pref)
    return "no " + line


def _fp_parse(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines()]
    cfg_start = next((i for i, l in enumerate(lines) if l == "configure"), len(lines))
    last_exit = max((i for i, l in enumerate(lines) if l == "exit" and i > cfg_start), default=len(lines))
    res: dict = {"exec": [], "vlan": [], "top": [], "blocks": []}
    section, stack, run = "exec", [], []
    for i, s in enumerate(lines):
        if not s or s.startswith("!"):
            run = []
            continue
        if section == "exec":
            if s == "vlan database":
                section = "vlan"
            elif s == "configure":
                section = "config"
            else:
                res["exec"].append(s)
        elif section == "vlan":
            if s == "exit":
                section = "exec"
            else:
                res["vlan"].append(s)
        elif section == "config":
            if s == "exit":
                if len(stack) > 1:
                    res["blocks"][-1][1].append(s)
                    stack.pop()
                elif stack:
                    stack.pop()
                elif i < last_exit and run:
                    # submodo desconocido: lo abre la primera línea desde el último corte
                    del res["top"][-len(run):]
                    res["blocks"].append((run[0], run[1:]))
                elif i >= last_exit:
                    section = "done"
                run = []
            elif stack:
                res["blocks"][-1][1].append(s)
                if FP_OPENER.match(s):
                    stack.append(s)
            elif FP_OPENER.match(s):
                stack = [s]
                res["blocks"].append((s, []))
                run = []
            else:
                res["top"].append(s)
                run.append(s)
    return res


def _fp_flat(cur: list[str], tgt: list[str]) -> list[str]:
    removed = [l for l in cur if l not in tgt]
    added = [l for l in tgt if l not in cur]
    added_keys = {_fp_key(a) for a in added} - {None}
    return [_fp_negate(r) for r in removed if _fp_key(r) not in added_keys] + added


def _vlan_ids(spec: str) -> set[int]:
    ids: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            ids.add(int(part))
    return ids


def _vlan_spec(ids: set[int]) -> str:
    out, s, i = [], sorted(ids), 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append(str(s[i]) if i == j else f"{s[i]}-{s[j]}")
        i = j + 1
    return ",".join(out)


def _fp_vlans(cur: list[str], tgt: list[str]) -> list[str]:
    def split(lines):
        ids, rest = set(), []
        for l in lines:
            m = re.fullmatch(r"vlan ([\d,\-]+)", l)
            if m:
                ids |= _vlan_ids(m.group(1))
            else:
                rest.append(l)
        return ids, rest
    cur_ids, cur_rest = split(cur)
    tgt_ids, tgt_rest = split(tgt)
    out = [f"vlan {_vlan_spec(tgt_ids - cur_ids)}"] if tgt_ids - cur_ids else []
    out += _fp_flat(cur_rest, tgt_rest)
    if cur_ids - tgt_ids:
        out.append(f"no vlan {_vlan_spec(cur_ids - tgt_ids)}")
    return out


def _fastpath(current: str, target: str) -> list[str]:
    cur, tgt = _fp_parse(current), _fp_parse(target)
    exec_cmds = _fp_flat([l for l in cur["exec"] if not FP_MGMT.match(l)],
                         [l for l in tgt["exec"] if not FP_MGMT.match(l)])
    vlan_cmds = _fp_vlans(cur["vlan"], tgt["vlan"])
    cfg = _fp_flat(cur["top"], tgt["top"])
    cur_b, tgt_b = dict(cur["blocks"]), dict(tgt["blocks"])
    for header, children in cur["blocks"]:
        if header in tgt_b:
            continue
        if re.match(r"(interface|line) ", header):  # los puertos no se borran: vuelven a su valor por omisión
            cfg += [header] + [_fp_negate(c) for c in children if c != "exit"] + ["exit"]
        else:
            cfg.append("no " + header)
    for header, children in tgt["blocks"]:
        old = cur_b.get(header)
        if old is None:
            cfg += [header] + children + ["exit"]
        elif old != children:
            if any(FP_OPENER.match(c) for c in old + children):  # submodos anidados: se recrea el bloque
                cfg += ["no " + header, header] + children + ["exit"]
            else:
                cfg += [header] + _fp_flat(old, children) + ["exit"]
    out: list[str] = []
    if exec_cmds or vlan_cmds:
        out.append("exit")  # al aplicar se entra en «configure»; estos comandos son del modo privilegiado
        out += exec_cmds
        if vlan_cmds:
            out += ["vlan database"] + vlan_cmds + ["exit"]
        out.append("configure")
    return out + cfg


def build_rollback(current: str, target: str, family: str) -> list[str]:
    if family == IOS:
        return _hierarchical(current, target, "no ", " exit")
    if family == HUAWEI:
        return _hierarchical(current, target, "undo ", " quit")
    if family == SET:
        return _set_style(current, target)
    if family == FASTPATH:
        return _fastpath(current, target)
    # MERGE: re-aplica la configuración destino completa
    return [l for l in target.splitlines() if l.strip() and not SKIP.match(l)]


__all__ = ["build_rollback", "IOS", "HUAWEI", "SET", "MERGE", "FASTPATH"]
