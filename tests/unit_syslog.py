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

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
