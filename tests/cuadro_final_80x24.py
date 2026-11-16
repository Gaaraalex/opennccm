"""El cuadro final del instalador en una terminal de 80x24 (la más chica que se soporta), con el bloque real de install.sh:
título visible, la línea «IMPORTANTE…» entera, Enter cierra los dos cuadros y la salida es 0.
Uso:  python3 tests/cuadro_final_80x24.py RUTA_REPO"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
src = (REPO / "install.sh").read_text(encoding="utf-8")
i = src.index("    # cuadro final sin texto desplazable")
fin = 'Administración › Sistema." 11 74 || true\n'
j = src.index(fin, i) + len(fin)
bloque = src[i:j].replace("local rows h=24;", "h=24;")
k = src.index("ui() {")
ui = src[k:src.index("\n", k)]
b = src.index("\nBACKTITLE=") + 1
backtitle = src[b:src.index("\n", b)]  # el real: el largo es el que tapaba el título del cuadro
script = (backtitle + "\n" + ui + "\n"
          'ver=9.9.9; URL=https://10.10.0.11/; ETC_DIR=/etc/opennccm\n'
          'creds="\\n  Usuario:     admin\\n  Contraseña:  Admin12345\\n\\n(guardados también en /root/opennccm-credenciales.txt)\\n"\n'
          + bloque)
f = Path(tempfile.mkdtemp()) / "cuadro.sh"
f.write_text(script, encoding="utf-8")
S = "cuadro"


def tmux(*a):
    return subprocess.run(["tmux", "-u", *a], capture_output=True, text=True)


def pantalla():
    return tmux("capture-pane", "-p", "-t", S).stdout


def esperar(cond, seg=10):
    fin = time.time() + seg
    while time.time() < fin:
        if cond(pantalla()):
            return True
        time.sleep(0.3)
    return False


fallas = []


def check(nombre, ok):
    print(("ok: " if ok else "FALLA: ") + nombre)
    if not ok:
        fallas.append(nombre)
        print(pantalla())


tmux("kill-session", "-t", S)
tmux("new-session", "-d", "-s", S, "-x", "80", "-y", "24", "-e", "LANG=C.UTF-8", "-e", "TERM=xterm-256color",
     f"bash {f}; echo FIN-CUADRO $?; sleep 60")
check("título «Instalación completada» visible en 80x24", esperar(lambda p: "┤ Instalación completada ├" in p))
check("línea «IMPORTANTE…» entera", "IMPORTANTE: respalde /etc/opennccm/secret.key en un lugar seguro." in pantalla())
tmux("send-keys", "-t", S, "Enter")
check("Enter cierra el primero y aparece «Estadística de uso»", esperar(lambda p: "┤ Estadística de uso ├" in p and "Una vez al día" in p))
tmux("send-keys", "-t", S, "Enter")
check("Enter cierra el segundo y la salida es 0", esperar(lambda p: "FIN-CUADRO 0" in p))
tmux("kill-session", "-t", S)
sys.exit(1 if fallas else 0)
