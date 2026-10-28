"""Reversión propia de plataformas que no siguen la sintaxis Cisco/Huawei/set: MikroTik RouterOS, Extreme EXOS y Extreme
VOSS. Cada función recibe la configuración actual y la de destino y devuelve (comandos, avisos):

  * los comandos llevan el equipo de la actual a la de destino, en un orden que no deja nada a medias: primero lo que hay
    que crear y cambiar, al final lo que hay que borrar (lo que se borra puede estar en uso hasta entonces);
  * nunca tocan la dirección por la que OpenNCCM gestiona el equipo (``mgmt``): cambiarla cortaría la conexión a mitad;
  * los avisos explican lo que no se puede deshacer con seguridad y hay que revisar a mano.
"""
from __future__ import annotations

import re

from .i18n import _f

# --------------------------------------------------------------------------------------------- MikroTik RouterOS
# «/export terse»: una línea por elemento, con la ruta completa. Ejemplos:
#   /interface bridge add name=bridge1
#   /interface ethernet set [ find default-name=ether1 ] comment=WAN
#   /ip address add address=192.168.88.1/24 interface=bridge1 network=192.168.88.0
#   /ip firewall filter add action=accept chain=input connection-state=established,related
#   /system identity set name=R1
VERBS = ("add", "set", "remove", "print", "enable", "disable", "unset", "edit", "export")
# elementos que se reconocen por estas propiedades (si cambian las demás, es un «set», no borrar y crear)
ROS_KEYS = {
    "/ip address": ("address",), "/ipv6 address": ("address",),
    "/interface bridge port": ("interface",), "/interface bridge vlan": ("bridge", "vlan-ids"),
    "/interface list member": ("list", "interface"), "/ip dhcp-server lease": ("mac-address",),
    "/ip dhcp-server network": ("address",), "/ip firewall address-list": ("list", "address"),
    "/ip dns static": ("name", "type"), "/ip neighbor discovery-settings": (),
    "/user": ("name",), "/snmp community": ("name",),
}
# listas en las que el orden importa: al volver a crear una regla se coloca antes de la que la seguía
ROS_ORDERED = re.compile(r"^/(ip|ipv6) firewall (filter|nat|mangle|raw)$|^/ip route rule$|^/queue simple$")
# propiedades con un valor por omisión conocido (para quitar un valor que la versión de destino no tenía)
ROS_DEFAULTS = {"comment": '""', "disabled": "no"}


def _ros_tokens(line: str) -> list[str]:
    """Divide en palabras respetando comillas («"a b"», «\\"») y corchetes («[ find default-name=ether1 ]»)."""
    out, cur, quote, depth, i = [], "", False, 0, 0
    while i < len(line):
        c = line[i]
        if quote:
            cur += c
            if c == "\\" and i + 1 < len(line):
                cur += line[i + 1]
                i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote, cur = True, cur + c
        elif c == "[":
            depth, cur = depth + 1, cur + c
        elif c == "]":
            depth, cur = depth - 1, cur + c
        elif c in " \t" and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if cur:
        out.append(cur)
    return out


def _ros_parse(text: str) -> list[dict]:
    items = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("/"):
            continue  # comentarios «#», líneas vacías
        t = _ros_tokens(line)
        vi = next((i for i, w in enumerate(t) if w in VERBS), None)
        if vi is None:
            continue
        path, verb, rest = " ".join(t[:vi]), t[vi], t[vi + 1:]
        sel = ""
        if verb == "set" and rest and (rest[0].startswith("[") or "=" not in rest[0]):
            sel, rest = rest[0], rest[1:]  # «[ find default-name=ether1 ]», «default», «0»
        props: dict[str, str] = {}
        for w in rest:
            k, _, v = w.partition("=")
            props[k] = v
        items.append({"path": path, "verb": verb, "sel": sel, "props": props, "line": line})
    return items


def _ros_keys(it: dict) -> tuple[str, ...] | None:
    """Propiedades que identifican un elemento «add» (None: se identifica por todas sus propiedades)."""
    keys = ROS_KEYS.get(it["path"])
    if keys is None and "name" in it["props"]:
        keys = ("name",)
    return keys if keys and all(k in it["props"] for k in keys) else None


def _ros_ident(it: dict):
    if it["verb"] == "set":
        return (it["path"], "set", it["sel"])
    if it["verb"] != "add":
        return (it["path"], it["verb"], it["line"])
    keys = _ros_keys(it)
    if keys:
        return (it["path"], "add") + tuple((k, it["props"][k]) for k in keys)
    return (it["path"], "add", tuple(sorted(it["props"].items())))  # sin clave: el elemento entero


def _ros_quote(v: str) -> str:
    """Valor entre comillas para «find where»: RouterOS 6 y 7 solo encuentran el elemento si todos los valores van
    entre comillas («address="10.0.0.1/24"»); sin ellas «find» no devuelve nada, ni siquiera para una IP o un puerto."""
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v  # ya viene entre comillas en la exportación
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def _ros_find(it: dict) -> str:
    """Selector «[ find where … and … ]» del elemento: por su clave si la tiene, si no por todas sus propiedades."""
    keys = _ros_keys(it)
    pairs = [(k, it["props"][k]) for k in keys] if keys else list(it["props"].items())
    return "[ find where " + " and ".join(f"{k}={_ros_quote(v)}" for k, v in pairs) + " ]"


def _ros_is_mgmt(it: dict, mgmt: str | None) -> bool:
    if not mgmt:
        return False
    if it["path"] in ("/ip address", "/ipv6 address") and it["props"].get("address", "").split("/")[0] == mgmt:
        return True
    return it["path"] == "/ip service" and ("ssh" in it["sel"] or "ssh" in it["props"].get("name", ""))


def routeros(current: str, target: str, mgmt: str | None = None) -> tuple[list[str], list[str]]:
    cur, tgt = _ros_parse(current), _ros_parse(target)
    cur_by = {_ros_ident(i): i for i in cur}
    tgt_ids = {_ros_ident(i) for i in tgt}
    apply: list[str] = []
    notes: list[str] = []
    for n, it in enumerate(tgt):
        ident = _ros_ident(it)
        old = cur_by.get(ident)
        if old is not None and old["props"] == it["props"]:
            continue
        if _ros_is_mgmt(it, mgmt) or (old is not None and _ros_is_mgmt(old, mgmt)):
            notes.append(_f("No se cambia la dirección o el acceso SSH por el que OpenNCCM gestiona el equipo: {0}", it["line"]))
            continue
        if old is None and it["verb"] == "add":
            line = it["line"]
            if ROS_ORDERED.match(it["path"]):
                # colocarla antes de la regla que la sigue en la versión de destino y ya existe en el equipo
                nxt = next((x for x in tgt[n + 1:] if x["path"] == it["path"] and _ros_ident(x) in cur_by), None)
                if nxt is not None:
                    line += f" place-before={_ros_find(nxt)}"
            apply.append(line)
            continue
        # cambiar el elemento existente: solo las propiedades distintas
        changed = {k: v for k, v in it["props"].items() if old is None or old["props"].get(k) != v}
        if old is not None:
            for k in old["props"]:
                if k not in it["props"]:
                    if k in ROS_DEFAULTS:
                        changed[k] = ROS_DEFAULTS[k]
                    else:
                        notes.append(_f("{0}: «{1}» no estaba en la versión de destino (tenía su valor por omisión); revíselo a mano.", it["path"], f"{k}={old['props'][k]}"))
        if not changed:
            continue
        sel = it["sel"] if it["verb"] == "set" else _ros_find(it)
        apply.append(f"{it['path']} set " + (f"{sel} " if sel else "") + " ".join(f"{k}={v}" for k, v in changed.items()))
    remove: list[str] = []
    for it in reversed(cur):  # al revés: primero lo que depende de otra cosa (IP de una VLAN antes que la VLAN)
        if _ros_ident(it) in tgt_ids:
            continue
        if _ros_is_mgmt(it, mgmt):
            notes.append(_f("No se borra la dirección por la que OpenNCCM gestiona el equipo: {0}", it["line"]))
            continue
        if it["verb"] == "add":
            remove.append(f"{it['path']} remove {_ros_find(it)}")
        elif it["verb"] == "set":
            resets = {k: ROS_DEFAULTS[k] for k in it["props"] if k in ROS_DEFAULTS}
            others = [k for k in it["props"] if k not in ROS_DEFAULTS]
            if resets:
                apply.append(f"{it['path']} set " + (f"{it['sel']} " if it["sel"] else "")
                             + " ".join(f"{k}={v}" for k, v in resets.items()))
            if others:
                notes.append(_f("{0}: en la versión de destino tenía los valores por omisión de {1}; revíselos a mano.", f"{it['path']} {it['sel']}".strip(), ", ".join(others)))
    return apply + remove, notes


# --------------------------------------------------------------------------------------------- Extreme EXOS
# «show configuration»: órdenes planas («create vlan "Datos"», «configure vlan Datos add ports 1-4 untagged»…).
def _exos_lines(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def _q(name: str) -> str:
    return name.strip('"')


EXOS_SINGLE = [  # ajustes de un solo valor: la línea nueva sustituye a la anterior
    r"^configure snmp sysName ", r"^configure snmp sysContact ", r"^configure snmp sysLocation ",
    r"^configure vlan \S+ tag ", r"^configure vlan \S+ ipaddress ", r"^configure vlan \S+ description ",
    r"^configure ports \S+ display-string ", r"^configure timezone ", r"^configure sntp-client (primary|secondary) ",
    r"^configure dns-client default-domain ", r"^configure ports \S+ auto ", r"^configure banner ",
]


def _exos_key(line: str):
    for p in EXOS_SINGLE:
        m = re.match(p, line)
        if m:
            return m.group(0)
    return None


def _exos_negate(line: str) -> str | None:
    t = line.split()
    m = re.match(r'^create vlan (\S+)', line)
    if m:
        return f"delete vlan {m.group(1)}"
    m = re.match(r"^configure vlan (\S+) add ports (\S+)", line)
    if m:
        return f"configure vlan {m.group(1)} delete ports {m.group(2)}"
    m = re.match(r"^configure vlan (\S+) ipaddress ", line)
    if m:
        return f"unconfigure vlan {m.group(1)} ipaddress"
    m = re.match(r"^configure vlan (\S+) description ", line)
    if m:
        return f"unconfigure vlan {m.group(1)} description"
    m = re.match(r"^configure ports (\S+) display-string ", line)
    if m:
        return f"unconfigure ports {m.group(1)} display-string"
    m = re.match(r"^configure iproute add (.+)$", line)
    if m:
        return f"configure iproute delete {m.group(1)}"
    m = re.match(r"^create account (\S+) (\S+)", line)
    if m:
        return f"delete account {m.group(2)}"
    m = re.match(r"^configure (\S+(?: \S+)?) add (.+)$", line)  # configure sntp-client add…, syslog add…
    if m and t[1] != "vlan":
        return f"configure {m.group(1)} delete {m.group(2)}"
    if t[0] == "enable":
        return "disable " + " ".join(t[1:])
    if t[0] == "disable":
        return "enable " + " ".join(t[1:])
    return None


def _exos_vlan_of(line: str) -> str | None:
    m = re.match(r"^(?:create vlan|configure vlan|enable \S+ vlan|disable \S+ vlan) (\S+)", line)
    return _q(m.group(1)) if m else None


def exos(current: str, target: str, mgmt: str | None = None) -> tuple[list[str], list[str]]:
    cur, tgt = _exos_lines(current), _exos_lines(target)
    cur_set, tgt_set = set(cur), set(tgt)
    notes: list[str] = []
    mgmt_vlans = {_q(re.match(r"^configure vlan (\S+) ipaddress", l).group(1)) for l in cur + tgt
                  if mgmt and re.match(rf"^configure vlan \S+ ipaddress {re.escape(mgmt)}(\s|/|$)", l)}
    deleted_vlans = {_q(re.match(r"^create vlan (\S+)", l).group(1)) for l in cur
                     if l not in tgt_set and l.startswith("create vlan ")}
    tgt_keys = {_exos_key(l) for l in tgt if l not in cur_set} - {None}

    create, apply, unapply, delete = [], [], [], []
    for l in tgt:
        if l in cur_set:
            continue
        if _exos_vlan_of(l) in mgmt_vlans and re.match(r"^configure vlan \S+ (ipaddress|delete ports|tag )", l):
            notes.append(_f("No se cambia la VLAN de gestión de OpenNCCM: {0}", l))
            continue
        (create if l.startswith("create ") else apply).append(l)
    for l in reversed(cur):
        if l in tgt_set:
            continue
        if _exos_key(l) and _exos_key(l) in tgt_keys:
            continue  # la línea de destino la sustituye
        vlan = _exos_vlan_of(l)
        if vlan in deleted_vlans and not l.startswith("create vlan"):
            continue  # se va con la VLAN
        if vlan in mgmt_vlans:
            notes.append(_f("No se cambia la VLAN de gestión de OpenNCCM: {0}", l))
            continue
        neg = _exos_negate(l)
        if neg is None:
            notes.append(_f("No se puede deshacer automáticamente (revíselo a mano): {0}", l))
        elif neg in tgt_set:
            continue  # «disable X» que sobra y «enable X» de destino: basta con la de destino
        elif l.startswith(("create account", "configure iproute add")):
            delete.append(neg)
        else:
            unapply.append(neg)  # incluye borrar VLAN: libera sus puertos antes de asignarlos a otra
    # orden: crear VLAN, quitar puertos/IP/VLAN que sobran (un puerto sin etiqueta solo puede estar en una VLAN),
    # aplicar lo nuevo y, al final, borrar rutas y cuentas que sobran (la ruta nueva ya está puesta)
    out: list[str] = []
    for c in create + unapply + apply + delete:
        if c not in out:
            out.append(c)
    return out, notes


# --------------------------------------------------------------------------------------------- Extreme VOSS / Fabric
# «show running-config»: órdenes de nivel superior y bloques «interface GigabitEthernet 1/1 … exit».
VOSS_OPEN = re.compile(r"^(interface |router |mgmt |isis$|spbm \d|vlan \d+ |route-map |ip prefix-list |"
                       r"radius |application$|logical-intf )")
VOSS_SINGLE = [r"^snmp-server name ", r"^snmp-server location ", r"^snmp-server contact ", r"^prompt ",
               r"^sys name ", r"^clock time-zone ", r"^ip domain-name ", r"^default-vlan-id ", r"^ip address ",
               r"^name ", r"^encapsulation ", r"^speed ", r"^duplex ", r"^auto-negotiate "]


def _voss_parse(text: str):
    top, blocks, cur = [], {}, None
    order = []
    for raw in text.splitlines():
        l = raw.strip()
        if not l or l.startswith(("#", "!")) or l in ("config terminal", "end"):
            continue
        if cur is not None:
            if l == "exit":
                cur = None
            else:
                blocks[cur].append(l)
            continue
        if VOSS_OPEN.match(l):
            cur = l
            blocks.setdefault(l, [])
            order.append(l)
        else:
            top.append(l)
    return top, blocks, order


def _voss_key(line: str):
    for p in VOSS_SINGLE:
        m = re.match(p, line)
        if m:
            return m.group(0)
    m = re.match(r"^vlan i-sid (\d+) ", line)
    return m.group(0) if m else None


def _voss_negate(line: str) -> str:
    m = re.match(r"^vlan create (\d+)", line)
    if m:
        return f"vlan delete {m.group(1)}"
    m = re.match(r"^vlan members add (\d+) (\S+)", line)
    if m:
        return f"vlan members remove {m.group(1)} {m.group(2)}"
    m = re.match(r"^vlan i-sid (\d+) ", line)
    if m:
        return f"no vlan i-sid {m.group(1)}"
    m = re.match(r"^ip route (\S+ \S+ \S+)", line)
    if m:
        return f"no ip route {m.group(1)}"
    if line == "shutdown":
        return "no shutdown"
    if line.startswith("no "):
        return line[3:]
    return "no " + line


def voss(current: str, target: str, mgmt: str | None = None) -> tuple[list[str], list[str]]:
    ctop, cblk, corder = _voss_parse(current)
    ttop, tblk, torder = _voss_parse(target)
    notes: list[str] = []
    mgmt_re = re.compile(rf"(^|\s){re.escape(mgmt)}(\s|/|$)") if mgmt else None

    def is_mgmt(header, line=""):
        return header.startswith("mgmt ") or bool(mgmt_re and (mgmt_re.search(line) or mgmt_re.search(header)))

    def skip(header, line):
        if is_mgmt(header, line):
            notes.append(_f("No se cambia el acceso de gestión de OpenNCCM: {0}", f"{header} › {line}" if header else line))
            return True
        return False

    def diff(old, new, header=""):
        """(quitar, poner): lo que sobra negado y lo que falta, sin repetir («shutdown» que sobra y «no shutdown»)."""
        new_keys = {_voss_key(l) for l in new if l not in old} - {None}
        add = [l for l in new if l not in old and not skip(header, l)]
        rem = [_voss_negate(l) for l in old if l not in new and not (_voss_key(l) and _voss_key(l) in new_keys)
               and not skip(header, l)]
        return [r for r in rem if r not in add], add

    def vlan_of(l):
        m = re.match(r"^vlan (?:create |delete |members (?:add|remove) |i-sid )(\d+)", l) or \
            re.match(r"^interface Vlan (\d+)$", l)
        return m.group(1) if m else None

    gone = {vlan_of(l) for l in ctop if l.startswith("vlan create ") and l not in ttop} - {None}
    creates = [l for l in ttop if l.startswith("vlan create ") and l not in ctop]
    rem, add = diff([l for l in ctop if not l.startswith("vlan create ") and vlan_of(l) not in gone],
                    [l for l in ttop if not l.startswith("vlan create ")])
    deletes = [f"vlan delete {v}" for v in sorted(gone, key=int)]  # también quita sus puertos e interfaz IP
    blocks: list[str] = []
    for h in torder:
        if h in cblk:
            r, a = diff(cblk[h], tblk[h], h)
            body = r + a
        else:
            body = [l for l in tblk[h] if not skip(h, l)]
        if body:
            blocks += [h] + body + ["exit"]
    for h in corder:
        if h in tblk or vlan_of(h) in gone:
            continue
        if re.match(r"^interface (GigabitEthernet|FastEthernet|gigabitEthernet|mgmtEthernet) ", h) or is_mgmt(h):
            body = [_voss_negate(l) for l in cblk[h] if not skip(h, l)]  # los puertos no se borran
            if body:
                blocks += [h] + body + ["exit"]
        else:
            blocks.append("no " + h)
    # orden: crear VLAN, quitar lo que sobra y borrar las VLAN que sobran (libera sus puertos), poner lo nuevo
    return creates + rem + deletes + add + blocks, notes
