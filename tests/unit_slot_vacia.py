"""Ranura vacía del chasis (OLT ZTE y otros): el «estado vacío» general (.empty de style.css y senal.css, columna centrada
con mucho relleno) no debe aplicarse a la fila de la ranura, o el «vacía» se sale y se monta sobre la ranura siguiente."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


src = open(os.path.join(ROOT, "app/templates/_ports.html"), encoding="utf-8").read()
senal = open(os.path.join(ROOT, "app/static/senal.css"), encoding="utf-8").read()
check("senal.css sigue con .empty en columna (si cambia, revisar esta prueba)", "flex-direction:column" in
      re.search(r"(?<![\w-])\.empty\{[^}]*\}", senal).group(0))
h = re.search(r"\.zch \.zsh\.empty\{([^}]*)\}", src)
v = re.search(r"\.zch \.zsl\.empty\{([^}]*)\}", src)
check("ranura vacía horizontal: en fila, sin el relleno del estado vacío",
      bool(h) and all(x in h.group(1) for x in ("flex-direction:row", "padding:0 10px", "text-align:left")))
check("ranura vacía vertical: en columna con su relleno propio",
      bool(v) and all(x in v.group(1) for x in ("flex-direction:column", "padding:6px 0")))
print("RESULTADO:", "TODO OK" if not FAILS else f"{len(FAILS)} FALLOS: {FAILS}")
raise SystemExit(1 if FAILS else 0)
