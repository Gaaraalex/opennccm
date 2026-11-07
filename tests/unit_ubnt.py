"""Ubiquiti: firmware disponible (catálogo fw-update.ui.com) y búsqueda de CVE para cada línea de producto."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import fwupdates as fw  # noqa: E402
from app.drivers import get_profile  # noqa: E402
from app.services import nvd_searches  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# extracto del catálogo real (product, platform, version)
CAT = [("XW", "airmax", "v6.3.26"), ("XW", "airmax", "v6.3.2-cs"), ("WA", "airmax", "v8.7.25"), ("WA", "airmax", "v8.7.4-cs"),
       ("af5xhd", "airfiber", "v1.5.7"), ("AF5X", "airfiber", "v4.1.0"), ("esx", "edgeswitch", "v1.11.1"),
       ("esxp", "edgeswitch", "v2.4.1"), ("unifi-firmware", "U7PG2", "v6.8.2.15592"), ("unifi-firmware", "UGW3", "v4.4.57.5578372"),
       ("unifi-firmware", "UGW4", "v4.4.57.5578372"), ("esgh", "edgeswitch", "v1.10.4.5602")]
fw._ubnt_catalog = lambda: [{"product": p, "platform": pl, "version": v, "url": "u", "sha256": "s", "size": 1} for p, pl, v in CAT]


def look(key, model, ver, raw=""):
    d = SimpleNamespace(model=model, os_version=ver, device_type=key)
    r = fw._ubiquiti(d, get_profile(key), raw)
    return r and (r.get("latest"), r.get("target"))


check("airMAX AC (WA): 8.7.25, sin las «-cs»", look("ubiquiti_airos", "LiteBeam 5AC Gen2", "8.7.4", "WA.v8.7.4.45112.210415.1103") == ("8.7.25", "8.7.25"))
check("airMAX M (XW, airOS 6)", look("ubiquiti_airos", "NanoStation M5", "6.3.26", "XW.v6.3.26.33617.211201.1150") == ("6.3.26", None))
check("airFiber 5XHD", look("ubiquiti_airfiber", "airFiber 5XHD", "1.4.2", "af5xhd.v1.4.2.123") == ("1.5.7", "1.5.7"))
check("AP UniFi por board.shortname", look("ubiquiti_unifi_ap", "UAP-AC-Pro-Gen2", "6.5.55", "Model: UAP-AC-Pro-Gen2\nboard.shortname=U7PG2") == ("6.8.2.15592", "6.8.2.15592"))
check("USG 3 y USG Pro 4", look("ubiquiti_usg", "UniFi-Gateway-3", "4.4.51") == ("4.4.57.5578372", "4.4.57.5578372")
      and look("ubiquiti_usg", "UniFi-Gateway-4", "4.4.57.5578372") == ("4.4.57.5578372", None))
check("EdgeSwitch X con Realtek (ES-18X)", look("ubiquiti_edgeswitch", "RTK ES-18X", "1.10.0", "Loader Version : 1.3.5\nFirmware Version : 1.10.0") == ("1.11.1", "1.11.1"))
check("EdgeSwitch FastPath sigue igual", look("ubiquiti_edgeswitch", "ES-24-Lite", "1.10.4") is None or look("ubiquiti_edgeswitch", "ES-24", "1.9.0") == ("1.10.4", "1.10.4"))
q = [x.get("virtualMatchString", "") for x in nvd_searches(get_profile("ubiquiti_airos"), "6.3.11")]
check("CVE airOS: también airMAX M y AC de NVD", "cpe:2.3:o:ui:airmax_m_xw_firmware:6.3.11" in q and "cpe:2.3:o:ui:airmax_ac_firmware:6.3.11" in q)
q = [x.get("virtualMatchString", "") for x in nvd_searches(get_profile("ubiquiti_airfiber"), "4.1.0")]
check("CVE airFiber por CPE (antes, búsqueda por texto)", "cpe:2.3:o:ui:af5x_firmware:4.1.0" in q)
q = [x.get("virtualMatchString", "") for x in nvd_searches(get_profile("ubiquiti_unifi_ap"), "6.5.55.14277")]
check("CVE AP UniFi por CPE", "cpe:2.3:o:ui:unifi_uap_firmware:6.5.55" in q)
check("USG: aviso de que NVD no tiene su producto", get_profile("ubiquiti_usg").cve_note[0] == "ubiquiti")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
