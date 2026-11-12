"""Configuración de referencia por modelo (Dispositivos › Configuración de referencia).

Se elige el equipo que está bien configurado y los demás del mismo tipo (mismo perfil y modelo) se comparan con él, cada
vez que se respaldan. No se comparan el nombre del equipo, la IP de gestión, las descripciones de interfaces, las
contraseñas y claves cifradas, los certificados ni las líneas que se ignoraron. Desde las diferencias se puede preparar
una solicitud de cambio para igualar a la referencia: nunca se aplica sola."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request
from sqlalchemy import select

from .auth import Forbidden, check_device, device_scope, require
from .crypto import try_decrypt
from .db import ConfigVersion, Device, SessionLocal, get_setting, set_setting
from .drivers import compare_text, get_profile
from .i18n import _, _f
from .web import ago, flash, go, log_action, render

router = APIRouter()
SEP = " ⟩ "
_SECRET = re.compile(r"(?i)\b(password|secret|key|community|passphrase|pre-shared-key|encrypted-password|auth-key|psk|"
                     r"authentication-key|md5)(\s+\d{1,2})?(\s+|=)(\"[^\"]*\"|\S+)")


# --------------------------------------------------------------------------- tipos y referencias
def type_key(d: Device) -> str:
    return f"{d.device_type}|{(d.model or '').strip()}"


def type_label(key: str) -> str:
    t, _s, model = key.partition("|")
    prof = get_profile(t)
    return f"{prof.vendor} {model}".strip() if model else prof.label


def refs() -> dict[str, int]:
    try:
        return {k: int(v) for k, v in json.loads(get_setting("ref_devices", "{}") or "{}").items()}
    except (ValueError, TypeError):
        return {}


def ignores() -> dict[str, list[str]]:
    """{«tipo»: [líneas ignoradas en todos], «dev:ID»: [líneas ignoradas en ese equipo]}."""
    try:
        return json.loads(get_setting("ref_ignore", "{}") or "{}")
    except ValueError:
        return {}


# --------------------------------------------------------------------------- normalización
def _section(word: str, rest: str = "") -> str:
    w = word.lower().lstrip("/")
    r = rest.lower()
    if w == "ip" or w == "ipv6":
        nxt = r.split()[0] if r.split() else ""
        return {"access-list": "ACL", "route": _("Enrutamiento"), "dns": "DNS", "name-server": "DNS", "domain-name": "DNS",
                "ssh": "SSH", "dhcp": "DHCP", "http": _("Servicios"), "firewall": "Firewall", "service": _("Servicios")}.get(nxt, "IP")
    if w in ("aaa", "tacacs-server", "tacacs", "radius-server", "radius"):
        return "AAA"
    if w in ("snmp-server", "snmp"):
        return "SNMP"
    if w in ("ntp", "clock", "sntp"):
        return "NTP"
    if w == "vlan" or (w == "interface" and r.startswith("vlan")):
        return "VLAN"
    if w == "interface" or w == "interfaces":
        return _("Interfaces")
    if w == "logging":
        return "Logging"
    if w in ("username", "user", "users"):
        return _("Usuarios")
    if w == "line":
        return _("Líneas")
    if w == "spanning-tree":
        return "STP"
    if w in ("router", "routing", "protocols", "routing-options"):
        return _("Enrutamiento")
    if w in ("access-list", "firewall", "security"):
        return "ACL" if w == "access-list" else "Firewall"
    if w in ("system",):
        sub = r.split()[0] if r.split() else ""
        return {"ntp": "NTP", "logging": "Logging", "clock": "NTP", "login": _("Usuarios")}.get(sub, _("Sistema"))
    if w in ("banner",):
        return "Banner"
    if w in ("service", "services"):
        return _("Servicios")
    return w.upper() if len(w) <= 4 else w.capitalize()


def _mask(line: str, hosts: list[str]) -> str:
    line = _SECRET.sub(lambda m: m.group(1) + (m.group(2) or "") + m.group(3) + "***", line)
    for h in hosts:
        if h and re.fullmatch(r"[\d.]+", h):
            line = re.sub(rf"(?<![\d.]){re.escape(h)}(?![\d.])", "{IP}", line)
    return line


# Plataformas cuyos bloques no van sangrados y terminan en «exit» (Extreme VOSS: «interface GigabitEthernet 1/5»,
# «name "Puesto-1"», «exit», todo en la columna 0)
EXIT_BLOCKS = {"extreme_vsp", "extreme_ers"}
_OPENER = re.compile(r"^(interface|router|route-map|mgmt|application|i-sid|logical-intf|vrf|ip ospf area|ipv6 interface)\b", re.I)


def _indent_exit_blocks(raw: list[str]) -> list[str]:
    out, inside = [], False
    for ln in raw:
        s = ln.strip()
        if inside and s == "exit":
            inside = False
            continue
        if inside and s and not _OPENER.match(s):
            out.append(" " + s)
            continue
        inside = bool(_OPENER.match(s)) and not ln.startswith((" ", "\t"))
        out.append(ln)
    return out


def _exos_section(s: str) -> str:
    w = s.lower().split()
    obj = w[1] if len(w) > 1 else ""
    if w[0] in ("enable", "disable") and len(w) > 1:
        obj = "vlan" if "vlan" in w[2:] else w[1]
    return {"vlan": "VLAN", "account": _("Usuarios"), "iproute": _("Enrutamiento"), "ports": _("Interfaces"),
            "snmp": "SNMP", "snmpv3": "SNMP", "snmpv2c": "SNMP", "sntp-client": "NTP", "syslog": "Logging", "log": "Logging",
            "ssh2": "SSH", "web": _("Servicios"), "radius": "AAA", "dns-client": "DNS"}.get(obj, _section(obj))


def lines_of(text: str, device_type: str, hosts: list[str]) -> list[tuple[str, str]]:
    """(sección, línea normalizada) de una configuración, sin lo que no se compara. Las líneas de un bloque llevan su
    cabecera delante («interface Gi1/0/1 ⟩ switchport mode access»)."""
    text = compare_text(text or "")
    out: list[tuple[str, str]] = []
    raw = text.replace("\r\n", "\n").split("\n")
    if device_type.startswith("mikrotik_routeros"):
        joined, buf = [], ""
        for ln in raw:  # «/export» corta las líneas largas con «\» al final
            if ln.rstrip().endswith("\\"):
                buf += ln.rstrip()[:-1].strip() + " "
                continue
            joined.append(buf + ln.strip() if buf else ln)
            buf = ""
        path = ""
        for ln in joined:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("/") and " " not in s.split(" ", 1)[0] and not re.search(r"\b(add|set|remove)\b", s):
                path = s
                continue
            if s.startswith("/"):  # «/ip dns set servers=…» en una línea
                parts = s.split()
                i = next((k for k, w in enumerate(parts) if w in ("add", "set", "remove")), len(parts))
                path, s = " ".join(parts[:i]), " ".join(parts[i:])
            if path.startswith("/system identity"):
                continue
            s = re.sub(r'\s*comment=("[^"]*"|\S+)', "", s)
            if not s:
                continue
            if re.match(r"add\b", s) and re.search(r'\bname="?default"?(\s|$)', s):
                # lo crea RouterOS solo («/system keymat-provider add … name=default» en 7.24): no es una diferencia
                # de configuración y volver a crearlo falla («QKD KME address is empty»)
                continue
            words = path.split()
            sec = _section(words[1] if len(words) > 1 and words[0] in ("/ip", "/ipv6", "/system", "/interface") and words[0] != "/interface"
                           else words[0] if words else "", " ".join(words[1:]))
            if words and words[0] == "/interface":
                sec = "VLAN" if len(words) > 1 and words[1] == "vlan" else _("Interfaces")
            if words and words[0] in ("/ip", "/ipv6"):
                sec = _section("ip", " ".join(words[1:]))
            out.append((sec, _mask(f"{path} {s}", hosts)))
        return out
    if device_type.startswith("extreme_exos"):
        # EXOS: órdenes planas. No se comparan las propias de cada switch: el nombre, el engine-id de SNMPv3 y las
        # contraseñas cifradas (cada switch las cifra con su propia sal; copiarlas cambiaba la contraseña de admin)
        for ln in raw:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if re.match(r"(?i)^configure (snmp sysName|snmpv3 engine-id) ", s):
                continue
            if re.match(r'(?i)^(create|configure) account (\S+ )?\S+ encrypted ', s):
                s = re.sub(r'encrypted\s+("[^"]*"|\S+)', "encrypted ***", s)
            out.append((_exos_section(s), _mask(s, hosts)))
        return out
    if any(ln.startswith("set ") for ln in raw[:80]):  # Junos, VyOS y similares: «set …» por línea
        for ln in raw:
            s = ln.strip()
            if not s.startswith("set "):
                continue
            if re.search(r"\bhost-name\b|\bdescription\b|\bcertificate\b", s):
                continue
            w = s.split()
            out.append((_section(w[1] if len(w) > 1 else "", " ".join(w[2:])), _mask(s, hosts)))
        return out
    if device_type in EXIT_BLOCKS:
        raw = _indent_exit_blocks(raw)
    parent, psec, skip_block = "", "", False
    base, ctx = None, ""  # sangría de las líneas del bloque y su sub-bloque (tercer nivel: «class X ⟩ inspect …»)
    for ln in raw:
        if not ln.strip() or ln.strip().startswith(("!", "#")) or ln.strip() in ("end", "exit", "quit", "return"):
            if not ln.startswith((" ", "\t")):
                parent = ""
            continue
        # (ASA: los comentarios empiezan con «:» —serie, hardware, «Saved»— y la versión es «ASA Version …»)
        if re.match(r"^(Building configuration|Current configuration|:|ASA Version )", ln):
            continue
        if not ln.startswith((" ", "\t")):
            s = ln.strip()
            w = s.split()
            # lo propio de cada equipo Cisco: su certificado autofirmado (trustpoint «TP-self-signed-<n.º>»; borrarlo
            # pide confirmar) y la licencia con su serie («license udi pid CSR1000V sn …»)
            skip_block = bool(re.match(r"crypto (pki|ca) certificate|crypto pki trustpoint TP-self-signed-", s))
            if skip_block or w[0] in ("hostname", "sysname", "prompt") or re.match(r"license udi\b", s):
                parent = s
                continue
            parent, psec = s, _section(w[0], " ".join(w[1:]))
            base, ctx = None, ""
            out.append((psec, _mask(s, hosts)))
            continue
        if skip_block:
            continue
        s = ln.strip()
        if s.startswith(("description ", "name ")) and parent.lower().startswith(("interface", "vlan")):
            continue
        if re.match(r"^(certificate|quit)\b", s) or re.fullmatch(r"[0-9A-Fa-f ]{16,}", s):
            continue
        if re.match(r"ip dhcp client client-id ascii \S+$", s):  # (lleva la serie del equipo)
            continue
        ind = len(ln) - len(ln.lstrip())
        if base is None:
            base = ind
        if ind <= base:
            ctx = s
        elif ctx:  # tercer nivel: con su sub-bloque delante, para escribirlo entrando en él
            s = f"{ctx}{SEP}{s}"
        out.append((psec or _("Otros"), _mask(f"{parent}{SEP}{s}" if parent else s, hosts)))
    return out


def _latest(db, dev_id: int):
    return db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id, ConfigVersion.kind == "running")
                      .order_by(ConfigVersion.id.desc()).limit(1)).first()


def _cfg(db, d: Device):
    v = _latest(db, d.id)
    text = try_decrypt(v.content_enc) if v else None
    return v, text


def compare(ref: Device, ref_text: str, d: Device, text: str, ign: dict) -> dict:
    """Diferencias de un equipo contra la referencia: las que faltan («−») y las que sobran («+»), por sección."""
    hosts = [ref.host, d.host]
    rl = lines_of(ref_text, ref.device_type, hosts)
    dl = lines_of(text, d.device_type, hosts)
    skip = set(ign.get(type_key(ref), [])) | set(ign.get(f"dev:{d.id}", []))
    rset, dset = {k for _s, k in rl}, {k for _s, k in dl}
    missing = [(s, k) for s, k in rl if k not in dset and k not in skip]
    extra = [(s, k) for s, k in dl if k not in rset and k not in skip]
    secs: list[str] = []
    for s, _k in missing + extra:
        if s not in secs:
            secs.append(s)
    return {"n": len(missing) + len(extra), "missing": missing, "extra": extra, "sections": secs, "ref_lines": rl, "dev_lines": dl,
            "skip": skip}


def blocks(c: dict) -> list[dict]:
    """Bloques por sección para la vista de diferencias: líneas iguales (contexto), las que faltan y las que sobran."""
    miss, extra = {k for _s, k in c["missing"]}, {k for _s, k in c["extra"]}
    out = []
    for sec in c["sections"]:
        rows = []
        ref_sec = [k for s, k in c["ref_lines"] if s == sec]
        dev_sec = [k for s, k in c["dev_lines"] if s == sec]
        changed_parents = {k.split(SEP)[0] for k in ref_sec + dev_sec if k in miss or k in extra}
        for k in ref_sec:
            if k in miss:
                rows.append({"t": "-", "k": k})
            elif k.split(SEP)[0] in changed_parents:
                rows.append({"t": "=", "k": k})
        for k in dev_sec:
            if k in extra:
                rows.append({"t": "+", "k": k})
        n = sum(r["t"] != "=" for r in rows)
        out.append({"sec": sec, "n": n, "rows": rows, "keys": [r["k"] for r in rows if r["t"] != "="]})
    return out


def commands(device_type: str, c: dict) -> tuple[str, int]:
    """Comandos para igualar a la referencia y cuántas diferencias no se pudieron escribir (claves enmascaradas, o lo
    que sobra en un MikroTik)."""
    from . import consolecore as cc
    os_key = cc.os_of(device_type)
    skipped = 0
    lines: list[str] = []
    if os_key == "fortios":
        # FortiOS va por bloques «config … / edit … / next / end» y no tiene «no …»: los comandos al estilo Cisco que
        # salían de aquí («no set …», bloques sin «end») el equipo los rechazaba a mitad («Unknown action 0», visto en un
        # FortiGate-VM 7.4.2). La comparación se muestra igual; las diferencias se igualan a mano.
        return "", len(c["missing"]) + len(c["extra"])
    if device_type.startswith("mikrotik_routeros"):
        for _s, k in c["missing"]:
            if "***" in k or "{IP}" in k:
                skipped += 1
                continue
            lines.append(k)
        skipped += len(c["extra"])
        return "\n".join(lines), skipped
    if device_type.startswith("extreme_exos"):
        # EXOS no tiene «no …»: lo que sobra se deshace con su orden contraria («delete vlan», «unconfigure…»), en el
        # orden que EXOS necesita. Se usa el mismo cálculo que «Volver a esta versión».
        from .rollback_vendor import exos
        bad = lambda k: "***" in k or "{IP}" in k  # noqa: E731
        cur = [k for _s, k in c["dev_lines"] if not bad(k)]
        # destino: lo de la referencia, salvo lo que se decidió no comparar, que se queda como está en el equipo
        tgt = [k for _s, k in c["ref_lines"] if not bad(k) and k not in c["skip"]]
        tgt += [k for _s, k in c["dev_lines"] if not bad(k) and k in c["skip"] and k not in tgt]
        skipped += sum(bad(k) for _s, k in c["missing"] + c["extra"])
        cmds, notes = exos("\n".join(cur), "\n".join(tgt))
        return "\n".join(cmds), skipped + len(notes)
    if os_key == "junos" or any(k.startswith("set ") for _s, k in c["missing"] + c["extra"]):
        for _s, k in c["missing"]:
            if "***" in k or "{IP}" in k:
                skipped += 1
            else:
                lines.append(k)
        from .rollback import set_deletes
        extra = []
        for _s, k in c["extra"]:
            if "***" in k or "{IP}" in k:
                skipped += 1
            else:
                extra.append(k)
        keep = [k for _s, k in c["ref_lines"]] + [k for _s, k in c["dev_lines"] if k in c["skip"]]
        # las interfaces del equipo no se borran enteras: su descripción (propia de cada equipo) no se compara
        keep += sorted({" ".join(k.split()[:3]) + " description" for k in extra if k.startswith("set interfaces ")})
        lines = set_deletes(extra, keep) + lines  # (lo que sobra se borra antes de poner lo que falta)
        return "\n".join(lines), skipped
    by_parent: dict[str, list[str]] = {}
    tops: list[str] = []

    def neg(line: str) -> str | None:
        if device_type in EXIT_BLOCKS:  # VOSS: «vlan create 20 …» se quita con «vlan delete 20», no con «no vlan create»
            from .rollback_vendor import _voss_negate
            return _voss_negate(line)
        # lo que sobra y ya es un «no …» («no ip address» de una interfaz) no se niega otra vez («no no ip address»)
        from .rollback import IOS_KEEP
        return None if line.startswith("no ") or line in IOS_KEEP else "no " + line
    gone = {k for _s, k in c["extra"] if SEP not in k}  # bloques que se quitan enteros: sus líneas no se tocan
    for sign, items in (("+", c["missing"]), ("no ", c["extra"])):
        for _s, k in items:
            if "***" in k or "{IP}" in k:
                skipped += 1
                continue
            parent, sep, child = k.partition(SEP)
            if sep and sign != "+" and parent in gone:
                continue
            if not sep and device_type not in EXIT_BLOCKS:  # AAA encendido/apagado no se cambia al igualar
                from .rollback import IOS_KEEP
                if parent in IOS_KEEP or parent.removeprefix("no ") in IOS_KEEP:
                    skipped += 1
                    continue
            if sep and SEP in child:  # tercer nivel: se niega la línea, no su sub-bloque
                sub, _s2, leaf = child.partition(SEP)
                line = f"{sub}{SEP}{leaf if sign == '+' else neg(leaf)}" if (sign == "+" or neg(leaf)) else None
            else:
                line = (child if sep else parent) if sign == "+" else neg(child if sep else parent)
            if line is None:
                continue
            if sep:
                by_parent.setdefault(parent, []).append(line)
            else:
                tops.append(line)
    blocks = []
    for p, ch in by_parent.items():
        blocks.append(p)
        inside = None
        for x in ch:
            sub, sep2, leaf = x.partition(SEP)
            if sep2:  # tercer nivel: dentro de su sub-bloque
                if sub != inside:
                    if inside is not None:
                        blocks.append("  exit")
                    blocks.append(" " + sub)
                    inside = sub
                blocks.append("  " + leaf)
                continue
            if inside is not None:
                blocks.append("  exit")
                inside = None
            blocks.append(" " + x)
        if inside is not None:
            blocks.append("  exit")
        if os_key in ("vrp",):
            blocks.append(" quit")
        elif device_type in EXIT_BLOCKS or os_key == "asa":
            blocks.append("exit")
    # (VOSS: «banner custom» y «banner "…"» se quitan con un solo «no banner»). Un bloque que falta entero sale con
    # sus líneas: su cabecera no se escribe también suelta
    tops = [t for t in dict.fromkeys(tops) if t not in by_parent]
    if os_key == "asa":
        # ASA: no sale solo de un modo («object network …») a la línea siguiente, y «mtu puesto 1500» o «route puesto …»
        # necesitan antes el «nameif puesto» de la interfaz: primero los bloques (cada uno con «exit»), después lo suelto.
        # Antes salía al revés y el ASA rechazaba «mtu puesto 1500» dentro de «object network» (ASAv 9.9.2)
        return "\n".join(blocks + tops), skipped
    return "\n".join(tops + blocks), skipped


def overview(user, key: str = "") -> dict:
    scope = device_scope(user)
    rf = refs()
    ign = ignores()
    with SessionLocal() as db:
        devs = [d for d in db.scalars(select(Device).where(Device.retired_at.is_(None)).order_by(Device.name)) if scope is None or d.id in scope]
        types: dict[str, list[Device]] = {}
        for d in devs:
            types.setdefault(type_key(d), []).append(d)
        tlist = [{"key": k, "label": type_label(k), "n": len(v), "ref": next((d.name for d in v if d.id == rf.get(k)), "")}
                 for k, v in sorted(types.items(), key=lambda kv: (-len(kv[1]), type_label(kv[0]).lower()))]
        cur = key if key in types else (tlist[0]["key"] if tlist else "")
        res = {"types": tlist, "cur": cur, "cur_label": type_label(cur) if cur else "", "rows": [], "ref": None,
               "members": [{"id": d.id, "name": d.name} for d in types.get(cur, [])]}
        if not cur:
            return res
        ref = next((d for d in types[cur] if d.id == rf.get(cur)), None)
        if ref is None:
            return res
        rv, rtext = _cfg(db, ref)
        res["ref"] = {"id": ref.id, "name": ref.name, "os": ref.os_version or "", "at": rv.created_at if rv else None,
                      "label": get_profile(ref.device_type).label}
        for d in types[cur]:
            if d.id == ref.id:
                continue
            v, text = _cfg(db, d)
            if not v or not text or not rtext:
                res["rows"].append({"id": d.id, "name": d.name, "n": None, "sections": [], "at": None})
                continue
            c = compare(ref, rtext, d, text, ign)
            res["rows"].append({"id": d.id, "name": d.name, "n": c["n"], "sections": c["sections"], "at": v.created_at})
    res["rows"].sort(key=lambda r: (r["n"] is None, -(r["n"] or 0)))
    res["max"] = max([r["n"] or 0 for r in res["rows"]] + [1])
    return res


# --------------------------------------------------------------------------- páginas
@router.get("/config-reference")
def page(request: Request, t: str = ""):
    user = require(request)
    return render(request, "reference.html", user, o=overview(user, t), ago=ago)


@router.post("/config-reference/set")
async def set_ref(request: Request):
    """«Usar como referencia» (desde la página o desde la ficha del equipo: ⋯ › Usar como referencia)."""
    user = require(request, "operator")
    form = await request.form()
    dev_id = int(form.get("device_id") or 0)
    check_device(user, dev_id)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None:
            raise Forbidden()
        k, name = type_key(d), d.name
    rf = refs()
    rf[k] = dev_id
    set_setting("ref_devices", json.dumps(rf))
    log_action(request, user, "reference_set", f"{type_label(k)}: {name}")
    flash(request, _f("{0} es ahora la referencia de {1}.", name, type_label(k)))
    return go("/config-reference?t=" + __import__("urllib.parse").parse.quote(k))


def _pair(user, dev_id: int):
    check_device(user, dev_id)
    with SessionLocal() as db:
        d = db.get(Device, dev_id)
        if d is None:
            raise Forbidden()
        ref_id = refs().get(type_key(d))
        ref = db.get(Device, ref_id) if ref_id else None
        if ref is None or ref.id == d.id:
            raise Forbidden()
        _rv, rtext = _cfg(db, ref)
        _v, text = _cfg(db, d)
    return ref, rtext or "", d, text or ""


@router.get("/config-reference/diff/{dev_id}")
def diff_page(request: Request, dev_id: int, view: str = "side"):
    user = require(request)
    ref, rtext, d, text = _pair(user, dev_id)
    c = compare(ref, rtext, d, text, ignores())
    cmds, skipped = commands(d.device_type, c)
    return render(request, "reference_diff.html", user, ref=ref, dev=d, c=c, blocks=blocks(c), view=view if view in ("side", "changes") else "side",
                  cmd_n=len([x for x in cmds.splitlines() if x.strip()]), skipped=skipped, tkey=type_key(ref), SEP=SEP)


@router.post("/config-reference/ignore")
async def ignore(request: Request):
    """«Ignorar en ESTE equipo» o «Ignorar en todos» las líneas de un bloque."""
    user = require(request, "operator")
    form = await request.form()
    dev_id = int(form.get("device_id") or 0)
    ref, rtext, d, text = _pair(user, dev_id)
    c = compare(ref, rtext, d, text, ignores())
    sec = form.get("section") or ""
    keys = next((b["keys"] for b in blocks(c) if b["sec"] == sec), [])
    ign = ignores()
    slot = type_key(ref) if form.get("scope") == "all" else f"dev:{d.id}"
    ign[slot] = sorted(set(ign.get(slot, [])) | set(keys))
    set_setting("ref_ignore", json.dumps(ign))
    log_action(request, user, "reference_ignore", f"{d.name} · {sec} · {len(keys)} líneas · {'todos' if slot != f'dev:{d.id}' else d.name}")
    flash(request, _f("{0} líneas de «{1}» ya no se comparan.", len(keys), sec))
    return go(f"/config-reference/diff/{dev_id}")


@router.get("/config-reference/ignored")
def ignored_page(request: Request, t: str = ""):
    """«No se comparan» › Editar: las líneas que se ignoraron (en todos o en un equipo)."""
    user = require(request, "operator")
    ign = ignores()
    with SessionLocal() as db:
        names = {d.id: d.name for d in db.scalars(select(Device).where(Device.retired_at.is_(None)))}
    devs = {k: v for k, v in ign.items() if k.startswith("dev:")}
    return render(request, "reference_ignored.html", user, t=t, label=type_label(t) if t else "", all_lines=ign.get(t, []),
                  devs={names.get(int(k[4:]), k): (k, v) for k, v in devs.items() if v}, ago=ago)


@router.post("/config-reference/ignored")
async def ignored_save(request: Request):
    user = require(request, "operator")
    form = await request.form()
    t = form.get("t") or ""
    ign = ignores()
    for k in [k for k in form.keys() if k.startswith("lines:")]:
        slot = k[6:]
        ign[slot] = [x.strip() for x in (form.get(k) or "").splitlines() if x.strip()]
    set_setting("ref_ignore", json.dumps({k: v for k, v in ign.items() if v}))
    log_action(request, user, "reference_ignore_edit", type_label(t) if t else "")
    flash(request, _("Líneas ignoradas guardadas."))
    return go("/config-reference?t=" + __import__("urllib.parse").parse.quote(t))


@router.post("/config-reference/request/{dev_id}")
def make_request(request: Request, dev_id: int):
    """«Preparar solicitud»: arma una solicitud de cambio con los comandos que faltan y los que sobran."""
    from .main import _create_cr
    user = require(request, "operator")
    ref, rtext, d, text = _pair(user, dev_id)
    c = compare(ref, rtext, d, text, ignores())
    cmds, skipped = commands(d.device_type, c)
    if not cmds.strip():
        flash(request, _("No hay comandos que se puedan preparar: las diferencias que quedan llevan claves o hay que hacerlas a mano."), "error")
        return go(f"/config-reference/diff/{dev_id}")
    notes = [_f("{0} diferencias no se pudieron escribir (claves, IP de gestión o líneas que sobran en MikroTik): revísalas a mano.", skipped)] if skipped else None
    cr = _create_cr(user, _f("Igualar {0} a la referencia {1}", d.name, ref.name), cmds, {}, [d.id], notes=notes)
    log_action(request, user, "reference_request", f"#{cr} {d.name} ← {ref.name}")
    flash(request, _f("Solicitud #{0} creada: igualar {1} a {2}. Espera aprobación.", cr, d.name, ref.name))
    return go(f"/changes/{cr}")


def drift_n(dev: Device) -> int | None:
    """Diferencias de un equipo con la referencia de su tipo (para la ficha); None si no hay referencia."""
    ref_id = refs().get(type_key(dev))
    if not ref_id or ref_id == dev.id:
        return None
    with SessionLocal() as db:
        ref = db.get(Device, ref_id)
        if ref is None:
            return None
        _rv, rtext = _cfg(db, ref)
        _v, text = _cfg(db, dev)
    if not rtext or not text:
        return None
    return compare(ref, rtext, dev, text, ignores())["n"]

