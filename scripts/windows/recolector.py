"""OpenNCCM Recolector para Windows: el modo recolector de app/recolector.py empaquetado en un .exe (PyInstaller).

Lo arma la CI pública (scripts/windows/armar.py) y se adjunta al release como opennccm-recolector-<versión>.exe con su
SHA-256. No trae código Pro: en el alta baja del servidor central el módulo firmado, como en Linux. Sin firma
Authenticode (Windows muestra el aviso de editor desconocido; la ventana de alta explica cómo comprobar el SHA-256).

    opennccm-recolector.exe /central=URL /clave=ra_…        instala (como administrador): copia, alta y tarea programada
                       [/carpeta=C:\\otra]                  instala en otra carpeta (los datos van en su subcarpeta «datos»)
                       [/sin-tarea]                        sin tarea programada (prueba: no toca nada del sistema)
    opennccm-recolector.exe estado                          sede, servidor central y cola
    opennccm-recolector.exe correr                          el servicio (lo arranca la tarea programada; revocado: la deshabilita)
    opennccm-recolector.exe desinstalar [/todo]             quita la tarea y el programa (/todo: también los datos)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TAREA = "OpenNCCM Recolector"
EXE = "opennccm-recolector.exe"
PROGRAMA = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "OpenNCCM Recolector"
DATOS = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "OpenNCCM Recolector"
CONF = "recolector.json"  # junto al .exe instalado: dónde están los datos y si hay tarea


def opciones(args: list[str]) -> tuple[dict, list[str]]:
    opts, resto = {}, []
    for a in args:
        if a.startswith("/") and len(a) > 1:
            k, _, v = a[1:].partition("=")
            opts[k.lower()] = v if v else True
        else:
            resto.append(a)
    return opts, resto


def preparar_entorno(datos: Path) -> None:
    """Antes de importar app: la configuración de esta instalación (como /etc/opennccm/opennccm.env en Linux)."""
    datos.mkdir(parents=True, exist_ok=True)
    os.environ.update({"NCM_DATA_DIR": str(datos), "NCM_KEY_FILE": str(datos / "secret.key"), "NCM_MODO": "recolector",
                       "NCM_SYSLOG_ENABLED": "0"})
    extra = datos / "opennccm.env"  # para pruebas o soporte: NOMBRE=valor por línea
    if extra.is_file():
        for line in extra.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                os.environ[k] = v


def recolector(*args: str) -> int:
    from app.db import init_db
    init_db()
    from app import recolector as rc
    return rc.main(list(args))


def instalado() -> tuple[Path, dict]:
    """La carpeta del .exe que corre y su configuración (si es una instalación)."""
    aqui = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    try:
        return aqui, json.loads((aqui / CONF).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return aqui, {}


def proteger(carpeta: Path) -> None:
    """Solo SYSTEM y Administradores (las credenciales de los equipos se guardan cifradas con secret.key de aquí)."""
    subprocess.run(["icacls", str(carpeta), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                   capture_output=True, check=False)


def tarea_xml(exe: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Respalda los equipos de esta sede para el servidor central de OpenNCCM.</Description></RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT30S</Delay></BootTrigger>
    <!-- cada minuto: si el proceso murió (o lo mataron), vuelve a arrancar; si ya corre, no se abre otro -->
    <TimeTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>"{exe}"</Command><Arguments>correr</Arguments><WorkingDirectory>{exe.parent}</WorkingDirectory></Exec></Actions>
</Task>
"""


def crear_tarea(exe: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(tarea_xml(exe))
    try:
        subprocess.run(["schtasks", "/Create", "/TN", TAREA, "/XML", f.name, "/F"], check=True, capture_output=True)
    finally:
        os.unlink(f.name)
    subprocess.run(["schtasks", "/Run", "/TN", TAREA], check=True, capture_output=True)


def instalar(opts: dict) -> int:
    central, clave = str(opts.get("central") or ""), str(opts.get("clave") or "")
    if not central.startswith(("http://", "https://")) or not clave.startswith("ra_"):
        print("Uso: opennccm-recolector.exe /central=https://servidor-central /clave=ra_…  (el comando lo da la ventana de alta)")
        return 2
    sin_tarea = bool(opts.get("sin-tarea"))
    if opts.get("carpeta"):
        programa = Path(str(opts["carpeta"])).resolve()
        datos = programa / "datos"
    else:
        programa, datos = PROGRAMA, DATOS
    programa.mkdir(parents=True, exist_ok=True)
    datos.mkdir(parents=True, exist_ok=True)
    if not sin_tarea:  # la prueba (/sin-tarea) no cambia permisos: quien la corre sin ser administrador no perdería su carpeta
        proteger(programa)
        proteger(datos)
    exe = programa / EXE
    if getattr(sys, "frozen", False) and Path(sys.executable).resolve() != exe:
        if not sin_tarea:
            subprocess.run(["schtasks", "/End", "/TN", TAREA], capture_output=True, check=False)  # una instalación anterior
        shutil.copy2(sys.executable, exe)
    (programa / CONF).write_text(json.dumps({"datos": str(datos), "tarea": not sin_tarea}, indent=1), encoding="utf-8")
    preparar_entorno(datos)
    print(f"Instalando el recolector en {programa} (datos en {datos})…")
    rc = recolector("alta", central, clave)
    if rc != 0:
        return rc
    if sin_tarea:
        print(f"Listo, sin tarea programada (prueba). Para correrlo a mano: \"{exe}\" correr")
    else:
        crear_tarea(exe)
        print(f"Listo: la tarea programada «{TAREA}» corre el recolector al arrancar Windows, y ya está corriendo.")
    print(f"Estado: \"{exe}\" estado")
    return 0


def desinstalar(opts: dict) -> int:
    aqui, conf = instalado()
    if not conf:
        print("Este .exe no es una instalación del recolector.")
        return 1
    if conf.get("tarea"):
        subprocess.run(["schtasks", "/End", "/TN", TAREA], capture_output=True, check=False)
        subprocess.run(["schtasks", "/Delete", "/TN", TAREA, "/F"], capture_output=True, check=False)
    datos = Path(conf["datos"])
    if opts.get("todo"):
        shutil.rmtree(datos, ignore_errors=True)
    # el .exe que corre no puede borrarse a sí mismo: lo borra cmd cuando este proceso termina
    subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{aqui}"' if aqui != datos.parent or opts.get("todo")
                     else f'cmd /c ping -n 3 127.0.0.1 >nul & del /q "{aqui / EXE}" "{aqui / CONF}"', shell=False,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("Recolector desinstalado." + ("" if opts.get("todo") else f" Los datos quedan en {datos}."))
    return 0


def main(argv: list[str]) -> int:
    opts, resto = opciones(argv)
    accion = resto[0] if resto else ""
    if opts.get("central") or opts.get("clave"):
        return instalar(opts)
    if accion == "desinstalar":
        return desinstalar(opts)
    if accion in ("estado", "correr"):
        _aqui, conf = instalado()
        if not conf:
            print("El recolector no está instalado con este .exe.")
            return 1
        preparar_entorno(Path(conf["datos"]))
        rc = recolector(accion)
        if accion == "correr" and rc == 0 and conf.get("tarea"):
            # sale con 0 solo si el central lo revocó: la tarea se deshabilita para que no vuelva a arrancar
            subprocess.run(["schtasks", "/Change", "/TN", TAREA, "/DISABLE"], capture_output=True, check=False)
        return rc
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
