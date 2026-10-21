"""Búsqueda de CVE en NVD con un servidor simulado: páginas pequeñas, reintentos ante cortes («Response ended
prematurely»), límite de peticiones, páginas más pequeñas si la respuesta se corta, y el resultado anterior se conserva si
NVD no responde."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
os.environ.pop("NCM_NVD_API_KEY", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import requests  # noqa: E402

from app import services  # noqa: E402
from app.db import Device, SessionLocal, Vulnerability, init_db, select  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def cve(i, sev="HIGH"):
    return {"cve": {"id": f"CVE-2020-{i:04d}", "descriptions": [{"lang": "en", "value": f"bug {i}"}],
                    "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 7.5, "baseSeverity": sev}}]}}}


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeNvd:
    """NVD con `total` CVE. `script` dice qué pasa en cada petición: "ok", "cut" (conexión cortada), "429", "500"."""

    def __init__(self, total, script=()):
        self.total, self.script, self.calls = total, list(script), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params))
        what = self.script.pop(0) if self.script else "ok"
        if what == "cut":
            raise requests.exceptions.ChunkedEncodingError(
                "('Connection broken: IncompleteRead', 'Response ended prematurely')")
        if what in ("429", "500"):
            return Resp(int(what))
        start, n = params["startIndex"], params["resultsPerPage"]
        items = [cve(i) for i in range(start, min(start + n, self.total))]
        return Resp(200, {"totalResults": self.total, "vulnerabilities": items})


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 1))
        self.t += max(0, s)


def run(fake, params=None):
    services._nvd_sent.clear()
    clk = Clock()
    orig = services.requests.get
    services.requests.get = fake.get
    try:
        return services.nvd_query(params or {"virtualMatchString": "cpe:2.3:o:cisco:ios:12.4\\(25d\\)"},
                                  sleep=clk.sleep, clock=clk), clk
    finally:
        services.requests.get = orig


# 1) 60 CVE (como Cisco IOS 12.4(25d)): dos páginas de 40, nunca 200 de golpe
fake = FakeNvd(60)
items, clk = run(fake)
check("60 CVE en páginas de 40 (2 peticiones)", len(items) == 60 and len(fake.calls) == 2
      and [c["resultsPerPage"] for c in fake.calls] == [40, 40] and [c["startIndex"] for c in fake.calls] == [0, 40],
      fake.calls)
check("la búsqueda va en cada página", all(c.get("virtualMatchString") for c in fake.calls))
check("sin esperas cuando no se supera el límite de NVD", clk.slept == [], clk.slept)

# 2) la conexión se corta («Response ended prematurely»): se reintenta con páginas más pequeñas
fake = FakeNvd(60, ["cut", "cut", "ok"])
items, clk = run(fake)
check("reintenta tras «Response ended prematurely» y obtiene todo", len(items) == 60, len(items))
check("tras cada corte la página se reduce a la mitad (40 → 20 → 10)",
      [c["resultsPerPage"] for c in fake.calls][:3] == [40, 20, 10], [c["resultsPerPage"] for c in fake.calls])
check("sin CVE repetidos ni perdidos", len({i["cve"]["id"] for i in items}) == 60)

# 3) límite de peticiones (429) y error del servidor (500): se reintenta
fake = FakeNvd(30, ["429", "500", "ok"])
items, clk = run(fake)
check("reintenta ante 429 y 500", len(items) == 30 and len(fake.calls) == 3, len(fake.calls))

# 4) límite público de NVD: 5 peticiones cada 30 s
fake = FakeNvd(300)
items, clk = run(fake)
check("300 CVE: 8 páginas", len(items) == 300 and len(fake.calls) == 8, len(fake.calls))
check("espera al pasar de 5 peticiones en 30 s", any(s >= 29 for s in clk.slept), clk.slept)

# 5) NVD no responde: error claro, sin quedarse colgado
fake = FakeNvd(60, ["cut"] * 10)
try:
    run(fake)
    check("tras 4 intentos fallidos se informa del error", False)
except services.NvdError as e:
    check("tras 4 intentos fallidos se informa del error", len(fake.calls) == 4 and "4 intentos" in str(e), str(e))

# 6) scan_cves: guarda los CVE y, si NVD falla después, conserva el resultado anterior
init_db()
with SessionLocal() as db:
    dev = Device(name="SW Cisco", host="10.0.0.154", device_type="cisco_ios", os_version="12.4(25d)")
    db.add(dev)
    db.commit()
    dev_id = dev.id
services.refresh_kev = lambda: None
orig = services.requests.get
services._nvd_sent.clear()
services.requests.get = FakeNvd(60, ["cut", "ok"]).get
real_sleep = services.time.sleep
services.time.sleep = lambda s: None
try:
    n = services.scan_cves(dev_id)
    check("scan_cves guarda los 60 CVE pese a un corte", n == 60, n)
    services._nvd_sent.clear()
    services.requests.get = FakeNvd(60, ["cut"] * 10).get
    try:
        services.scan_cves(dev_id)
        check("scan_cves informa si NVD no responde", False)
    except services.NvdError:
        check("scan_cves informa si NVD no responde", True)
    with SessionLocal() as db:
        kept = db.scalars(select(Vulnerability).where(Vulnerability.device_id == dev_id)).all()
    check("si NVD falla se conservan los CVE anteriores", len(kept) == 60, len(kept))
finally:
    services.requests.get = orig
    services.time.sleep = real_sleep

# 7) qué se busca en NVD según el equipo
from app.drivers import get_profile  # noqa: E402

s = services.nvd_searches(get_profile("ubiquiti_edgeswitch"), "1.9.3.5263085")
check("EdgeSwitch: se busca por sus CPE con la versión sin n.º de compilación",
      s == [{"virtualMatchString": "cpe:2.3:o:ui:edgeswitch_firmware:1.9.3"},
            {"virtualMatchString": "cpe:2.3:o:ubnt:edgeswitch_firmware:1.9.3"}], s)
s = services.nvd_searches(get_profile("ubiquiti_unifiswitch"), "6.6.65.15779")
check("UniFi Switch: CPE unifi_switch_firmware", s == [{"virtualMatchString": "cpe:2.3:o:ui:unifi_switch_firmware:6.6.65"}], s)
s = services.nvd_searches(get_profile("ubiquiti_edgerouter"), "2.0.9-hotfix.7")
check("EdgeRouter: EdgeOS y firmware EdgeMAX con «2.0.9»", [x["virtualMatchString"] for x in s] ==
      ["cpe:2.3:o:ui:edgeos:2.0.9", "cpe:2.3:o:ui:edgemax_edgerouter_firmware:2.0.9"], s)
s = services.nvd_searches(get_profile("cisco_ios"), "12.4(25d)")
check("Cisco IOS: la versión se escapa como pide NVD", s == [{"virtualMatchString": "cpe:2.3:o:cisco:ios:12.4\\(25d\\)"}], s)
for v, want in (("21.4R3-S5.4", "21.4:r3-s5"), ("22.2R1.9", "22.2:r1"), ("15.1X49-D200.3", "15.1x49:d200"),
                ("18.4R2-S7", "18.4:r2-s7")):
    s = services.nvd_searches(get_profile("juniper_junos"), v)
    check(f"Junos {v}: versión y actualización separadas como en NVD ({want})",
          s == [{"virtualMatchString": f"cpe:2.3:o:juniper:junos:{want}"}], s)
s = services.nvd_searches(get_profile("fortinet"), "7.2.5")
check("FortiGate: CPE fortios", s == [{"virtualMatchString": "cpe:2.3:o:fortinet:fortios:7.2.5"}], s)
s = services.nvd_searches(get_profile("mikrotik_routeros"), "7.12.1")
check("MikroTik: CPE routeros", s == [{"virtualMatchString": "cpe:2.3:o:mikrotik:routeros:7.12.1"}], s)
s = services.nvd_searches(get_profile("watchguard_fireware"), "12.10.3.B704297")
check("WatchGuard: CPE fireware con la versión sin compilación",
      s == [{"virtualMatchString": "cpe:2.3:o:watchguard:fireware:12.10.3"}], s)
from app.drivers import parse_inventory  # noqa: E402

for sysinfo in ("System Name     : WG-Central\nModel           : M370\nSerial Number   : 80B2012345678\n"
                "Software Version: 12.10.3.B704297\n",
                "model: T45\nserial_number: 2B42012345678\nsystem_version: 12.8.2.B666625\n",
                "WatchGuard Fireware OS v12.9.4\nModel: M590\nSerial No: 80C1012345678\n"):
    inv = parse_inventory(sysinfo, get_profile("watchguard_fireware"))
    check(f"WatchGuard: inventario de «show sysinfo» ({inv.get('model')})",
          inv.get("os_version", "").startswith("12.") and inv.get("model") and inv.get("serial"), inv)
vsp = get_profile("extreme_vsp")
check("VOSS: se busca por su CPE (Avaya VSP OS) y avisa de los avisos de Extreme",
      services.nvd_searches(vsp, "8.10.0.0")[0]["virtualMatchString"].startswith("cpe:2.3:a:avaya:vsp_operating_system")
      and vsp.cve_note and vsp.cve_note[1].startswith("https://www.extremenetworks.com/"))

# 8) búsqueda en segundo plano: el botón no espera a NVD y no se lanzan dos búsquedas a la vez
import threading  # noqa: E402

gate = threading.Event()
orig_scan = services.scan_cves
services.scan_cves = lambda i: (gate.wait(10), 7)[1]
try:
    started = services.start_cve_scan(dev_id)
    again = services.start_cve_scan(dev_id)
    st = services.cve_scan_state(dev_id)
    check("la búsqueda arranca en segundo plano y responde al instante", started and st["running"] is not None)
    check("no se lanza una segunda búsqueda del mismo equipo mientras hay una en curso", again is False)
    gate.set()
    for _ in range(50):
        if services.cve_scan_state(dev_id)["running"] is None:
            break
        services.time.sleep(0.1)
    last = services.cve_scan_state(dev_id)["last"]
    check("al terminar queda el resultado (7 CVE)", last and last["ok"] and last["n"] == 7, last)
    services.scan_cves = lambda i: (_ for _ in ()).throw(services.NvdError(4, "Response ended prematurely"))
    services.start_cve_scan(dev_id)
    for _ in range(50):
        if services.cve_scan_state(dev_id)["running"] is None:
            break
        services.time.sleep(0.1)
    last = services.cve_scan_state(dev_id)["last"]
    check("si NVD falla queda el error con el número de intentos", last and not last["ok"] and last["tries"] == 4
          and "prematurely" in last["error"], last)
finally:
    services.scan_cves = orig_scan
check("se recuerda cuándo se consultó NVD aunque no haya CVE", services.cve_scanned_at(dev_id) is not None)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
