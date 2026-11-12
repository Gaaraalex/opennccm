"""Guía del desarrollador en PDF: junta docs/desarrollo/*.md en un HTML con el estilo de los manuales (doc.css) y lo
imprime con Microsoft Edge sin ventana, como build.py. Uso:
    python docs/src/guia_dev.py                      → docs/OpenNCCM-Guia-del-desarrollador.pdf
    python docs/src/guia_dev.py --extra anexo.md --out otra.pdf   (capítulos que no van en el repositorio)
El convertidor de Markdown es mínimo a propósito (sin dependencias): títulos, párrafos, listas, tablas, bloques de
código, `código`, **negrita** y [enlaces](…). Escriba los capítulos con eso."""
import html
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "docs" / "desarrollo"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


def inline(t: str) -> str:
    parts = re.split(r"(`[^`]+`)", t)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{html.escape(p[1:-1])}</code>")
            continue
        p = html.escape(p, quote=False)
        p = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", p)
        # enlaces entre capítulos («02-modulos.md») van al ancla del capítulo dentro del PDF
        p = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                   lambda m: f'<a href="#{Path(m.group(2)).stem}">{m.group(1)}</a>' if m.group(2).endswith(".md")
                   else f'<a href="{m.group(2)}">{m.group(1)}</a>', p)
        out.append(p)
    return "".join(out)


def md(text: str, anchor: str) -> str:
    lines, out, i = text.splitlines(), [], 0
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(x.strip() for x in para)) + "</p>")
            para.clear()
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre>" + html.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,3}) (.*)", ln)
        if m:
            flush()
            n = len(m.group(1))
            ida = f' id="{anchor}"' if n == 1 else ""
            out.append(f"<h{n}{ida}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            cells = lambda s: [c.strip() for c in s.strip().strip("|").split("|")]  # noqa: E731
            align = ["right" if c.endswith(":") and not c.startswith(":") else "" for c in cells(lines[i + 1])]
            head = cells(ln)
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append(cells(lines[j]))
                j += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            tb = "".join("<tr>" + "".join(f'<td{" style=text-align:right" if k < len(align) and align[k] else ""}>{inline(c)}</td>'
                                          for k, c in enumerate(r)) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            i = j
            continue
        m = re.match(r"(\s*)(\d+\.|-) (.*)", ln)
        if m:
            flush()
            tag = "ol" if m.group(2)[0].isdigit() else "ul"
            items, j = [], i
            while j < len(lines):
                mm = re.match(r"(\s*)(\d+\.|-) (.*)", lines[j])
                if mm and len(mm.group(1)) <= len(m.group(1)):
                    items.append([mm.group(3)])
                elif lines[j].startswith("   ") and lines[j].strip() and items:
                    items[-1].append(lines[j].strip())
                else:
                    break
                j += 1
            out.append(f"<{tag}>" + "".join("<li>" + inline(" ".join(it)) + "</li>" for it in items) + f"</{tag}>")
            i = j
            continue
        if not ln.strip():
            flush()
        else:
            para.append(ln)
        i += 1
    flush()
    return "\n".join(out)


def build(extra: list[Path], out_pdf: Path, version: str):
    files = sorted(SRC.glob("[0-9][0-9]-*.md"))
    readme = (SRC / "README.md").read_text(encoding="utf-8")
    chapters = [("intro", readme)] + [(f.stem, f.read_text(encoding="utf-8")) for f in files] + \
               [(f"anexo-{k}", f.read_text(encoding="utf-8")) for k, f in enumerate(extra)]
    titles = [re.search(r"^# (.*)$", t, re.M).group(1) for _a, t in chapters[1:]]
    toc = [re.sub(r"^[0-9]+[.] ", "", t) for t in titles]  # (sin «\» dentro de un f-string: Python 3.10)
    hoy = date.today()
    body = [f'''<div class="cover"><div class="brand">Open<span>NCCM</span></div><div>
<h1>Guía del desarrollador</h1><p class="sub">Cómo está hecho OpenNCCM, dónde está cada cosa, cómo se prueba y cómo se
publica: lo necesario para continuar el proyecto.</p></div>
<div class="meta">Versión del software: {version}<br>{MONTHS[hoy.month - 1].capitalize()} de {hoy.year}</div></div>''',
            '<section class="chapter"><h1>Contenido</h1><div class="toc"><ol>' +
            "".join(f"<li>{html.escape(t)}</li>" for t in toc) + "</ol></div></section>"]
    for anchor, text in chapters:
        body.append(f'<section class="chapter">{md(text, anchor)}</section>')
    css = (HERE / "doc.css").read_text(encoding="utf-8").replace('"OpenNCCM 2.10.0"', f'"OpenNCCM {version} · Guía del desarrollador"')
    page = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="color-scheme" content="light">'
            f'<title>OpenNCCM — Guía del desarrollador</title><style>{css}\npre{{white-space:pre-wrap}}</style></head>'
            f'<body>{"".join(body)}</body></html>')
    tmp = HERE / "_guia_dev.html"
    tmp.write_text(page, encoding="utf-8")
    try:
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--blink-settings=preferredColorScheme=1", "--virtual-time-budget=8000",
                        f"--print-to-pdf={out_pdf}", tmp.as_uri()], check=True, capture_output=True, timeout=180)
    finally:
        tmp.unlink(missing_ok=True)
    try:
        from pypdf import PdfReader
        print(f"{out_pdf.name}: {len(PdfReader(out_pdf).pages)} páginas, {out_pdf.stat().st_size // 1024} KB")
    except ImportError:
        print(out_pdf)


if __name__ == "__main__":
    args = sys.argv[1:]
    extra = [Path(args[k + 1]) for k, a in enumerate(args) if a == "--extra"]
    out = Path(args[args.index("--out") + 1]) if "--out" in args else ROOT / "docs" / "OpenNCCM-Guia-del-desarrollador.pdf"
    ver = re.search(r'__version__ = "([^"]+)"', (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")).group(1)
    build(extra, out.resolve(), ver)
