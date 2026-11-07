"""Entrar por SSH: un fallo pasajero («No existing session», visto en un CHR ocupado con varias sesiones) se reintenta;
una contraseña mala o un equipo que no responde no."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import services  # noqa: E402

services.time.sleep = lambda s: None
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  [{extra}]"))
    if not cond:
        fails.append(name)


def handler(errors):
    calls = []

    def h(**_k):
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "conexión"
    return h, calls


h, calls = handler([ValueError("Paramiko: 'No existing session' error: try increasing 'conn_timeout'")])
check("«No existing session» se reintenta y entra", services._connect_retry(h, {"host": "x"}) == "conexión" and len(calls) == 2, calls)
h, calls = handler([RuntimeError("Authentication to device failed.")])
try:
    services._connect_retry(h, {"host": "x"})
    check("contraseña mala: no se reintenta", False)
except RuntimeError:
    check("contraseña mala: no se reintenta", len(calls) == 1, calls)
h, calls = handler([EOFError()] * 5)
try:
    services._connect_retry(h, {"host": "x"})
    check("como mucho 3 intentos", False)
except EOFError:
    check("como mucho 3 intentos", len(calls) == 3, calls)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
