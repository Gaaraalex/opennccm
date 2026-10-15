"""Convierte los documentos HTML a PDF con Microsoft Edge (headless). Uso: python build.py"""
import subprocess
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
OUT.mkdir(exist_ok=True)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = {"guia-instalacion.html": "OpenNCCM-Guia-de-instalacion.pdf",
        "manual-funciones.html": "OpenNCCM-Manual-de-funciones.pdf"}

for src, dst in DOCS.items():
    if not (HERE / src).exists():
        continue
    pdf = OUT / dst
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--blink-settings=preferredColorScheme=1", "--virtual-time-budget=8000",
                    f"--print-to-pdf={pdf}", (HERE / src).as_uri()], check=True, capture_output=True, timeout=180)
    print(f"{dst}: {len(PdfReader(pdf).pages)} páginas, {pdf.stat().st_size // 1024} KB")
