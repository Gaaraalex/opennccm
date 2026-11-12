"""Genera docs/desarrollo/02-modulos.md: qué hace cada archivo de app/, agrupado por área, con la primera frase del
docstring de cada módulo y su tamaño. Uso: python docs/src/mapa_codigo.py   (después de agregar o renombrar módulos)"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AREAS = [
    ("Núcleo de la aplicación", ["main", "web", "auth", "config", "db", "crypto", "i18n", "scheduler", "cli", "errors",
                                 "metrics", "security", "systime", "updates", "sysbackup", "__init__"]),
    ("Equipos: conexión, respaldo e inventario", ["services", "drivers", "shell_drivers", "legacy_ssh", "fwtag", "chassis",
                                                 "ports", "porttable", "swos", "fireware_web", "fxi", "licenses",
                                                 "bulkbackup", "retire", "restore"]),
    ("Cambios, reversión y consola", ["rollback", "rollback_vendor", "console", "consolecenter", "consolecore", "cmdref",
                                      "nettools", "favorites", "credview", "impact"]),
    ("Recetas, configlets y cumplimiento", ["recipes", "recipes_more", "recipes_major", "recipes_fw", "recipes_gen",
                                            "recipes_rest", "recipes_ubnt_zte", "configlets_seed", "configlets_ui",
                                            "compliance_packs", "verified", "refconfig"]),
    ("Red: mapa, vecinos, MAC/IP e informes", ["topology", "topolayout", "nodes", "nbwatch", "macfind", "ipam",
                                              "mapreport", "netreports", "netdoc", "reports", "charts", "health"]),
    ("Firmware, CVE y ciclo de vida", ["firmware", "fwupdates", "fwverify", "eol"]),
    ("Integraciones", ["extauth", "zabbix", "gitexport", "vendorlogos"]),
    ("Asistente IA y base de conocimiento", ["assistant", "aichat", "kb"]),
    ("Rutas agrupadas y API", ["routes_ops", "mobile", "mobile_app"]),
    ("Utilidades", ["cronutil"]),
]


def first_sentence(doc: str) -> str:
    d = " ".join((doc or "").split())
    m = re.match(r"(.+?[.:])(\s|$)", d)
    s = m.group(1) if m else d
    return (s[:300] + "…") if len(s) > 300 else s


def public(text: str) -> str:
    """Esta guía es pública: la app móvil se nombra en general."""
    return re.sub(r"(?i)\bapp Android\b", "app móvil", text)


def main():
    mods = {p.stem: p for p in (ROOT / "app").glob("*.py")}
    seen, out = set(), ["# 2. Mapa del código", "",
                        "Qué hace cada archivo de `app/`. La descripción es la primera frase del docstring del módulo: si "
                        "cambia lo que hace un archivo, actualice su docstring y vuelva a generar esta página con "
                        "`python docs/src/mapa_codigo.py`.", "",
                        "Otras carpetas: `app/templates/` (páginas Jinja2), `app/static/` (CSS, JS, iconos, logos de "
                        "marca), `app/locales/` (traducciones), `tests/` (pruebas, capítulo 7), `scripts/` (utilidades "
                        "de desarrollo), `tools/`, `docs/` (manuales y esta guía) e `install.sh` (instalador y "
                        "actualizador, capítulo 8).", ""]
    for area, names in AREAS:
        out += [f"## {area}", "", "| Archivo | Líneas | Qué hace |", "|---|---:|---|"]
        for n in names:
            if n not in mods:
                continue
            seen.add(n)
            src = mods[n].read_text(encoding="utf-8")
            doc = ast.get_docstring(ast.parse(src)) or ""
            out.append(f"| `app/{n}.py` | {len(src.splitlines())} | {public(first_sentence(doc)).replace('|', '/') or '—'} |")
        out.append("")
    rest = sorted(set(mods) - seen)
    if rest:
        out += ["## Otros", "", "| Archivo | Líneas | Qué hace |", "|---|---:|---|"]
        for n in rest:
            src = mods[n].read_text(encoding="utf-8")
            out.append(f"| `app/{n}.py` | {len(src.splitlines())} | {first_sentence(ast.get_docstring(ast.parse(src)) or '') or '—'} |")
        out.append("")
    out += ["## Dónde buscar", "",
            "| Quiero cambiar… | Mire en |", "|---|---|",
            "| cómo se respalda un equipo | `services.backup_device` y el `Profile` de su tipo en `drivers.py` |",
            "| cómo se aplica una solicitud de cambio | `services.execute_change_request` → `apply_config` → `_push_stepwise` |",
            "| qué comando cuenta como consulta | `consolecore.is_change` (y `_QUERY` / `_DANGER` por familia) |",
            "| los comandos de «Volver a esta versión» | `rollback.py` (familias) y `rollback_vendor.py` (RouterOS, EXOS, VOSS) |",
            "| una receta | `recipes*.py` (formato en `recipes.py`) y su sello en `recipes.VERIFIED` |",
            "| una regla de cumplimiento incluida | `compliance_packs.py` |",
            "| el panel principal | `main.dashboard`, `reports.analytics`, `health.summary`, `templates/dashboard.html` |",
            "| la lista de equipos | `main.devices`, `templates/devices.html` |",
            "| la barra lateral y la superior | `templates/base.html`, `web.render` y `web._nav_counts` |",
            "| un texto en inglés | `locales/en.json` |",
            "| lo que ve la app móvil | `mobile_app.py` (pantallas) y `mobile.py` (sesión y acciones) |", ""]
    dst = ROOT / "docs" / "desarrollo" / "02-modulos.md"
    dst.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"{dst}: {len(seen) + len(rest)} módulos")


if __name__ == "__main__":
    main()
