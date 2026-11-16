"""Arma opennccm-recolector-<versión>.exe (un solo archivo, con Python adentro) y su .sha256.

Desde la carpeta del repositorio, en Windows y con PyInstaller y requirements.txt instalados:
    python scripts/windows/armar.py [--salida dist]
La CI pública lo corre al publicar una versión y adjunta los dos archivos al release de GitHub."""
import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (RAIZ / "app" / "__init__.py").read_text(encoding="utf-8")).group(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(RAIZ / "dist"))
    a = ap.parse_args()
    import PyInstaller.__main__
    ver = version()
    nombre = f"opennccm-recolector-{ver}"
    trabajo = Path(tempfile.mkdtemp(prefix="armar-recolector-"))
    sep = os.pathsep
    datos = [f"{RAIZ / 'app'}{sep}app"]  # todo app/ (textos, plantillas, json): lo que el código lea al importarse
    args = [str(RAIZ / "scripts" / "windows" / "recolector.py"), "--onefile", "--console", "--noconfirm", "--clean",
            "--name", nombre, "--distpath", a.salida, "--workpath", str(trabajo / "build"), "--specpath", str(trabajo),
            "--paths", str(RAIZ), "--collect-submodules", "app",
            # lo que el módulo bajado del central importa en tiempo de ejecución (no se ve al analizar el código)
            "--collect-all", "netmiko", "--collect-all", "ntc_templates", "--collect-all", "textfsm",
            "--collect-submodules", "paramiko", "--collect-submodules", "cryptography", "--collect-submodules", "requests",
            "--collect-submodules", "sqlalchemy", "--collect-submodules", "urllib3"]
    for d in datos:
        args += ["--add-data", d]
    PyInstaller.__main__.run(args)
    shutil.rmtree(trabajo, ignore_errors=True)
    exe = Path(a.salida) / f"{nombre}.exe"
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (Path(a.salida) / f"{nombre}.exe.sha256").write_text(f"{sha}  {exe.name}\n", encoding="utf-8")
    print(exe, sha, f"{exe.stat().st_size // 2**20} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
