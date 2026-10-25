"""Enlaces de «Apoya OpenNCCM» que trae la aplicación sin configurar nada (sin variables NCM_DONATE_*): GitHub Sponsors
primero y Ko-fi después; Open Collective no aparece porque no hay cuenta. Y el botón «Sponsor» del repositorio en GitHub
(.github/FUNDING.yml) con las mismas dos plataformas."""
import os
import sys
import tempfile

for k in list(os.environ):
    if k.startswith("NCM_DONATE_"):
        del os.environ[k]  # run-tests.sh exporta enlaces de prueba: aquí se prueban los de verdad
tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app.web import donate_links  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


links = donate_links()
check("por omisión hay dos formas de donar: GitHub Sponsors y Ko-fi, en ese orden",
      [(d["key"], d["url"]) for d in links] == [("github", "https://github.com/sponsors/Gaaraalex"),
                                                ("kofi", "https://ko-fi.com/opennccm")], links)
check("Open Collective no aparece (no hay cuenta)", all(d["key"] != "opencollective" for d in links))
check("cada enlace con su nombre e icono", [d["label"] for d in links] == ["GitHub Sponsors", "Invitar un café (Ko-fi)"]
      and [d["icon"] for d in links] == ["github", "coffee"], links)
funding = open(os.path.join(ROOT, ".github", "FUNDING.yml"), encoding="utf-8").read()
check("GitHub muestra el botón «Sponsor» con GitHub Sponsors y Ko-fi (.github/FUNDING.yml)",
      "github: Gaaraalex" in funding and "ko_fi: opennccm" in funding and "open_collective" not in funding, funding)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
