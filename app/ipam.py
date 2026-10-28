"""Inventario de IP y VLAN sacado de las configuraciones guardadas (no se ejecuta nada en los equipos).

Se reconocen los formatos de configuración más comunes: Cisco y similares (incluidos Huawei, EdgeSwitch, VOSS), Junos y
VyOS/EdgeRouter («set»), MikroTik («/export»), FortiGate, EXOS y airOS. Solo IPv4. Lo que no se reconoce no aparece.
IP: {"interface", "ip", "prefix"}; VLAN: {"id", "name"}.
"""
from __future__ import annotations

import ipaddress
import re


def _plen(mask: str) -> int | None:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return None


def _add(out, iface, ip, prefix):
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        return
    if ip.startswith(("0.", "127.")) or prefix is None:
        return
    out.append({"interface": iface or "", "ip": ip, "prefix": int(prefix)})


def ips(text: str, device_type: str) -> list[dict]:
    out: list[dict] = []
    lines = text.splitlines()
    if device_type == "mikrotik_routeros":
        for l in lines:
            if l.startswith("/ip address") and " add " in f" {l} ":
                m, i = re.search(r"\baddress=(\d+\.\d+\.\d+\.\d+)/(\d+)", l), re.search(r"\binterface=(\S+)", l)
                if m:
                    _add(out, i.group(1).strip('"') if i else "", m.group(1), m.group(2))
        return out
    if any(l.startswith("set ") for l in lines[:50]) or device_type in ("juniper_junos", "vyos", "ubiquiti_edgerouter", "ubiquiti_usg"):
        for l in lines:
            m = re.match(r"set interfaces (\S+)(?: unit (\d+))? family inet address (\d+\.\d+\.\d+\.\d+)/(\d+)", l)
            if m:
                _add(out, m.group(1) + (f".{m.group(2)}" if m.group(2) else ""), m.group(3), m.group(4))
            m = re.match(r"set interfaces \S+ (\S+)(?: vif (\d+))? address '?(\d+\.\d+\.\d+\.\d+)/(\d+)", l)
            if m:
                _add(out, m.group(1) + (f".{m.group(2)}" if m.group(2) else ""), m.group(3), m.group(4))
        return out
    if device_type == "extreme_exos":
        for l in lines:
            m = re.match(r'configure vlan "?(\S+?)"? ipaddress (\d+\.\d+\.\d+\.\d+)(?:/(\d+)| (\d+\.\d+\.\d+\.\d+))', l.strip())
            if m:
                _add(out, f"vlan {m.group(1)}", m.group(2), m.group(3) or _plen(m.group(4)))
        return out
    if device_type in ("ubiquiti_airos", "ubiquiti_airfiber", "ubiquiti_unifi_ap"):
        cfg = dict(l.split("=", 1) for l in lines if "=" in l)
        for k, v in cfg.items():
            m = re.match(r"netconf\.(\d+)\.ip$", k)
            if m:
                _add(out, cfg.get(f"netconf.{m.group(1)}.devname", ""), v, _plen(cfg.get(f"netconf.{m.group(1)}.netmask", "")))
        return out
    # Cisco y similares, Huawei, EdgeSwitch, VOSS y FortiGate: la IP pertenece a la interfaz del bloque en que está.
    # El bloque se abre con «interface …» (o «edit "port1"» en FortiGate) y se cierra con «exit», «!», «next» o «#»;
    # en EdgeSwitch y VOSS su contenido no lleva sangría, por eso no se usa la sangría para cerrarlo.
    iface = ""
    for l in lines:
        s = l.strip()
        m = re.match(r"(?:interface|edit)\s+\"?([^\"]+?)\"?\s*$", s)
        if m:
            iface = m.group(1)
            continue
        if s in ("exit", "!", "next", "#", "end", "quit"):
            iface = ""
            continue
        m = re.match(r"(?:ip address|set ip)\s+(\d+\.\d+\.\d+\.\d+)(?:/(\d+)|\s+(\d+\.\d+\.\d+\.\d+))", s)
        if m:
            _add(out, iface, m.group(1), m.group(2) or _plen(m.group(3)))
        m = re.match(r"network parms (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)", s)  # EdgeSwitch: gestión
        if m:
            _add(out, "network parms", m.group(1), _plen(m.group(2)))
    return out


def vlans(text: str, device_type: str) -> list[dict]:
    found: dict[int, str] = {}

    def put(vid, name=""):
        try:
            vid = int(vid)
        except (TypeError, ValueError):
            return
        if 1 <= vid <= 4094:
            found[vid] = (name or found.get(vid) or "").strip().strip('"')

    def ranges(spec):
        for part in re.split(r"[,\s]+", spec.strip()):
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit() and int(b) - int(a) < 4095:
                    for v in range(int(a), int(b) + 1):
                        put(v)
            elif part.isdigit():
                put(part)

    lines = text.splitlines()
    if device_type == "mikrotik_routeros":
        for l in lines:
            if l.startswith("/interface vlan") and " add " in f" {l} ":
                v, n = re.search(r"\bvlan-id=(\d+)", l), re.search(r'\bname=("[^"]*"|\S+)', l)
                if v:
                    put(v.group(1), n.group(1) if n else "")
            elif l.startswith("/interface bridge vlan") and " add " in f" {l} ":
                v = re.search(r"\bvlan-ids=([\d,\-]+)", l)
                if v:
                    ranges(v.group(1))
        return [{"id": k, "name": v} for k, v in sorted(found.items())]
    if device_type == "extreme_exos":
        names = {}
        for l in lines:
            s = l.strip()
            m = re.match(r'configure vlan "?(\S+?)"? tag (\d+)', s)
            if m:
                names[m.group(1)] = m.group(2)
                put(m.group(2), m.group(1))
        return [{"id": k, "name": v} for k, v in sorted(found.items())]
    current = None
    for l in lines:
        s = l.strip()
        m = re.match(r"set vlans (\S+) vlan-id (\d+)", s)  # Junos
        if m:
            put(m.group(2), m.group(1))
            continue
        m = re.match(r'vlan create (\d+)(?: name "?([^"]+?)"?)?(?: type|$)', s)  # VOSS
        if m:
            put(m.group(1), m.group(2) or "")
            continue
        m = re.match(r"vlan batch (.+)$", s)  # Huawei
        if m:
            spec = m.group(1).replace(" to ", "-")
            ranges(spec)
            continue
        m = re.match(r"vlan name (\d+) \"?([^\"]+)\"?$", s)  # EdgeSwitch: «vlan name 10 Datos»
        if m:
            put(m.group(1), m.group(2))
            continue
        m = re.match(r"vlan ([\d,\-]+)$", s)  # Cisco «vlan 10», EdgeSwitch «vlan 10,20-30»
        if m and not l[:1].isspace():
            current = m.group(1) if m.group(1).isdigit() else None
            ranges(m.group(1))
            continue
        m = re.match(r"name \"?([^\"]+)\"?$", s)
        if m and current and l[:1].isspace():
            put(current, m.group(1))
            continue
        if not l[:1].isspace():
            current = None
    return [{"id": k, "name": v} for k, v in sorted(found.items())]
