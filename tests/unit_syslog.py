"""Avisos syslog seguidos: un segundo cambio poco después del primero no debe perderse (antes se descartaba
durante 60 s), y varios avisos seguidos se agrupan en un solo respaldo extra."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import scheduler, services  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


clock = [1000.0]
timers, backups = [], []


class FakeTimer:
    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon = delay, fn, True
        timers.append(self)

    def start(self):
        pass


scheduler.time.time = lambda: clock[0]
scheduler.threading.Timer = FakeTimer
scheduler._device_for_ip = lambda ip: 1
services.add_event = lambda *a, **k: None
services.pool.submit = lambda fn, dev: backups.append((clock[0], dev))
MSG = b"<189>45: R1: %SYS-5-CONFIG_I: Configured from console by admin on vty0"


def syslog():
    h = scheduler.SyslogHandler.__new__(scheduler.SyslogHandler)
    h.request, h.client_address = (MSG, None), ("10.0.0.1", 514)
    h.handle()


def run_due():
    """Ejecuta los temporizadores cuyo plazo ya venció."""
    for t in list(timers):
        if clock[0] >= t.at:
            timers.remove(t)
            t.fn()


_orig_init = FakeTimer.__init__


def _init(self, delay, fn):
    _orig_init(self, delay, fn)
    self.at = clock[0] + delay


FakeTimer.__init__ = _init

syslog()                              # cambio 1 (t=0)
check("primer aviso: respaldo a los 20 s", len(timers) == 1 and timers[0].delay == 20)
clock[0] += 20
run_due()
check("primer respaldo ejecutado", len(backups) == 1)
clock[0] += 10
syslog()                              # cambio 2, 30 s después del primero (antes se descartaba)
check("segundo aviso a los 30 s NO se descarta", len(timers) == 1, len(timers))
clock[0] += 5
syslog()                              # cambio 3, enseguida: se agrupa con el anterior
check("avisos seguidos se agrupan (un solo respaldo pendiente)", len(timers) == 1, len(timers))
clock[0] += 120
run_due()
check("se hizo un respaldo más que recoge los cambios 2 y 3", len(backups) == 2, backups)
clock[0] += 200
syslog()                              # cambio mucho después: comportamiento normal
check("aviso aislado posterior: respaldo normal a los 20 s", len(timers) == 1 and timers[0].delay == 20)

# Extreme VOSS / Fabric Engine: no avisa «configuración cambiada»; cada comando de la CLI llega como CLILOG (salida real
# de un 5520 con VOSS 9.3 en GNS3). Cuentan los de configuración y el guardado; los de consulta no.
from app.scheduler import CHANGE_PATTERNS  # noqa: E402
H = "<190>1 2026-11-06T10:32:35.733Z 5520-24T-FabricEngine CP1 - 0x002c0600 - 00000000 GlobalRouter CLILOG INFO   "
check("VOSS: un comando de configuración (CLILOG) es un cambio", bool(CHANGE_PATTERNS.search(H + "137 SSH:10.0.0.5 admin vlan create 998 name X type port-mstprstp 0")))
check("VOSS: «Save config … successful» es un cambio", bool(CHANGE_PATTERNS.search("<190>1 x GlobalRouter SW INFO Save config to file /intflash/config.cfg successful.")))
check("VOSS: show, terminal, enable, configure y end no son cambios", not any(CHANGE_PATTERNS.search(H + "1 SSH:10.0.0.5 admin " + c) for c in (
    "show running-config", "terminal more disable", "enable", "configure terminal", "end", "exit")))
# Extreme EXOS 33.6 (salida real con «enable cli-config-logging»)
X = "<142>Nov  7 18:52:29 X-1 cli: 10.0.0.5 (ssh) admin: "
check("EXOS: un comando de configuración es un cambio", all(CHANGE_PATTERNS.search(X + c) for c in (
    "create vlan prueba", "configure vlan DATOS add ports 5 untagged", "delete vlan prueba", "disable ports 2",
    "save configuration")))
check("EXOS: consultas y la preparación de la sesión no son cambios", not any(CHANGE_PATTERNS.search(X + c) for c in (
    "show configuration", "disable clipaging", "show switch", "exit")))
check("EXOS: el inicio de sesión no es un cambio", not CHANGE_PATTERNS.search(
    "<142>Nov  7 18:56:29 X-1 AAA: Login passed for user admin through ssh (10.0.0.5)"))

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
