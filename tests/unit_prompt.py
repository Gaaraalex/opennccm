"""Un cambio que renombra el equipo cambia su prompt: OpenNCCM lo vuelve a leer antes de salir del modo configuración y
guardar (si no, la solicitud fallaba por tiempo y la configuración quedaba sin guardar; visto en un VOSS 5520)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.services import _refresh_prompt  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Conn:
    def __init__(self, base, prompt):
        self.base_prompt, self._p = base, prompt

    def find_prompt(self, pattern=None):
        return self._p


c = Conn("5520-24T-FabricEngine:1", "VSP-LAB-1:1(config)#")
_refresh_prompt(c)
check("VOSS: «sys name» nuevo, en modo configuración", c.base_prompt == "VSP-LAB-1:1", c.base_prompt)
c = Conn("R1", "SW-CORE(config)#")
_refresh_prompt(c)
check("Cisco: «hostname» nuevo", c.base_prompt == "SW-CORE", c.base_prompt)
c = Conn("R1", "R1(config-if)#")
_refresh_prompt(c)
check("sin cambio de nombre no se toca", c.base_prompt == "R1", c.base_prompt)
c = Conn("[admin@MikroTik]", "[admin@Nuevo] >")
_refresh_prompt(c)
check("RouterOS no se toca", c.base_prompt == "[admin@MikroTik]", c.base_prompt)

print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} fallos: {fails}")
sys.exit(1 if fails else 0)
