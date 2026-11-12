"""Generación de comandos de reversión (current -> target) según la familia de sintaxis.

No es un "config replace" del fabricante: calcula las diferencias y emite los
comandos de negación/adición necesarios. Siempre se muestran al usuario dentro
de una solicitud de cambio antes de ejecutarse (salvo la reversión automática).
"""
import re

from .drivers import FASTPATH, HUAWEI, IOS, MERGE, SET
from .i18n import N_, _, _f

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


# Ajustes de un solo valor (Cisco y clones, Huawei/H3C): la línea nueva reemplaza a la anterior. Negarla primero
# dejaría el equipo un instante sin ese valor; con «no ip address» en la interfaz de gestión se corta la conexión.
H_SINGLE = sorted((tuple(s.split()) for s in (
    "description", "hostname", "sysname", "ip address", "switchport access vlan", "switchport trunk allowed vlan",
    "switchport trunk native vlan", "switchport mode", "switchport voice vlan", "speed", "duplex", "mtu", "ip mtu",
    "bandwidth", "ip default-gateway", "snmp-server location", "snmp-server contact", "clock timezone",
    "ip domain-name", "ip domain name", "spanning-tree mode", "enable secret", "enable password", "name",
    "port link-type", "port default vlan", "port trunk pvid vlan", "encapsulation dot1q", "router-id",
    "snmp-agent sys-info location", "snmp-agent sys-info contact")), key=len, reverse=True)
VLAN_HEADER = re.compile(r"^vlan \d")


def _h_key(line: str):
    t = line.split()
    if not t or t[-1] == "secondary":  # direcciones secundarias: puede haber varias
        return None
    for pref in H_SINGLE:
        if tuple(t[:len(pref)]) == pref:
            return pref
    if t[0] in ("username", "local-user") and len(t) > 1:
        return (t[0], t[1])
    return None


def _hw_ids(spec: list[str]) -> set[int]:
    """«10 20 30 to 40» (Huawei/H3C) -> {10, 20, 30, ..., 40}."""
    ids, i = set(), 0
    while i < len(spec):
        if i + 2 < len(spec) and spec[i + 1] == "to":
            ids.update(range(int(spec[i]), int(spec[i + 2]) + 1))
            i += 3
        else:
            if spec[i].isdigit():
                ids.add(int(spec[i]))
            i += 1
    return ids


def _hw_spec(ids: set[int]) -> str:
    out, s, i = [], sorted(ids), 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append(str(s[i]) if i == j else f"{s[i]} to {s[j]}")
        i = j + 1
    return " ".join(out)


# Lo propio de cada equipo Cisco, que no se copia ni se borra al restaurar (en otro equipo, o en el mismo tras borrarlo):
# su certificado autofirmado («crypto pki trustpoint/certificate chain TP-self-signed-<n.º>», borrarlo pide confirmar
# y se detenía la restauración), la licencia con su serie y el client-id de DHCP con la serie (CSR1000v en GNS3)
IOS_OWN = re.compile(r"^(crypto pki (trustpoint|certificate chain) TP-self-signed-|license udi )")
# lo que IOS no deja quitar: «no aaa new-model» responde «Changing configuration back to no aaa new-model is not
# supported. Continue?[confirm]» y la restauración se detenía ahí
IOS_KEEP = {"aaa new-model", "aaa session-id common"}  # (la segunda la pone IOS con la primera: «Cannot negate a default configuration»)
IOS_OWN_CHILD = re.compile(r"^\s*ip dhcp client client-id ascii \S+$")


def _hierarchical(current: str, target: str, neg: str, exit_cmd: str) -> list[str]:
    cur_list, tgt = _blocks(current), _blocks(target)
    if neg == "no ":
        # (tampoco «no aaa new-model» del destino: IOS lo muestra en la configuración cuando AAA está apagado)
        cur_list, tgt = ([(h, [c for c in ch if not IOS_OWN_CHILD.match(c)]) for h, ch in b
                          if not IOS_OWN.match(h) and h not in IOS_KEEP and h.removeprefix("no ") not in IOS_KEEP]
                         for b in (cur_list, tgt))
    pre: list[str] = []   # antes de todo: VLAN que hacen falta
    post: list[str] = []  # al final: VLAN que sobran (antes de eso los puertos todavía las usan)
    # Huawei/H3C: «vlan batch 10 20 30 to 40» se compara como conjunto
    batch = [(h, _hw_ids(h.split()[2:])) for h, _ in cur_list if h.startswith("vlan batch ")]
    tbatch = [(h, _hw_ids(h.split()[2:])) for h, _ in tgt if h.startswith("vlan batch ")]
    if batch or tbatch:
        cur_ids = set().union(*(i for _, i in batch)) if batch else set()
        tgt_ids = set().union(*(i for _, i in tbatch)) if tbatch else set()
        if tgt_ids - cur_ids:
            pre.append("vlan batch " + _hw_spec(tgt_ids - cur_ids))
        if cur_ids - tgt_ids:
            post.append(f"{neg}vlan batch " + _hw_spec(cur_ids - tgt_ids))
        cur_list = [b for b in cur_list if not b[0].startswith("vlan batch ")]
        tgt = [b for b in tgt if not b[0].startswith("vlan batch ")]
    cur = dict(cur_list)
    tgt_map = dict(tgt)
    out: list[str] = []
    # 1) eliminar lo que sobra (salvo lo que se reemplaza y las VLAN, que van al final)
    new_keys = {_h_key(h) for h in tgt_map if h not in cur} - {None}
    for header in cur:
        if header in tgt_map or _h_key(header) in new_keys or (neg == "no " and header in IOS_KEEP):
            continue
        (post if VLAN_HEADER.match(header) else out).append(_negate(header, neg))
    # 2) agregar / ajustar lo que falta
    for header, children in tgt:
        if header not in cur:
            target_list = pre if VLAN_HEADER.match(header) else out  # las VLAN nuevas se crean primero
            target_list.append(header)
            target_list.extend(children)
            if children:
                target_list.append(exit_cmd)
            continue
        cur_children = cur[header]
        removed = [c for c in cur_children if c not in children]
        added = [c for c in children if c not in cur_children]
        if removed or added:
            akeys = {_h_key(a) for a in added} - {None}
            out.append(header)
            # un «no ip address» que sobra se reemplaza con la «ip address …» que se agrega: negarlo daba «ip address»
            # sola («% Incomplete command», restaurar un CSR1000v en otro)
            out.extend(_negate(c, neg) for c in removed if _h_key(c) not in akeys and not (
                c.strip().startswith(neg) and any(a.strip().startswith(c.strip()[len(neg):]) for a in added)))
            out.extend(added)
            out.append(exit_cmd)
    return pre + out + post


# palabras de Junos/VyOS seguidas del nombre de un objeto
SET_NAMED = {"interfaces", "unit", "prefix-list", "user", "community", "filter", "term", "policy", "policy-statement",
             "security-zone", "address-set", "address", "group", "route", "host", "vlans", "neighbor", "server",
             "rule", "rule-set", "pool", "zone", "application", "address-book", "static-host-mapping"}
# instrucciones que existen solas y llevan opciones debajo: «family inet dhcp vendor-id X» se borra desde «dhcp»
# (borrar la línea entera quita solo «vendor-id» y deja «dhcp»: el vQFX de fábrica trae «dhcp vendor-id …» en cada
# puerto xe-, y al restaurar una copia con IP fija el commit fallaba con «Incompatible with interface assigned with
# address», visto en un vQFX 19.4R1.10)
SET_PRESENCE = {"dhcp", "dhcp-client", "dhcpv6-client"}


def set_deletes(extra: list[str], target: list[str]) -> list[str]:
    """«delete …» de las líneas «set …» que sobran, por el objeto entero cuando el destino no lo tiene: borrar solo
    «set policy-options prefix-list X 192.0.2.1/32» deja en Junos la lista X vacía («set policy-options prefix-list
    X»), y el equipo no vuelve a quedar igual. Se borra el objeto con nombre más corto («prefix-list X», «unit 0»,
    «user lectura», «route 10.0.0.0/8») que ninguna línea del destino usa; Junos no acepta cortar en la palabra clave
    sola («delete interfaces ge-0/0/9 unit»: syntax error). Si no hay uno, la línea entera, como antes."""
    tw = [t.split()[1:] for t in target]
    out: list[str] = []
    for line in sorted(extra):
        w = line.split()[1:]
        cut = w
        for n in range(1, len(w)):
            if ((n >= 2 and w[n - 2] in SET_NAMED) or w[n - 1] in SET_PRESENCE) and not any(t[:n] == w[:n] for t in tw):
                cut = w[:n]
                break
        d = "delete " + " ".join(cut)
        if not any(d == o or d.startswith(o + " ") for o in out):
            out = [o for o in out if not o.startswith(d + " ")] + [d]
    return out


def _set_mgmt_if(current: str, mgmt: str | None) -> str | None:
    """«set interfaces fxp0 unit 0 family inet address 10.0.0.242/24» con la IP por la que OpenNCCM gestiona el equipo
    -> «set interfaces fxp0» (también «unit 0» de una VLAN/irb: el prefijo hasta la unidad)."""
    if not mgmt:
        return None
    for l in current.splitlines():
        m = re.match(rf"\s*(set interfaces \S+(?: unit \S+)?) .*\baddress {re.escape(mgmt)}(/|\s|$)", l)
        if m:
            return m.group(1)
    return None


def _set_style(current: str, target: str, mgmt: str | None = None) -> list[str]:
    cur = {l.strip() for l in current.splitlines() if l.strip().startswith("set ")}
    tgt = [l.strip() for l in target.splitlines() if l.strip().startswith("set ")]
    # la interfaz de gestión del equipo no se toca: restaurar la copia de otro equipo (o una vieja) le ponía la IP de
    # gestión de esa copia y OpenNCCM lo perdía (vMX en GNS3: M-2 quedaba con la IP de M-1)
    own = _set_mgmt_if(current, mgmt)
    if own:
        cur = {l for l in cur if not (l == own or l.startswith(own + " "))}
        tgt = [l for l in tgt if not (l == own or l.startswith(own + " "))]
    tgt_set = set(tgt)
    out = set_deletes(sorted(cur - tgt_set), tgt)
    out += [l for l in tgt if l not in cur]
    return out


# ------------------------------------------------------------------ Broadcom FastPath (Ubiquiti EdgeSwitch)
# La configuración no usa sangría: los submodos se abren con una línea conocida y se cierran con «exit».
# EdgeSwitch tiene tres ámbitos: modo privilegiado (network parms...), «vlan database» y «configure». Otros equipos
# con la misma base (Dell N-series/OS6, Netgear, Broadcom ICOS, Quanta) no tienen «vlan database»: las VLAN son
# bloques «vlan 10 ... exit» dentro de la configuración. Se detecta por el estilo, sea cual sea el perfil.
FP_OPENER = re.compile(
    r"^(interface |line |router |ipv6 router |class-map |policy-map |class |ip dhcp pool |ipv6 dhcp pool |"
    r"spanning-tree mst configuration|ip access-list |ipv6 access-list |mac access-list |"
    r"management access-list |ip vrf |vrf |aaa |radius server |tacacs-server host |monitor session |vlan \d)")
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
    return _h_key(line)  # switchport access vlan, ip address... (Dell N-series, Netgear)


def _fp_negate(line: str) -> str:
    t = line.split()
    if t[:1] == ["no"]:
        return line[3:].strip()
    if t[:2] == ["vlan", "participation"] and len(t) > 3 and t[2] in ("include", "exclude", "auto"):
        # valor de fábrica: la VLAN 1 «include» y las demás «auto» («no vlan participation …» no existe)
        ids = _vlan_ids(t[3])
        if 1 in ids:
            rest = ids - {1}
            return "vlan participation include 1" + (f"\nvlan participation auto {_vlan_spec(rest)}" if rest else "")
        return f"vlan participation auto {t[3]}"
    if t[:1] == ["username"] and len(t) > 1:
        return f"no username {t[1]}"
    if t[:2] == ["vlan", "name"] and len(t) > 2:
        return f"no vlan name {t[2]}"
    if t[:2] == ["snmp-server", "community"] and len(t) > 2:  # «no snmp-server community NOMBRE» (sin «ro»/«rw»)
        return f"no snmp-server community {t[2]}"
    if t[:2] in (["snmp-server", "location"], ["snmp-server", "contact"]):
        # EdgeSwitch 1.9.3 no tiene cómo borrarlas: «no snmp-server location» da «Invalid input», vacía no se admite y
        # «""» guarda dos comillas. Solo se pueden cambiar por otro valor: no se manda nada
        return None
    for pref in FP_SINGLE:
        if tuple(t[:len(pref)]) == pref:
            return "no " + " ".join(pref)
    key = _h_key(line)
    if key and key[0] not in ("username", "local-user"):
        return "no " + " ".join(key)
    return "no " + line


def _fp_parse(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines()]
    cfg_start = next((i for i, l in enumerate(lines) if l == "configure"), None)
    if cfg_start is None:  # sin «configure» (Dell N-series, Netgear...): todo es configuración
        section, last_exit = "config", len(lines) + 1
    else:
        section = "exec"
        last_exit = max((i for i, l in enumerate(lines) if l == "exit" and i > cfg_start), default=len(lines))
    res: dict = {"exec": [], "vlan": [], "top": [], "blocks": []}
    stack, run = [], []
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
    # un servidor syslog se quita por su número («logging host remove N»; «no logging host "IP" …» da «Invalid input»
    # en un EdgeSwitch 1.9.3): el número es su orden en la configuración. De mayor a menor, para que no se corran.
    hosts = [l for l in cur if l.startswith("logging host ")]
    gone_hosts = sorted((hosts.index(r) + 1 for r in removed if r in hosts), reverse=True)
    neg = [_fp_negate(r) for r in removed if r not in hosts and _fp_key(r) not in added_keys]
    neg = [x for n in neg if n is not None for x in n.split("\n")]
    return [f"logging host remove {n}" for n in gone_hosts] + neg + added


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


def _fp_vlans(cur: list[str], tgt: list[str]) -> tuple[list[str], list[str], set[int]]:
    """Devuelve (comandos previos: crear VLAN y nombres, comandos finales: borrar VLAN, VLAN borradas)."""
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
    gone = cur_ids - tgt_ids
    # el nombre de una VLAN que se borra desaparece con ella
    cur_rest = [l for l in cur_rest if not (re.match(r"vlan name (\d+) ", l) and int(l.split()[2]) in gone)]
    before = [f"vlan {_vlan_spec(tgt_ids - cur_ids)}"] if tgt_ids - cur_ids else []
    before += _fp_flat(cur_rest, tgt_rest)
    after = [f"no vlan {_vlan_spec(gone)}"] if gone else []
    return before, after, gone


def _refs_only(line: str, vlans: set[int]) -> bool:
    """¿La línea de un puerto solo menciona VLAN que se van a borrar? (al borrar la VLAN se va con ella)."""
    m = re.fullmatch(r"vlan (?:participation (?:include|exclude)|tagging) ([\d,\-]+)", line)
    return bool(m and vlans and _vlan_ids(m.group(1)) <= vlans)


def _fastpath(current: str, target: str) -> list[str]:
    cur, tgt = _fp_parse(current), _fp_parse(target)
    exec_cmds = _fp_flat([l for l in cur["exec"] if not FP_MGMT.match(l)],
                         [l for l in tgt["exec"] if not FP_MGMT.match(l)])
    vlan_before, vlan_after, gone = _fp_vlans(cur["vlan"], tgt["vlan"])
    cfg = _fp_flat(cur["top"], tgt["top"])
    cur_b, tgt_b = dict(cur["blocks"]), dict(tgt["blocks"])
    cfg_pre: list[str] = []   # bloques «vlan N» nuevos: antes que los puertos que las usan
    cfg_post: list[str] = []  # bloques «vlan N» que sobran: al final
    for header in cur_b:
        if header not in tgt_b and VLAN_HEADER.match(header):
            gone |= _vlan_ids(header.split(None, 1)[1].replace(" ", ""))

    def port_diff(old, new):
        return _fp_flat([l for l in old if not _refs_only(l, gone)], new)

    for header, children in cur["blocks"]:
        if header in tgt_b:
            continue
        if re.match(r"(interface|line) ", header):  # los puertos no se borran: vuelven a su valor por omisión
            body = port_diff([c for c in children if c != "exit"], [])
            if body:
                cfg += [header] + body + ["exit"]
        elif VLAN_HEADER.match(header):
            cfg_post.append("no " + header)
        else:
            cfg.append("no " + header)
    for header, children in tgt["blocks"]:
        old = cur_b.get(header)
        if old is None:
            (cfg_pre if VLAN_HEADER.match(header) else cfg).extend([header] + children + ["exit"])
        elif old != children:
            if any(FP_OPENER.match(c) for c in old + children):  # submodos anidados: se recrea el bloque
                cfg += ["no " + header, header] + children + ["exit"]
            else:
                body = port_diff(old, children)
                if body:
                    cfg += [header] + body + ["exit"]
    # Orden: 1) modo privilegiado y VLAN nuevas (los puertos las necesitan), 2) configuración,
    # 3) borrar VLAN al final (antes de eso los puertos todavía las usan).
    out: list[str] = []
    if exec_cmds or vlan_before:
        out.append("exit")  # al aplicar se entra en «configure»; estos comandos son del modo privilegiado
        out += exec_cmds
        if vlan_before:
            out += ["vlan database"] + vlan_before + ["exit"]
        out.append("configure")
    out += cfg_pre + cfg + cfg_post
    if vlan_after:
        out += ["exit", "vlan database"] + vlan_after + ["exit", "configure"]
    return out


def fastpath_style(text: str) -> bool:
    """¿Configuración estilo FastPath? (la primera interfaz se cierra con «exit» sin sangría)."""
    lines = [l.rstrip() for l in text.splitlines()]
    for i, l in enumerate(lines):
        if re.match(r"interface \S", l):
            j = i + 1
            while (j < len(lines) and lines[j] and not lines[j][0].isspace() and lines[j] != "exit"
                   and not lines[j].startswith(("interface ", "!"))):
                j += 1
            return j < len(lines) and lines[j] == "exit"
    return False


def build_rollback(current: str, target: str, family: str, mgmt: str | None = None) -> list[str]:
    if family in (IOS, FASTPATH) and (fastpath_style(current) or fastpath_style(target)):
        return _fastpath(current, target)
    if family == IOS:
        return _hierarchical(current, target, "no ", " exit")
    if family == HUAWEI:
        return _hierarchical(current, target, "undo ", " quit")
    if family == SET:
        return _set_style(current, target, mgmt)
    if family == FASTPATH:
        return _fastpath(current, target)
    # MERGE: re-aplica la configuración destino completa (rollback_plan ya no lo usa: ver MANUAL)
    return [l for l in target.splitlines() if l.strip() and not SKIP.match(l)]


# Plataformas con reversión propia (rollback_vendor): no siguen la sintaxis Cisco/Huawei/set
def _vendor():
    from . import rollback_vendor as rv
    return {"mikrotik_routeros": rv.routeros, "extreme_exos": rv.exos, "extreme_vsp": rv.voss}


# Sin reversión por comandos: la configuración no se puede restaurar con seguridad desde la CLI del equipo. Re-aplicar
# la configuración completa (lo que se hacía antes) no borra lo añadido y se detiene en la primera línea que ya existe
# («already exists»), dejando el equipo a medias. Se explica cómo restaurarla con la herramienta del fabricante.
MANUAL = {
    "watchguard_fireware": N_("WatchGuard Fireware no permite restaurar la configuración desde su CLI. Restáurela desde Fireware Web UI o WatchGuard System Manager (Policy Manager) con un archivo de configuración guardado del equipo, y compruebe el resultado comparando el siguiente respaldo."),
    "mikrotik_swos": N_("SwitchOS no admite cambios por comandos. Para volver a esta versión, descárguela y restáurela en SwitchOS › System (o Upgrade) › Restore Backup."),
    "ubiquiti_usg": N_("El USG lo configura el controlador UniFi Network (config.gateway.json): lo que se cambie por la CLI se pierde al reprovisionar. Revierta el cambio en UniFi Network y OpenNCCM lo verá en el siguiente respaldo."),
    "ubiquiti_unifiswitch": N_("Los switches UniFi los configura el controlador UniFi Network: lo que se cambie por la CLI se pierde al reprovisionar. Revierta el cambio en UniFi Network (o restaure su copia de seguridad) y OpenNCCM lo verá en el siguiente respaldo."),
}
MANUAL_GENERIC = N_("OpenNCCM no genera la reversión por comandos para {0}: volver a aplicar la configuración completa no borra lo que se añadió después y se detiene en la primera línea que ya existe, dejando el equipo a medias. Descargue la versión y restáurela con la herramienta del fabricante (restaurar archivo de configuración), o deshaga los cambios a mano mirando la comparación.")


def rollback_supported(profile) -> bool:
    return profile.key in _vendor() or (profile.family != MERGE and profile.key not in MANUAL)


def rollback_plan(current: str, target: str, profile, mgmt: str | None = None) -> tuple[list[str] | None, list[str]]:
    """(comandos, avisos) para llevar el equipo de «current» a «target». Comandos None: no se puede por comandos
    (el aviso explica cómo restaurar). «mgmt»: dirección por la que OpenNCCM gestiona el equipo, que no se toca."""
    fn = _vendor().get(profile.key)
    if fn:
        return fn(current, target, mgmt)
    if not rollback_supported(profile):
        return None, [_(MANUAL[profile.key]) if profile.key in MANUAL else _f(MANUAL_GENERIC, profile.label)]
    return build_rollback(current, target, profile.family, mgmt), []


__all__ = ["build_rollback", "rollback_plan", "rollback_supported", "IOS", "HUAWEI", "SET", "MERGE", "FASTPATH"]
