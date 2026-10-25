"""Actualizaciones de firmware de los equipos: qué versión más nueva hay, qué mejora y, cuando el fabricante la publica
sin cuenta, descargarla a la biblioteca de Firmware.

Fuentes (todas públicas; se consultan una vez al día, o con «Comprobar ahora»):
- MikroTik (upgrade.mikrotik.com): versión estable y long-term, novedades y archivo por arquitectura con su SHA-256.
- Ubiquiti (fw-update.ui.com): EdgeSwitch, EdgeRouter y UniFi Switch por modelo, con archivo y SHA-256.
- El resto de equipos con CPE: diccionario de versiones de NVD («la versión más reciente que NVD conoce»). Esos
  fabricantes piden cuenta o contrato para descargar, así que OpenNCCM avisa y enlaza las notas cuando hay un enlace fiable.
Mejoras: los CVE de la versión instalada que ya no afectan a la nueva (según NVD), más las novedades del fabricante.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from sqlalchemy import select

from . import __version__, services
from .config import DATA_DIR
from .db import Device, FirmwareImage, FirmwareUpdate, SessionLocal, get_setting, now, set_setting
from .drivers import get_profile

log = logging.getLogger("opennccm.fwupdates")
CACHE_DIR = DATA_DIR / "fwupdates"
UA = {"User-Agent": f"OpenNCCM/{__version__} (+https://github.com/Gaaraalex/opennccm)"}
MIKROTIK = "https://upgrade.mikrotik.com/routeros"
MIKROTIK_DL = "https://download.mikrotik.com/routeros"
UBNT_API = "https://fw-update.ui.com/api/firmware-latest?filter=eq~~channel~~release"
DOWNLOAD_HOSTS = {"download.mikrotik.com", "fw-download.ubnt.com", "dl.ui.com"}  # únicos orígenes de descarga
MAX_DOWNLOAD = 2 * 1024 ** 3
PRERELEASE = re.compile(r"beta|alpha|\brc\d*|eft|testing|preview|devel|nightly", re.I)
CPE_TTL = 24 * 3600   # diccionario de versiones de NVD
CVE_TTL = 7 * 24 * 3600  # CVE de una versión concreta

# Notas de la versión: solo enlaces comprobados. «{v}» es la versión; «{mm}» mayor-menor con guion (PAN-OS «10-2»).
NOTES = {
    "fortinet": "https://docs.fortinet.com/document/fortigate/{v}/fortios-release-notes",
    "paloalto_panos": "https://docs.paloaltonetworks.com/pan-os/{mm}/pan-os-release-notes",
    "juniper_junos": "https://www.juniper.net/documentation/product/us/en/junos-os/",
    "opnsense": "https://docs.opnsense.org/releases.html",
    "pfsense": "https://docs.netgate.com/pfsense/en/latest/releases/index.html",
}


# --------------------------------------------------------------------------- versiones
def vkey(v: str) -> tuple:
    """Orden natural de versiones: «7.2.13» > «7.2.5», «15.2(4)M10» > «15.2(4)M7», «21.4:r3-s5» > «21.4:r3»."""
    return tuple((1, int(t)) if t.isdigit() else (0, t) for t in re.findall(r"\d+|[a-z]+", (v or "").lower()))


def family(prof) -> str:
    if prof.cpe_format == "junos":
        return "junos"
    cpes = (prof.cpe,) if isinstance(prof.cpe, str) else tuple(prof.cpe or ())
    if any(c.endswith(":cisco:ios") for c in cpes):
        return "cisco_ios"
    if prof.key.startswith("mikrotik"):
        return "major"
    return "default"


def branch(v: str, fam: str) -> str | None:
    """Rama de una versión: dentro de ella se recomienda la más nueva (mismo hardware, sin saltos de versión mayor)."""
    v = (v or "").lower()
    if fam == "cisco_ios":  # «15.2(4)m7» → «15.2(4)m»: misma serie de mantenimiento
        m = re.match(r"(\d+\.\d+\(\d+\)[a-z]*)", v)
        return m.group(1) if m else None
    if fam == "junos":  # «21.4:r3-s5» → «21.4»; «15.1x49:d200» → «15.1x49»
        return v.split(":")[0] or None
    nums = re.findall(r"\d+", v)
    if fam == "major":
        return nums[0] if nums else None
    return ".".join(nums[:2]) if len(nums) >= 2 else (nums[0] if nums else None)


def pretty(v: str | None, fam: str, like: str = "") -> str | None:
    """Versión del diccionario CPE escrita como la escribe el fabricante: «21.4:r3-s9» → «21.4R3-S9»,
    «15.1x49:d200» → «15.1X49-D200», «15.2(4)m11» → «15.2(4)M11», «10.2.18:h12» → «10.2.18-h12»; con mayúsculas si
    la versión instalada las usa («4.28.3M» → «4.28.7M»)."""
    if not v:
        return v
    if fam == "junos":
        ver, _s, upd = v.partition(":")
        if not upd:
            return ver.upper()
        return ver.upper() + (upd.upper() if upd.lower().startswith("r") else "-" + upd.upper())
    if fam == "cisco_ios":
        return v.upper()
    base, _s, upd = v.partition(":")
    if like and like != like.lower():
        base = base.upper()
    if not upd:
        return base
    if upd.startswith("take_"):  # Check Point: «r81.20:take_99» → «R81.20 Take 99»
        return f"{base} Take {upd[5:]}"
    return f"{base}-{upd}"


def device_cpe_version(prof, version: str) -> str | None:
    """Versión del equipo escrita como en los CPE de NVD (sin escapes)."""
    if not version:
        return None
    if prof.cpe_format == "junos":
        jv = services.junos_cpe_version(version)
        return jv.replace("\\", "") if jv else None
    if prof.cpe_version and (m := re.search(prof.cpe_version, version)):
        return m.group(1).lower()
    return version.strip().lower()


def pick(current: str, candidates: list[str], fam: str) -> tuple[str | None, str | None]:
    """(la más nueva de la rama del equipo, la más nueva de todas) entre las candidatas más nuevas que la instalada."""
    cur_k, cur_b = vkey(current), branch(current, fam)
    newer = [c for c in candidates if vkey(c) > cur_k and not PRERELEASE.search(c)]
    if fam == "junos":
        newer = [c for c in newer if ":" in c]  # «21.4» sin actualización no es una versión instalable
    same = [c for c in newer if branch(c, fam) == cur_b]
    best_branch = max(same, key=vkey) if same else None
    if fam == "cisco_ios":  # las series de IOS dependen del hardware: solo se recomienda la misma serie
        return best_branch, best_branch
    # «la más reciente» de otra rama: solo ramas con al menos 2 versiones conocidas. Así una entrada suelta o mal escrita
    # del diccionario (p. ej. «16.088.0» entre las versiones 8.x del controlador inalámbrico de Cisco) no se recomienda.
    count: dict = {}
    for c in candidates:
        b = branch(c, fam)
        count[b] = count.get(b, 0) + 1
    solid = [c for c in newer if branch(c, fam) == cur_b or count.get(branch(c, fam), 0) >= 2]
    best = max(solid, key=vkey) if solid else None
    return best_branch, best


# --------------------------------------------------------------------------- caché en disco
def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / f"{kind}-{re.sub(r'[^A-Za-z0-9._-]+', '_', key)[:150]}.json"


def _cached(kind: str, key: str, ttl: int, fetch):
    p = _cache_path(kind, key)
    try:
        if p.exists() and time.time() - p.stat().st_mtime < ttl:
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data = fetch()
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", (10, 60)), **kw)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------- NVD: versiones conocidas y CVE
def nvd_versions(cpe: str) -> list[str]:
    """Versiones de un producto en el diccionario CPE de NVD («21.4:r3-s5» si tiene actualización)."""
    def fetch():
        out, start, total = set(), 0, None
        while total is None or start < total:
            data = None
            for attempt in range(3):
                services._nvd_wait(time.sleep, time.monotonic)
                try:
                    key = services.nvd_api_key()
                    r = requests.get("https://services.nvd.nist.gov/rest/json/cpes/2.0",
                                     headers={**UA, **({"apiKey": key} if key else {})},
                                     params={"cpeMatchString": cpe, "resultsPerPage": 10000, "startIndex": start},
                                     timeout=(10, 120))
                    if r.status_code in (403, 429) or r.status_code >= 500:
                        time.sleep(2 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    data = r.json()
                    break
                except (requests.RequestException, ValueError):
                    time.sleep(2 * (attempt + 1))
            if data is None:
                raise RuntimeError("NVD no respondió (diccionario de versiones)")
            total = int(data.get("totalResults") or 0)
            prods = data.get("products", [])
            for p in prods:
                c = p.get("cpe", {})
                if c.get("deprecated"):
                    continue
                parts = c.get("cpeName", "").split(":")
                if len(parts) < 7 or parts[5] in ("*", "-", ""):
                    continue
                v = parts[5].replace("\\", "")
                if parts[6] not in ("*", "-", ""):
                    v = f"{v}:{parts[6].replace(chr(92), '')}"
                out.add(v.lower())
            if not prods:
                break
            start += len(prods)
        return sorted(out, key=vkey)
    return _cached("cpe", cpe, CPE_TTL, fetch)


def _cve_params(prof, cpe_ver: str) -> list[dict]:
    cpes = (prof.cpe,) if isinstance(prof.cpe, str) else tuple(prof.cpe or ())
    esc = ":".join(services._cpe_escape(p) for p in cpe_ver.split(":")) if prof.cpe_format == "junos" \
        else services._cpe_escape(cpe_ver)
    return [{"virtualMatchString": f"{c}:{esc}"} for c in cpes]


def _severity(cve: dict) -> str:
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        for m in cve.get("metrics", {}).get(key, []):
            return (m["cvssData"].get("baseSeverity") or m.get("baseSeverity") or "").upper()
    return ""


def cves_for(prof, cpe_ver: str) -> dict[str, str]:
    """{CVE: severidad} que NVD asocia a esa versión (con caché de una semana)."""
    def fetch():
        out = {}
        for params in _cve_params(prof, cpe_ver):
            for item in services.nvd_query(params):
                out[item["cve"]["id"]] = _severity(item["cve"])
        return out
    key = "|".join(p["virtualMatchString"] for p in _cve_params(prof, cpe_ver))
    return _cached("cve", key, CVE_TTL, fetch)


SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "": 4}


def fixed_cves(prof, cur_ver: str, new_ver: str) -> tuple[list, int]:
    """(CVE corregidos al pasar de la versión instalada a la nueva, ordenados por gravedad; CVE que siguen)."""
    cur, new = cves_for(prof, cur_ver), cves_for(prof, new_ver)
    fixed = sorted(([c, s] for c, s in cur.items() if c not in new), key=lambda x: (SEV_ORDER.get(x[1], 4), x[0]))
    return fixed, len(new)


# --------------------------------------------------------------------------- MikroTik
def _mikrotik_channel(chan: str) -> str | None:
    def fetch():
        txt = _get(f"{MIKROTIK}/NEWEST{chan}", timeout=(10, 20)).text.split()
        return txt[0] if txt and re.match(r"^\d+\.\d+", txt[0]) else None
    return _cached("mikrotik", chan, 6 * 3600, fetch)


def mikrotik_file(version: str, arch: str | None) -> str | None:
    """Nombre del paquete «routeros» para la arquitectura del equipo (v7: «routeros-7.x-arm64.npk», v6: al revés)."""
    if not arch:
        return None
    arch = arch.lower()
    if version.startswith("6."):
        return f"routeros-x86-{version}.npk" if arch in ("x86", "x86_64", "i386") else f"routeros-{arch}-{version}.npk"
    return f"routeros-{version}.npk" if arch in ("x86", "x86_64", "i386") else f"routeros-{version}-{arch}.npk"


def _mikrotik(dev, prof, raw: str) -> dict:
    cur = (dev.os_version or "").strip()
    major = cur.split(".")[0]
    chans = {"6": ["6.long-term", "6.stable"], "7": ["a7.stable", "a7.long-term"]}.get(major, ["a7.stable"])
    vers = {c: _mikrotik_channel(c) for c in chans}
    if major == "6":
        vers["a7.stable"] = _mikrotik_channel("a7.stable")
    same = [v for c, v in vers.items() if v and v.split(".")[0] == major]
    target, latest = pick(cur, same, "major")
    _b, latest_any = pick(cur, [v for v in vers.values() if v], "default")
    latest = latest_any or latest
    res = {"source": "mikrotik", "target": target or latest, "latest": latest or cur,
           "notes_url": "https://mikrotik.com/download/changelogs"}
    if not res["target"]:
        return res
    v = res["target"]
    try:
        res["changelog"] = _cached("mikrotik-log", v, 7 * 24 * 3600,
                                   lambda: _get(f"{MIKROTIK}/{v}/CHANGELOG", timeout=(10, 30)).text[:8000])
    except requests.RequestException:
        pass
    m = re.search(r"architecture-name:\s*(\S+)", raw or "")
    name = mikrotik_file(v, m.group(1) if m else None)
    if name:
        res["download_url"] = f"{MIKROTIK_DL}/{v}/{name}"
    else:
        res["detail"] = "arch"
    return res


# --------------------------------------------------------------------------- Ubiquiti
def _ubnt_catalog() -> list[dict]:
    def fetch():
        items = _get(UBNT_API, timeout=(10, 60)).json().get("_embedded", {}).get("firmware", [])
        return [{"product": f.get("product"), "platform": f.get("platform"), "version": f.get("version") or "",
                 "url": ((f.get("_links") or {}).get("data") or {}).get("href"), "sha256": f.get("sha256_checksum"),
                 "size": f.get("file_size")} for f in items]
    return _cached("ubnt", "catalog", 6 * 3600, fetch)


def ubnt_clean(v: str) -> str:
    """«v7.5.15+17146» → «7.5.15»."""
    return (v or "").strip().lstrip("vV").split("+")[0]


EDGEROUTER = [(r"infinity|8-?xg", "e1000"), (r"edgerouter (4|6p|12)|\ber-?(4|6p|12p?)\b", "e300"),
              (r"edgerouter x|\ber-?x\b|ep-?r6", "e50"), (r"lite|poe|erlite|erpoe", "e100"),
              (r"edgerouter (8|pro)|\ber-?8\b|erpro|ep-?r8", "e200")]


def ubnt_product(prof_key: str, model: str) -> tuple[str, str] | None:
    """(product, platform) del catálogo de Ubiquiti para el modelo del equipo, o None si no se reconoce."""
    m = (model or "").lower()
    if prof_key == "ubiquiti_edgeswitch":
        # con PoE («150W», «250W»…) la línea ESWH; sin PoE / Lite / XG la ESGH
        return ("eswh" if re.search(r"\d+\s*w\b", m) else "esgh"), "edgeswitch"
    if prof_key == "ubiquiti_edgerouter":
        for rx, product in EDGEROUTER:
            if re.search(rx, m):
                return product, "edgerouter"
        return None
    if prof_key == "ubiquiti_unifiswitch":
        g = re.match(r"^us-?(\d+)-?(\d+)w", m)  # «US-24-250W» → «US24P250»
        plat = f"US{g.group(1)}P{g.group(2)}" if g else re.sub(r"[^A-Za-z0-9]", "", model or "").upper()
        return "unifi-firmware", plat
    return None


def _ubiquiti(dev, prof) -> dict | None:
    pp = ubnt_product(prof.key, dev.model or "")
    if not pp:
        return None
    product, plat = pp
    cat = _ubnt_catalog()
    if prof.key == "ubiquiti_unifiswitch":
        items = [f for f in cat if f["product"] == product and (f["platform"] or "").upper() == plat]
    else:
        lite = "lite" in (dev.model or "").lower()
        items = [f for f in cat if f["product"] == product and f["platform"] == plat
                 and ("-lite" in f["version"]) == (lite and prof.key == "ubiquiti_edgeswitch")]
    if not items:
        return None
    best = max(items, key=lambda f: vkey(ubnt_clean(f["version"])))
    cur = device_cpe_version(prof, dev.os_version) or dev.os_version
    new = ubnt_clean(best["version"])
    res = {"source": "ubiquiti", "latest": new, "notes_url": "https://community.ui.com/releases"}
    if vkey(new.replace("-lite", "")) > vkey(cur):
        res.update(target=new, download_url=best["url"], sha256=best["sha256"], size=best["size"])
    return res


# --------------------------------------------------------------------------- Ubuntu y Debian
DISTRO_INFO = "https://salsa.debian.org/debian/distro-info-data/-/raw/main/{}.csv"  # la misma tabla que ubuntu-distro-info


def _distro_rows(distro: str) -> list[dict]:
    def fetch():
        import csv
        import io
        return list(csv.DictReader(io.StringIO(_get(DISTRO_INFO.format(distro), timeout=(10, 30)).text)))
    return _cached("distro", distro, 24 * 3600, fetch)


def _distro(dev, today=None) -> dict | None:
    """Ubuntu (solo LTS) o Debian: la versión estable más nueva y el fin de soporte de la instalada (distro-info-data)."""
    from datetime import date
    today = (today or date.today()).isoformat()
    cur = (dev.os_version or "").strip()
    m = re.match(r"^(\d+\.\d+)", cur)
    distro = "ubuntu" if m else ("debian" if re.match(r"^\d+$", cur) else None)
    if not distro:
        return None
    series = m.group(1) if m else cur
    rows = [r for r in _distro_rows(distro) if r.get("release") and r["release"] <= today]
    if distro == "ubuntu":
        rows = [r for r in rows if "LTS" in r.get("version", "")]
    key = (lambda r: r["version"].split()[0])
    mine = next((r for r in rows if key(r) == series), None)
    newest = max(rows, key=lambda r: vkey(key(r)), default=None)
    res = {"source": "distro", "latest": key(newest) + (" LTS" if distro == "ubuntu" else "") if newest else None}
    if newest and vkey(key(newest)) > vkey(series):
        res["target"] = res["latest"]
        res["notes_url"] = (f"https://documentation.ubuntu.com/release-notes/{key(newest)}/" if distro == "ubuntu"
                            else f"https://www.debian.org/releases/{newest['series']}/releasenotes")
    if mine:
        end = mine.get("eol-server") or mine.get("eol") or ""
        extra = mine.get("eol-esm") or mine.get("eol-lts") or ""
        if end and end < today:
            res["detail"] = f"sinsoporte:{end}:{extra}"
        elif end:
            res["detail"] = f"soporte:{end}"
    return res


# --------------------------------------------------------------------------- comprobación
def _notes(prof, version: str | None) -> str | None:
    tpl = NOTES.get(prof.key)
    if not tpl or not version:
        return None
    nums = re.findall(r"\d+", version)
    return tpl.format(v=version.split(":")[0], mm="-".join(nums[:2]))


def check_device(dev, raw: str = "") -> dict:
    """Resultado de comprobar un equipo (no guarda nada). status: update | current | unknown."""
    prof = get_profile(dev.device_type)
    res: dict = {"status": "unknown", "current": dev.os_version}
    if not dev.os_version:
        return {**res, "detail": "noversion"}
    found = None
    if prof.key.startswith("mikrotik"):
        found = _mikrotik(dev, prof, raw)
    elif prof.key.startswith("ubiquiti_"):
        found = _ubiquiti(dev, prof)
    elif prof.key == "ubuntu_server":
        found = _distro(dev)
    if found is None and prof.cpe:
        cpe = prof.cpe if isinstance(prof.cpe, str) else prof.cpe[0]
        cur = device_cpe_version(prof, dev.os_version)
        if cur:
            fam = family(prof)
            known = nvd_versions(cpe)
            target, latest = pick(cur, known, fam)
            like = dev.os_version
            found = {"source": "nvd", "target": pretty(target or latest, fam, like),
                     "latest": pretty(latest or target, fam, like),
                     "notes_url": _notes(prof, target or latest), "target_cpe": target or latest}
            if not found["target"]:
                if cur not in known and (not known or vkey(cur) >= vkey(max(known, key=vkey))):
                    # NVD no conoce ninguna versión igual o posterior a la instalada: no se puede saber si hay otra
                    return {**res, "source": "nvd", "detail": "nvd_behind"}
                found["latest"] = pretty(max(known + [cur], key=vkey), fam, like)
    if found is None:
        return {**res, "detail": "nosource"}
    res.update(found)
    res["status"] = "update" if res.get("target") else "current"
    return res


def _save(dev_id: int, res: dict, error: str | None = None):
    with SessionLocal() as db:
        row = db.get(FirmwareUpdate, dev_id) or FirmwareUpdate(device_id=dev_id)
        if error:
            row.status, row.detail = "error", error[:1000]
        else:
            row.status, row.source = res["status"], res.get("source")
            row.current, row.target, row.latest = res.get("current"), res.get("target"), res.get("latest")
            row.notes_url, row.changelog = res.get("notes_url"), res.get("changelog")
            row.download_url, row.sha256, row.size = res.get("download_url"), res.get("sha256"), res.get("size")
            row.detail = res.get("detail")
            if res.get("fixed") is not None:
                row.fixed_cves, row.fixed_for, row.open_cves = json.dumps(res["fixed"]), res.get("target"), res.get("open")
            elif row.fixed_for != res.get("target"):
                row.fixed_cves = row.fixed_for = row.open_cves = None
        row.checked_at = now()
        db.merge(row)
        db.commit()


_state = {"running": False, "started": None, "done": 0, "total": 0}
_lock = threading.Lock()


def state() -> dict:
    return dict(_state)


def start_check(device_ids: list[int] | None = None) -> bool:
    """Lanza la comprobación en segundo plano (todos los equipos o algunos). False si ya hay una en curso."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, started=time.time(), done=0, total=0)
    services.jobs.submit(_run, device_ids)
    return True


def _run(device_ids=None):
    try:
        check_all(device_ids)
    except Exception:  # noqa: BLE001
        log.exception("Comprobación de actualizaciones de firmware")
    finally:
        with _lock:
            _state["running"] = False


def scheduled():
    if get_setting("fw_update_check", "1") != "0":
        start_check()


def check_all(device_ids: list[int] | None = None) -> int:
    """Comprueba los equipos, calcula los CVE que corrige cada versión nueva y avisa una vez por versión."""
    from .crypto import decrypt
    with SessionLocal() as db:
        q = select(Device).where(Device.enabled.is_(True))
        if device_ids:
            q = select(Device).where(Device.id.in_(device_ids))
        devs = list(db.scalars(q.order_by(Device.name)))
        raws = {}
        for d in devs:
            try:
                raws[d.id] = decrypt(d.inventory_raw_enc) if d.inventory_raw_enc else ""
            except Exception:  # noqa: BLE001
                raws[d.id] = ""
        db.expunge_all()
    _state["total"] = len(devs)
    found = 0
    for d in devs:
        try:
            res = check_device(d, raws.get(d.id, ""))
            prof = get_profile(d.device_type)
            if res["status"] == "update" and prof.cpe:
                cur = device_cpe_version(prof, d.os_version)
                new = res.get("target_cpe") or device_cpe_version(prof, res["target"])
                try:
                    res["fixed"], res["open"] = fixed_cves(prof, cur, new)
                except Exception as e:  # noqa: BLE001 — sin NVD se avisa igual, sin la lista de CVE
                    log.info("CVE corregidos de %s: %s", d.name, e)
            _save(d.id, res)
            if res["status"] == "update":
                found += 1
                _notify(d, res)
        except Exception as e:  # noqa: BLE001
            log.info("Actualizaciones de firmware de %s: %s", d.name, e)
            _save(d.id, {}, error=str(e) or type(e).__name__)
        _state["done"] += 1
    set_setting("fw_update_checked_at", now().isoformat(timespec="minutes"))
    return found


def _notify(dev, res: dict):
    with SessionLocal() as db:
        row = db.get(FirmwareUpdate, dev.id)
        if not row or row.notified == res["target"]:
            return
        row.notified = res["target"]
        db.commit()
    fixed = len(res.get("fixed") or [])
    msg = f"{dev.name}: firmware {res['target']} disponible (instalado {dev.os_version})"
    if fixed:
        msg += f"; corrige {fixed} CVE"
    services.add_event("info", "Actualización de firmware", msg, dev.id)


# --------------------------------------------------------------------------- descarga a la biblioteca
_dl: dict[int, dict] = {}


def download_state(device_id: int) -> dict | None:
    return _dl.get(device_id)


def start_download(device_id: int, username: str) -> bool:
    with _lock:
        if (_dl.get(device_id) or {}).get("running"):
            return False
        _dl[device_id] = {"running": True, "error": None, "image": None, "at": time.time()}
    services.jobs.submit(_download_job, device_id, username)
    return True


def _download_job(device_id: int, username: str):
    st = _dl[device_id]
    try:
        st["image"] = download(device_id, username)
        services.add_event("info", "Firmware descargado", f"{st['image']} guardado en la biblioteca de Firmware",
                           device_id, notify=False)
    except Exception as e:  # noqa: BLE001
        st["error"] = str(e) or type(e).__name__
        log.warning("Descarga de firmware para %s: %s", device_id, e)
    finally:
        st["running"] = False


def download(device_id: int, username: str) -> str:
    """Descarga el firmware recomendado a la biblioteca, verificando su SHA-256. Devuelve el nombre del archivo."""
    from .firmware import FIRMWARE_DIR, SAFE_NAME
    with SessionLocal() as db:
        row = db.get(FirmwareUpdate, device_id)
        dev = db.get(Device, device_id)
        if not row or not row.download_url or not dev:
            raise RuntimeError("Este equipo no tiene una descarga pública disponible")
        url, sha, version, dtype = row.download_url, row.sha256, row.target, dev.device_type
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in DOWNLOAD_HOSTS:
        raise RuntimeError(f"Origen de descarga no permitido: {u.hostname}")
    name = Path(u.path).name
    if not SAFE_NAME.match(name):
        name = re.sub(r"[^A-Za-z0-9._+-]", "_", name)[-200:]
    if u.hostname == "download.mikrotik.com" and not sha:  # MikroTik publica «archivo.npk.sha256» al lado
        sha = _get(url + ".sha256", timeout=(10, 30)).text.split()[0].lower()
    if not sha or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise RuntimeError("El fabricante no publica la suma SHA-256: no se descarga sin poder verificarla")
    with SessionLocal() as db:
        same = db.scalars(select(FirmwareImage).where(FirmwareImage.filename == name)).first()
        if same and (FIRMWARE_DIR / name).exists():
            return name
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FIRMWARE_DIR / (name + ".part")
    h256, h5, size = hashlib.sha256(), hashlib.md5(), 0
    try:
        with requests.get(url, headers=UA, stream=True, timeout=(10, 120)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise RuntimeError("El archivo supera el tamaño máximo (2 GB)")
                    f.write(chunk)
                    h256.update(chunk)
                    h5.update(chunk)
        if h256.hexdigest() != sha.lower():
            raise RuntimeError(f"La suma SHA-256 no coincide (esperada {sha[:12]}…, recibida {h256.hexdigest()[:12]}…): "
                               "se descartó el archivo")
        tmp.replace(FIRMWARE_DIR / name)
    finally:
        tmp.unlink(missing_ok=True)
    with SessionLocal() as db:
        img = db.scalars(select(FirmwareImage).where(FirmwareImage.filename == name)).first() \
            or FirmwareImage(filename=name)
        img.size, img.md5, img.version = size, h5.hexdigest(), version
        img.device_type_filter, img.uploaded_by, img.uploaded_at = f"^{re.escape(dtype)}$", username, now()
        db.add(img)
        db.commit()
    return name


# --------------------------------------------------------------------------- lectura para las páginas
def in_library(device_type: str, version: str | None) -> str | None:
    """Nombre de una imagen de la biblioteca con esa versión y compatible con el tipo de equipo, si ya existe."""
    if not version:
        return None
    with SessionLocal() as db:
        for img in db.scalars(select(FirmwareImage).where(FirmwareImage.version == version)):
            try:
                if not img.device_type_filter or re.search(img.device_type_filter, device_type):
                    return img.filename
            except re.error:
                continue
    return None


def for_device(device_id: int) -> dict | None:
    with SessionLocal() as db:
        row = db.get(FirmwareUpdate, device_id)
        if not row:
            return None
        d = {c.name: getattr(row, c.name) for c in FirmwareUpdate.__table__.columns}
    try:
        d["fixed"] = json.loads(d["fixed_cves"]) if d.get("fixed_cves") else None
    except ValueError:
        d["fixed"] = None
    d["fixed_crit"] = sum(1 for _c, s in (d["fixed"] or []) if s == "CRITICAL")
    d["fixed_high"] = sum(1 for _c, s in (d["fixed"] or []) if s == "HIGH")
    d["needs_account"] = d.get("status") == "update" and not d.get("download_url")
    return d


def all_rows(scope=None) -> list[dict]:
    with SessionLocal() as db:
        devs = {d.id: d for d in db.scalars(select(Device)) if scope is None or d.id in scope}
        ids = [r.device_id for r in db.scalars(select(FirmwareUpdate)) if r.device_id in devs]
        names = {i: (devs[i].name, devs[i].device_type) for i in ids}
    out = []
    for i in ids:
        r = for_device(i)
        if r:
            r["name"], r["device_type"] = names[i]
            out.append(r)
    order = {"update": 0, "error": 1, "unknown": 2, "current": 3}
    return sorted(out, key=lambda r: (order.get(r["status"], 9), -(len(r["fixed"] or [])), r["name"].lower()))
