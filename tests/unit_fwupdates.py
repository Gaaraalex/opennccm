"""Actualizaciones de firmware con fuentes simuladas: orden de versiones y ramas, MikroTik (canales, novedades y
archivo por arquitectura), Ubiquiti (modelo → producto del catálogo), NVD (diccionario de versiones), CVE corregidos,
aviso una sola vez por versión y descarga verificada con SHA-256."""
import hashlib
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import requests  # noqa: E402

from app import fwupdates as fw  # noqa: E402
from app import services  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import Device, Event, FirmwareImage, FirmwareUpdate, SessionLocal, init_db, select, set_setting  # noqa: E402
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- versiones y ramas
check("orden natural: 7.2.13 > 7.2.5", fw.vkey("7.2.13") > fw.vkey("7.2.5"))
check("orden natural: 15.2(4)m10 > 15.2(4)m7", fw.vkey("15.2(4)m10") > fw.vkey("15.2(4)m7"))
check("orden natural: 21.4:r3-s5 > 21.4:r3 y < 21.4:r4", fw.vkey("21.4:r3") < fw.vkey("21.4:r3-s5") < fw.vkey("21.4:r4"))
check("orden natural: 2.0.9-hotfix.7 > 2.0.9", fw.vkey("2.0.9-hotfix.7") > fw.vkey("2.0.9"))
FORTI = ["7.0.12", "7.0.15", "7.2.4", "7.2.5", "7.2.11", "7.2.13", "7.4.0", "7.6.6", "7.6.7", "7.6.8-beta", "8.0.0:rc1"]
check("FortiOS 7.2.5: la más nueva de su rama (7.2.13) y la más reciente (7.6.7), sin betas ni RC",
      fw.pick("7.2.5", FORTI, "default") == ("7.2.13", "7.6.7"), fw.pick("7.2.5", FORTI, "default"))
JUN = ["21.4", "21.4:r1", "21.4:r3", "21.4:r3-s5", "21.4:r3-s12", "22.2:r1", "23.4:r1", "23.4:r2-s3", "15.1x49:d200"]
check("Junos 21.4R3-S5: misma rama 21.4R3-S12; más reciente 23.4R2-S3; ignora «21.4» sin actualización",
      fw.pick("21.4:r3-s5", JUN, "junos") == ("21.4:r3-s12", "23.4:r2-s3"), fw.pick("21.4:r3-s5", JUN, "junos"))
IOS = ["15.2(4)m7", "15.2(4)m9", "15.2(4)m11", "15.2(7)e8", "15.9(3)m13"]
check("Cisco IOS 15.2(4)M7: solo la misma serie (M11), nunca otra serie que depende del hardware",
      fw.pick("15.2(4)m7", IOS, "cisco_ios") == ("15.2(4)m11", "15.2(4)m11"), fw.pick("15.2(4)m7", IOS, "cisco_ios"))
WLC = ["8.10.130.0", "8.10.185.0", "8.10.190.0", "16.088.0"]
check("una entrada suelta del diccionario («16.088.0») no se recomienda como la más reciente",
      fw.pick("8.10.130.0", WLC, "default") == ("8.10.190.0", "8.10.190.0"), fw.pick("8.10.130.0", WLC, "default"))
check("al día: nada más nuevo", fw.pick("7.6.7", FORTI, "default") == (None, None))
check("presentación: Junos «21.4:r3-s12» → «21.4R3-S12» y «15.1x49:d200» → «15.1X49-D200»",
      fw.pretty("21.4:r3-s12", "junos") == "21.4R3-S12" and fw.pretty("15.1x49:d200", "junos") == "15.1X49-D200")
check("presentación: PAN-OS «10.2.18:h12» → «10.2.18-h12»; EOS con mayúsculas si el equipo las usa",
      fw.pretty("10.2.18:h12", "default") == "10.2.18-h12" and fw.pretty("4.28.7m", "default", "4.28.3M") == "4.28.7M")

for dtype, ver, want in (("aruba_osswitch", "WC.16.10.0012", "16.10.0012"), ("checkpoint_gaia", "R81.10", "r81.10"),
                         ("pfsense", "2.7.2-RELEASE", "2.7.2"), ("extreme_exos", "32.6.3.126", "32.6.3"),
                         ("cisco_wlc", "8.10.130.0", "8.10.130.0"), ("f5_tmsh", "16.1.3.1", "16.1.3")):
    p = get_profile(dtype)
    check(f"{dtype}: CPE de NVD y versión «{ver}» → «{want}»",
          p.cpe and fw.device_cpe_version(p, ver) == want, (p.cpe, fw.device_cpe_version(p, ver)))
check("presentación: Check Point «r81.10:take_192» → «R81.10 Take 192»",
      fw.pretty("r81.10:take_192", "default", "R81.10") == "R81.10 Take 192")

# ---------------------------------------------------------------- MikroTik y Ubiquiti: archivo y producto
check("MikroTik v7 arm64: routeros-7.24.5-arm64.npk", fw.mikrotik_file("7.24.5", "arm64") == "routeros-7.24.5-arm64.npk")
check("MikroTik v7 x86: routeros-7.24.5.npk", fw.mikrotik_file("7.24.5", "x86_64") == "routeros-7.24.5.npk")
check("MikroTik v6 mipsbe: routeros-mipsbe-6.49.23.npk", fw.mikrotik_file("6.49.23", "mipsbe") == "routeros-mipsbe-6.49.23.npk")
check("MikroTik sin arquitectura: no se elige archivo", fw.mikrotik_file("7.24.5", None) is None)
check("EdgeSwitch con PoE («150W») → línea ESWH; sin PoE → ESGH",
      fw.ubnt_product("ubiquiti_edgeswitch", "EdgeSwitch 16-Port 150W") == ("eswh", "edgeswitch")
      and fw.ubnt_product("ubiquiti_edgeswitch", "EdgeSwitch 24 Lite") == ("esgh", "edgeswitch"))
for model, prod in (("EdgeRouter X 5-Port", "e50"), ("EdgeRouter Lite 3-Port", "e100"), ("EdgeRouter 4", "e300"),
                    ("EdgeRouter 12P", "e300"), ("EdgeRouter Pro 8-Port", "e200"), ("EdgeRouter Infinity", "e1000")):
    check(f"EdgeRouter «{model}» → {prod}", fw.ubnt_product("ubiquiti_edgerouter", model) == (prod, "edgerouter"),
          fw.ubnt_product("ubiquiti_edgerouter", model))
check("UniFi Switch «US-24-250W» → plataforma US24P250",
      fw.ubnt_product("ubiquiti_unifiswitch", "US-24-250W") == ("unifi-firmware", "US24P250"))

# ---------------------------------------------------------------- red simulada
FILE = b"firmware de prueba " * 1000
SHA = hashlib.sha256(FILE).hexdigest()
CATALOG = {"_embedded": {"firmware": [
    {"product": "eswh", "platform": "edgeswitch", "version": "v1.12.2", "sha256_checksum": SHA, "file_size": len(FILE),
     "_links": {"data": {"href": "https://fw-download.ubnt.com/data/es/ES-eswh-1.12.2.stk"}}},
    {"product": "eswh", "platform": "edgeswitch", "version": "v1.12.2-lite", "sha256_checksum": SHA,
     "_links": {"data": {"href": "https://fw-download.ubnt.com/data/es/ES-eswh-1.12.2-lite.stk"}}},
    {"product": "e50", "platform": "edgerouter", "version": "v3.0.1", "sha256_checksum": SHA,
     "_links": {"data": {"href": "https://fw-download.ubnt.com/data/er/e50-3.0.1.tar"}}},
    {"product": "unifi-firmware", "platform": "US24P250", "version": "v7.5.15+17146", "sha256_checksum": SHA,
     "_links": {"data": {"href": "https://fw-download.ubnt.com/data/unifi/US24P250-7.5.15.bin"}}}]}}
CPES = {"cpe:2.3:o:fortinet:fortios": ["7.2.5", "7.2.13", "7.6.6", "7.6.7"], "cpe:2.3:a:avaya:vsp_operating_system_software": ["4.2.1"]}
calls = []


class Resp:
    def __init__(self, status=200, text="", data=None, content=b""):
        self.status_code, self.text, self._data, self._content = status, text, data, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._content), n):
            yield self._content[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_get(url, headers=None, params=None, timeout=None, stream=False):
    calls.append(url)
    if url.endswith("NEWESTa7.stable"):
        return Resp(text="7.24.5 1790691687")
    if url.endswith("NEWESTa7.long-term"):
        return Resp(text="7.23.8 1791452776")
    if url.endswith("NEWEST6.long-term") or url.endswith("NEWEST6.stable"):
        return Resp(text="6.49.23 1791389116")
    if url.endswith("/7.24.5/CHANGELOG"):
        return Resp(text="What's new in 7.24.5 (2026-09-29):\n\n*) bridge - fix DHCP snooping;\n")
    if url.endswith(".npk.sha256"):
        return Resp(text=f"{SHA}  routeros-7.24.5-arm64.npk")
    if url.endswith(".npk") or "fw-download.ubnt.com" in url:
        return Resp(content=FILE)
    if url.startswith(fw.UBNT_API.split("?")[0]):
        return Resp(data=CATALOG)
    if "cpes/2.0" in url:
        vers = CPES.get(params["cpeMatchString"], [])
        prods = [{"cpe": {"cpeName": f"{params['cpeMatchString']}:{v}:*:*:*:*:*:*:*"}} for v in vers]
        return Resp(data={"totalResults": len(prods), "products": prods})
    return Resp(status=404)


fw.requests.get = fake_get
services._nvd_wait = lambda sleep, clock: None
fw.time.sleep = lambda s: None
CVES = {"7.2.5": {"CVE-2024-1": "CRITICAL", "CVE-2024-2": "HIGH", "CVE-2024-3": "MEDIUM"},
        "7.2.13": {"CVE-2024-3": "MEDIUM"}, "7.12.1": {"CVE-2023-9": "HIGH"}, "7.24.5": {}}
fw.cves_for = lambda prof, ver: CVES.get(ver, {})

fixed, still = fw.fixed_cves(get_profile("fortinet"), "7.2.5", "7.2.13")
check("CVE corregidos: los de la versión instalada que ya no tiene la nueva, los críticos primero",
      fixed == [["CVE-2024-1", "CRITICAL"], ["CVE-2024-2", "HIGH"]] and still == 1, (fixed, still))

init_db()
with SessionLocal() as db:
    devs = [Device(name="MK-Borde", host="10.0.0.1", device_type="mikrotik_routeros", os_version="7.12.1",
                   model="RB5009UG+S+", inventory_raw_enc=encrypt("version: 7.12.1 (stable)\narchitecture-name: arm64\n")),
            Device(name="SW-Bodega", host="10.0.0.2", device_type="ubiquiti_edgeswitch", os_version="1.9.3.5263085",
                   model="EdgeSwitch 16-Port 150W"),
            Device(name="ER-Sucursal", host="10.0.0.3", device_type="ubiquiti_edgerouter", os_version="2.0.9-hotfix.7",
                   model="EdgeRouter X 5-Port"),
            Device(name="USW-Piso2", host="10.0.0.4", device_type="ubiquiti_unifiswitch", os_version="6.6.65.15779",
                   model="US-24-250W"),
            Device(name="FGT-Central", host="10.0.0.5", device_type="fortinet", os_version="7.2.5", model="FortiGate-60F"),
            Device(name="VSP-Core", host="10.0.0.6", device_type="extreme_vsp", os_version="8.10.0.0", model="VSP-8284"),
            Device(name="Z-sin-fuente", host="10.0.0.7", device_type="zte_zxros", os_version="5.0", model="ZXR10"),
            Device(name="MK-Nuevo", host="10.0.0.8", device_type="mikrotik_routeros", os_version="7.24.5", model="hEX")]
    db.add_all(devs)
    db.commit()
    ids = {d.name: d.id for d in devs}

n = fw.check_all()
check("se encuentran 5 equipos con firmware nuevo", n == 5, n)
r = fw.for_device(ids["MK-Borde"])
check("MikroTik 7.12.1 → 7.24.5 con novedades, notas, archivo arm64 y CVE corregidos",
      r["status"] == "update" and r["target"] == "7.24.5" and "DHCP snooping" in (r["changelog"] or "")
      and r["download_url"] == "https://download.mikrotik.com/routeros/7.24.5/routeros-7.24.5-arm64.npk"
      and r["notes_url"] and r["fixed"] == [["CVE-2023-9", "HIGH"]], r)
r = fw.for_device(ids["SW-Bodega"])
check("EdgeSwitch 1.9.3 → 1.12.2 (no la versión «-lite»), con descarga y SHA-256",
      r["status"] == "update" and r["target"] == "1.12.2" and r["download_url"].endswith("ES-eswh-1.12.2.stk")
      and r["sha256"] == SHA, r)
r = fw.for_device(ids["ER-Sucursal"])
check("EdgeRouter X 2.0.9 → 3.0.1", r["status"] == "update" and r["target"] == "3.0.1", r)
r = fw.for_device(ids["USW-Piso2"])
check("UniFi US-24-250W 6.6.65 → 7.5.15", r["status"] == "update" and r["target"] == "7.5.15", r)
r = fw.for_device(ids["FGT-Central"])
check("FortiGate 7.2.5 → 7.2.13 (más reciente 7.6.7), sin descarga: requiere cuenta, con notas de Fortinet",
      r["status"] == "update" and r["target"] == "7.2.13" and r["latest"] == "7.6.7" and r["needs_account"]
      and r["notes_url"] == "https://docs.fortinet.com/document/fortigate/7.2.13/fortios-release-notes"
      and r["fixed_crit"] == 1, r)
r = fw.for_device(ids["VSP-Core"])
check("VOSS: NVD no conoce versiones posteriores → «no se puede saber», no «al día»",
      r["status"] == "unknown" and r["detail"] == "nvd_behind", r)
check("equipo sin CPE ni fuente: «sin fuente pública»", fw.for_device(ids["Z-sin-fuente"])["detail"] == "nosource")
r = fw.for_device(ids["MK-Nuevo"])
check("MikroTik ya en 7.24.5: al día", r["status"] == "current", r)

with SessionLocal() as db:
    evs = [e for e in db.scalars(select(Event)) if e.category == "Actualización de firmware"]
check("un aviso por equipo con firmware nuevo", len(evs) == 5, [e.message for e in evs])
check("el aviso dice la versión y cuántos CVE corrige",
      any("FGT-Central: firmware 7.2.13 disponible" in e.message and "corrige 2 CVE" in e.message for e in evs))
fw.check_all()
with SessionLocal() as db:
    evs2 = [e for e in db.scalars(select(Event)) if e.category == "Actualización de firmware"]
check("no se repite el aviso de la misma versión al volver a comprobar", len(evs2) == 5, len(evs2))

# ---------------------------------------------------------------- descarga a la biblioteca
from app.firmware import FIRMWARE_DIR  # noqa: E402

name = fw.download(ids["MK-Borde"], "admin")
with SessionLocal() as db:
    img = db.scalars(select(FirmwareImage).where(FirmwareImage.filename == name)).first()
check("MikroTik: se descarga, se verifica con el «.sha256» del fabricante y queda en la biblioteca",
      name == "routeros-7.24.5-arm64.npk" and img and img.version == "7.24.5" and img.size == len(FILE)
      and img.md5 == hashlib.md5(FILE).hexdigest() and img.device_type_filter == "^mikrotik_routeros$"
      and (FIRMWARE_DIR / name).read_bytes() == FILE)
check("la descarga sale solo del sitio del fabricante", any(c.endswith(".npk.sha256") for c in calls))

with SessionLocal() as db:
    row = db.get(FirmwareUpdate, ids["ER-Sucursal"])
    row.sha256 = "0" * 64
    db.commit()
try:
    fw.download(ids["ER-Sucursal"], "admin")
    check("si la suma SHA-256 no coincide, se descarta el archivo", False)
except RuntimeError as e:
    check("si la suma SHA-256 no coincide, se descarta el archivo",
          "no coincide" in str(e) and not (FIRMWARE_DIR / "e50-3.0.1.tar").exists()
          and not (FIRMWARE_DIR / "e50-3.0.1.tar.part").exists(), str(e))

with SessionLocal() as db:
    row = db.get(FirmwareUpdate, ids["USW-Piso2"])
    row.download_url = "https://evil.example.com/US24P250-7.5.15.bin"
    db.commit()
try:
    fw.download(ids["USW-Piso2"], "admin")
    check("solo se descarga de los sitios oficiales", False)
except RuntimeError as e:
    check("solo se descarga de los sitios oficiales", "no permitido" in str(e), str(e))
try:
    fw.download(ids["FGT-Central"], "admin")
    check("FortiGate: no hay descarga pública", False)
except RuntimeError:
    check("FortiGate: no hay descarga pública", True)

# ---------------------------------------------------------------- Ubuntu y Debian (distro-info-data)
from datetime import date  # noqa: E402

UBU_CSV = ("version,codename,series,created,release,eol,eol-server,eol-esm,eol-legacy\n"
           "20.04 LTS,Focal Fossa,focal,2019-10-17,2020-04-23,2025-05-29,2025-05-29,2030-04-23,2035-04-26\n"
           "24.04 LTS,Noble Numbat,noble,2023-10-12,2024-04-25,2029-05-31,2029-05-31,2034-04-25,2039-04-28\n"
           "25.10,Questing Quokka,questing,2025-04-24,2025-10-09,2026-07-09,,,\n"
           "26.04 LTS,Resolute Raccoon,resolute,2025-10-09,2026-04-23,2031-05-29,2031-05-29,2036-04-23,2041-04-30\n"
           "26.10,Stonking Stingray,stonking,2026-04-23,2026-10-15,2027-07-15,,,\n"
           "28.04 LTS,Futuro,futuro,2027-10-14,2028-04-20,2033-05-31,2033-05-31,,\n")
DEB_CSV = ("version,codename,series,created,release,eol,eol-lts,eol-elts\n"
           "12,Bookworm,bookworm,2021-08-14,2023-06-10,2026-07-11,2028-06-30,2033-06-30\n"
           "13,Trixie,trixie,2023-06-10,2025-08-09,2028-08-09,2030-06-30,2035-06-30\n"
           "14,Forky,forky,2025-08-09,,,,\n")
_prev_get = fw.requests.get


def distro_get(url, headers=None, params=None, timeout=None, stream=False):
    if url.endswith("/ubuntu.csv"):
        return Resp(text=UBU_CSV)
    if url.endswith("/debian.csv"):
        return Resp(text=DEB_CSV)
    return _prev_get(url, headers=headers, params=params, timeout=timeout, stream=stream)


fw.requests.get = distro_get
HOY = date(2026, 10, 25)
r = fw._distro(Device(device_type="ubuntu_server", os_version="20.04.6 LTS"), HOY)
check("Ubuntu 20.04: nueva LTS 26.04 (no la 26.10 intermedia ni una futura) y sin soporte estándar desde 2025-05-29",
      r["target"] == "26.04 LTS" and r["detail"] == "sinsoporte:2025-05-29:2030-04-23"
      and r["notes_url"] == "https://documentation.ubuntu.com/release-notes/26.04/", r)
r = fw._distro(Device(device_type="ubuntu_server", os_version="24.04.3 LTS"), HOY)
check("Ubuntu 24.04: puede pasar a 26.04 y tiene soporte hasta 2029-05-31",
      r["target"] == "26.04 LTS" and r["detail"] == "soporte:2029-05-31", r)
r = fw._distro(Device(device_type="ubuntu_server", os_version="26.04 LTS"), HOY)
check("Ubuntu 26.04: al día, con soporte hasta 2031-05-29", "target" not in r and r["detail"] == "soporte:2031-05-29", r)
r = fw._distro(Device(device_type="ubuntu_server", os_version="12"), HOY)
check("Debian 12: nueva 13 (no la 14 aún sin publicar) y ya sin soporte de seguridad",
      r["target"] == "13" and r["detail"] == "sinsoporte:2026-07-11:2028-06-30"
      and r["notes_url"] == "https://www.debian.org/releases/trixie/releasenotes", r)
r = fw._distro(Device(device_type="ubuntu_server", os_version="13"), HOY)
check("Debian 13: al día, con soporte hasta 2028-08-09", "target" not in r and r["detail"] == "soporte:2028-08-09", r)
fw.requests.get = _prev_get

# ---------------------------------------------------------------- comprobación diaria desactivable
started = []
fw.start_check = lambda ids=None: started.append(1) or True
set_setting("fw_update_check", "0")
fw.scheduled()
check("con la comprobación diaria desactivada no se consulta nada", started == [])
set_setting("fw_update_check", "1")
fw.scheduled()
check("activada, la tarea diaria lanza la comprobación", started == [1])
check("la lista de la página pone primero los equipos con firmware nuevo",
      [r["status"] for r in fw.all_rows()][:5] == ["update"] * 5 and json.dumps(fw.all_rows(), default=str))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
