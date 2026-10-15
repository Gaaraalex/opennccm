"""Prueba el asistente de instalación (menús whiptail) como lo haría una persona.

Ejecuta install.sh dentro de tmux, espera cada pantalla, guarda una captura (texto con colores ANSI)
y pulsa las teclas. Uso (como root):  python3 tests/tui_drive.py RUTA_REPO CARPETA_CAPTURAS
"""
import subprocess
import sys
import time
from pathlib import Path

REPO, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
S = "inst"
PASSWORD = "Admin12345"


def tmux(*args, check=True):
    return subprocess.run(["tmux", "-u", *args], capture_output=True, text=True, check=check)


def screen(ansi=False):
    args = ["capture-pane", "-p", "-t", S] + (["-e"] if ansi else [])
    return tmux(*args, check=False).stdout


def wait_for(text, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if text in screen():
            time.sleep(0.6)  # dejar que termine de dibujar
            return
        if tmux("has-session", "-t", S, check=False).returncode != 0:
            raise SystemExit(f"El instalador terminó antes de mostrar: {text!r}")
        time.sleep(0.5)
    print(screen())
    raise SystemExit(f"No apareció la pantalla con: {text!r}")


def snap(name):
    (OUT / f"{name}.ans").write_text(screen(ansi=True), encoding="utf-8")
    (OUT / f"{name}.txt").write_text(screen(), encoding="utf-8")


def keys(*k):
    for key in k:
        if key.startswith("text:"):
            tmux("send-keys", "-t", S, "-l", key[5:])
        else:
            tmux("send-keys", "-t", S, key)
        time.sleep(0.25)


cmd = (f"cat {REPO}/install.sh | bash -s -- --source {REPO}; "
       "echo; echo FIN-INSTALADOR $?; sleep 600")
tmux("new-session", "-d", "-s", S, "-x", "100", "-y", "32", "-e", "LANG=C.UTF-8", "-e", "TERM=xterm-256color", cmd)

PASOS = [
    ("Bienvenido al instalador", "01-bienvenida", ["Enter"]),
    ("Comprobación del servidor: correcta", "02-comprobacion", ["Enter"]),
    ("Actualizar Ubuntu ahora", "03-actualizar-ubuntu", ["Tab", "Enter"]),          # No (más rápido en CI)
    ("accederán los usuarios", "04-direccion", ["Enter"]),
    ("Zona horaria actual", "05-zona-horaria", ["Enter"]),
    ("protegerá el acceso web", "06-https", ["Enter"]),
    ("base de datos usará", "07-base-de-datos", ["Enter"]),
    ("Configurar el cortafuegos", "08-cortafuegos", ["Tab", "Enter"]),             # No
    ("usuario administrador", "09-administrador", ["Enter"]),
    ("define la contraseña", "10-contrasena-modo", ["Down", "Space", "Enter"]),    # escribirla
    ("Escriba la contraseña", "11-contrasena", [f"text:{PASSWORD}", "Enter"]),
    ("Repita la contraseña", None, [f"text:{PASSWORD}", "Enter"]),
    ("alertas e informes", "12-correo", ["Enter"]),                                  # No (por defecto)
    ("Comenzar la instalación", "13-resumen", ["Enter"]),
]
for text, name, k in PASOS:
    wait_for(text)
    if name:
        snap(name)
    print("ok:", text)
    keys(*k)

# barra de progreso
end = time.time() + 120
while time.time() < end and "%" not in screen():
    time.sleep(0.5)
time.sleep(3)
snap("14-progreso")
wait_for("Instalación completada", timeout=1200)
snap("15-completada")
print("ok: Instalación completada")
keys("Enter")
wait_for("FIN-INSTALADOR", timeout=60)
final = screen()
(OUT / "salida-final.txt").write_text(final, encoding="utf-8")
code = final.split("FIN-INSTALADOR")[-1].split()[0]
print("Código de salida del instalador:", code)
sys.exit(0 if code == "0" else 1)
