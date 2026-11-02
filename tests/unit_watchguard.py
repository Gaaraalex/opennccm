"""WatchGuard Fireware: el respaldo usa «export config to console» (Fireware no tiene «show running-config»), un error del
equipo en la segunda línea («^» y «% Invalid input…») no se guarda como versión y la feature key se lee y se guarda UNA
sola vez (los respaldos siguientes ya no la piden). Con un equipo simulado, sin red."""
import os
import sys
import tempfile

D = tempfile.mkdtemp()
open(os.path.join(D, "secret.key"), "wb").write(os.urandom(32))
os.environ.update(NCM_DATA_DIR=D, NCM_KEY_FILE=os.path.join(D, "secret.key"), NCM_FW_CHECK_NEW="0")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import services  # noqa: E402
from app.crypto import decrypt, encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, SessionLocal, init_db, select  # noqa: E402
from app.drivers import get_profile  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


XML = '<?xml version="1.0"?>\n<profile>\n<product-grade>2</product-grade>\n<system-parameters>\n</system-parameters>\n</profile>\n'
SYSINFO = "system name: fw-prueba\nmodel: T40\nserial number: D02807134F41D\nsoftware version: 12.8.1.B663143\n"
FEATURE = "Serial Number: D02807134F41D\nLicense ID: D02807134F41D\nName: fw-prueba\nModel: T40\nFeature: AUTH_DEVICE#1\n"
INVALID = "^\n% Invalid input detected at '^' marker."
SENT = []
STATE = {"backup": XML, "feature": FEATURE}


class Fake:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, cmd, **kw):
        SENT.append(cmd)
        if cmd == "export config to console":
            return STATE["backup"]
        if cmd == "show sysinfo":
            return SYSINFO
        if cmd == "show feature-key":
            if STATE["feature"] is None:
                raise TimeoutError("sin respuesta")
            return STATE["feature"]
        return INVALID


services.connect = lambda dev: Fake()
init_db()
p = get_profile("watchguard_fireware")
check("perfil: «export config to console» y feature key una vez", p.backup == "export config to console"
      and p.license_cmd == "show feature-key" and p.license_label == "Feature key")
check("un error en la segunda línea se reconoce", services.device_error(INVALID))
check("una configuración XML no se toma por error", not services.device_error(XML))

with SessionLocal() as db:
    c = Credential(name="wg", username="read", password_enc=encrypt("x"))
    db.add(c)
    db.flush()
    dev = Device(name="fw-prueba", host="10.0.0.254", port=4118, device_type="watchguard_fireware", credential_id=c.id)
    db.add(dev)
    db.commit()
    dev_id = dev.id


def versions():
    with SessionLocal() as db:
        return [decrypt(v.content_enc) for v in db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == dev_id))]


def device():
    with SessionLocal() as db:
        return db.get(Device, dev_id)


# 1) un comando que el equipo no entiende: no queda una «versión» con el mensaje de error
STATE["backup"] = INVALID
r = services.backup_device(dev_id, "manual", "admin")
check("salida «% Invalid input…»: respaldo fallido, sin versión", r == "error" and versions() == [], (r, versions()))

# 2) primer respaldo bueno: configuración XML, inventario y feature key
STATE["backup"] = XML
SENT.clear()
r = services.backup_device(dev_id, "manual", "admin")
d = device()
check("primer respaldo: guarda la configuración XML",
      r == "first" and len(versions()) == 1 and versions()[0].startswith("<?xml"), (r, versions()))
check("inventario desde «show sysinfo»: modelo, serie y versión",
      (d.model, d.serial, d.os_version) == ("T40", "D02807134F41D", "12.8.1.B663143"), (d.model, d.serial, d.os_version))
check("pide la feature key en el primer respaldo", "show feature-key" in SENT, SENT)
check("feature key guardada cifrada, con su fecha", d.license_at is not None and decrypt(d.license_enc).startswith("Serial Number")
      and "AUTH_DEVICE" not in (d.license_enc or ""))

# 3) los respaldos siguientes ya no la piden ni la cambian
first_at = d.license_at
STATE["feature"] = "Serial Number: OTRA\nFeature: CAMBIADA#1\n"
SENT.clear()
r = services.backup_device(dev_id, "scheduled", None)
d = device()
check("segundo respaldo: no vuelve a pedir la feature key", "show feature-key" not in SENT and r == "unchanged", (r, SENT))
check("la feature key guardada no cambia", d.license_at == first_at and "CAMBIADA" not in decrypt(d.license_enc))

# 4) si el equipo no la da, el respaldo sigue bien y se intenta de nuevo la próxima vez
with SessionLocal() as db:
    db.get(Device, dev_id).license_enc = None
    db.get(Device, dev_id).license_at = None
    db.commit()
STATE["feature"] = None
r = services.backup_device(dev_id, "scheduled", None)
d = device()
check("sin respuesta a «show feature-key»: el respaldo no falla y no guarda nada", r == "unchanged" and d.license_enc is None
      and d.last_status == "ok", (r, d.last_status))
STATE["feature"] = INVALID
services.backup_device(dev_id, "scheduled", None)
check("un error del equipo no se guarda como feature key", device().license_enc is None)
STATE["feature"] = FEATURE
services.backup_device(dev_id, "scheduled", None)
check("en el respaldo siguiente que la consigue, se guarda", device().license_enc is not None)

# 5) puertos desde el XML: interfaces físicas, su nombre, si están habilitadas, su red y las VLAN que pasan por ellas
import re  # noqa: E402

from app import ports  # noqa: E402
from app.compliance_packs import PACKS  # noqa: E402

PORTS_XML = """<?xml version="1.0"?>
<profile>
  <system-parameters>
    <snmp-conf><snmp-protocol><snmp-version>2</snmp-version><snmp-comm-string>otra</snmp-comm-string></snmp-protocol></snmp-conf>
    <ntp-conf><ntp-enabled>1</ntp-enabled></ntp-conf>
    <log-conf><remote-logging><enabled>0</enabled></remote-logging></log-conf>
    <common-logging><enabled>1</enabled></common-logging>
    <auth-global-setting><mgmt-user-idle-timeout>900</mgmt-user-idle-timeout>
      <mgmt-acct-lockout><enabled>0</enabled><failures>5</failures></mgmt-acct-lockout></auth-global-setting>
    <misc-global-setting><block-spoof-enabled>1</block-spoof-enabled></misc-global-setting>
  </system-parameters>
  <alias-list>
    <alias><name>WatchGuard.1.from</name><alias-member-list><alias-member><alias-name>Any-Trusted</alias-name></alias-member></alias-member-list></alias>
    <alias><name>WatchGuard Web UI.1.from</name><alias-member-list>
      <alias-member><alias-name>Any-Trusted</alias-name></alias-member><alias-member><alias-name>Any-External</alias-name></alias-member>
    </alias-member-list></alias>
  </alias-list>
  <logon-banner><title/><message/><enable>0</enable></logon-banner>
  <interface-list>
    <interface><name>Any</name><property>4</property></interface>
    <interface><name>WAN</name><description>Internet</description><if-item-list><item><physical-if>
      <if-num>1</if-num><if-dev-name>eth1</if-dev-name><enabled>1</enabled><ip>198.51.100.2</ip><netmask>255.255.255.252</netmask>
    </physical-if></item></if-item-list></interface>
    <interface><name>Disabled</name><if-item-list><item><physical-if>
      <if-num>4</if-num><if-dev-name>eth4</if-dev-name><enabled>0</enabled><ip>0.0.0.0</ip><netmask>0.0.0.0</netmask>
    </physical-if></item></if-item-list></interface>
    <interface><name>VLAN</name><if-item-list><item><physical-if>
      <if-num>0</if-num><if-dev-name>eth0</if-dev-name><enabled>1</enabled>
    </physical-if></item></if-item-list></interface>
    <interface><name>CAMARAS</name><if-item-list><item><vlan-if><vlan-id>112</vlan-id>
      <member-list><member><if-num>0</if-num><if-dev-name>eth0</if-dev-name></member></member-list></vlan-if></item></if-item-list></interface>
    <interface><name>ADMIN</name><if-item-list><item><vlan-if><vlan-id>10</vlan-id>
      <member-list><member><if-num>0</if-num><if-dev-name>eth0</if-dev-name></member></member-list></vlan-if></item></if-item-list></interface>
  </interface-list>
</profile>
"""
pl, has = ports.parse(None, "watchguard_fireware", PORTS_XML, "T40")
by = {p["n"]: p for p in pl}
check("puertos del XML: eth0, eth1 y eth4 en orden, sin estado de enlace", [p["n"] for p in pl] == ["eth0", "eth1", "eth4"]
      and has is False, [p["n"] for p in pl])
check("WAN con su IP y su nombre", by["eth1"]["net"] == "198.51.100.2/30" and by["eth1"]["d"] == "WAN · Internet" and
      by["eth1"]["s"] == "on", by["eth1"])
check("puerto deshabilitado: apagado", by["eth4"]["s"] == "off")
check("puerto con VLAN: las VLAN que pasan por él", by["eth0"]["net"] == "VLAN 10, 112", by["eth0"]["net"])
check("Fireware no recibe comandos de puertos que no tiene", ports.commands("watchguard_fireware") == [])
lay = ports.layout("T40", pl)
check("T40: puertos atrás, con sus rótulos y el frente de luces", lay.get("ports_rear") and lay["names"]["eth0"] == "0/WAN"
      and lay["names"]["eth4"] == "4-PoE" and lay["leds"]["order"] == "desc" and "console" in lay["before"])
r = ports.rear_from_model("T40", True)
check("T40: atrás con la entrada de 54 V DC", r and r["psu"][0]["n"] == "54 V DC" and r["psu"][0]["s"] == "ok", r)
check("otro Firebox sigue con el diseño genérico", not ports.layout("T80", pl).get("ports_rear"))

# 6) las 10 reglas de cumplimiento sobre el XML
flt, _desc, rules = PACKS["WatchGuard Fireware · Seguridad básica"]
check("paquete WatchGuard: 10 reglas, solo para Fireware", len(rules) == 10 and re.search(flt, "watchguard_fireware")
      and not re.search(flt, "fortinet"))


def passes(rule):
    found = re.search(rule[2], PORTS_XML, re.MULTILINE) is not None
    return found if rule[1] == "must_contain" else not found


res = {r[0]: passes(r) for r in rules}
expect_fail = {"Web UI no accesible desde Internet", "Bloqueo de cuentas de administración tras intentos fallidos",
               "SNMP v3 (cifrado) en lugar de v1/v2c", "Aviso antes del inicio de sesión"}
check("reglas: fallan justo las 4 que no cumple el ejemplo", {k for k, v in res.items() if not v} == expect_fail, res)
fixed = (PORTS_XML.replace("<alias-name>Any-External</alias-name>", "")
         .replace("<mgmt-acct-lockout><enabled>0</enabled>", "<mgmt-acct-lockout><enabled>1</enabled>")
         .replace("<snmp-version>2</snmp-version>", "<snmp-version>3</snmp-version>")
         .replace("<enable>0</enable></logon-banner>", "<enable>1</enable></logon-banner>"))
PORTS_XML = fixed
check("con todo corregido, cumple las 10", all(passes(r) for r in rules), {r[0]: passes(r) for r in rules})

# 7) configuraciones enormes: la comparación es rápida y la página no dibuja 40.000 filas
import time  # noqa: E402

big = ["<profile>"] + [x for i in range(8000) for x in (" <policy>", f"  <name>p{i}</name>", "  <enabled>1</enabled>", " </policy>")]
big2 = list(big)
big2[401], big2[24001] = "  <name>cambio-a</name>", "  <name>cambio-b</name>"
t0 = time.time()
rows = services.side_by_side("\n".join(big), "\n".join(big2), 3)
check("comparar dos XML de 32.000 líneas tarda menos de 3 s", time.time() - t0 < 3, round(time.time() - t0, 2))
check("y encuentra los dos cambios", [r[4] for r in rows if r[0] == "c"] == ["  <name>cambio-a</name>", "  <name>cambio-b</name>"])
shown, total = services.cap_rows(services.side_by_side(INVALID, "\n".join(big), 3))
check("v1 (error) contra un XML entero: se dibujan 2.000 filas y se avisa del total",
      len(shown) == services.DIFF_ROWS_MAX and total > 32000, (len(shown), total))
check("las configuraciones normales se comparan como siempre",
      services._opcodes(["a", "b"], ["a", "c"]) == [("equal", 0, 1, 0, 1), ("replace", 1, 2, 1, 2)])

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
