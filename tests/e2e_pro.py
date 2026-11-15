"""Puertas de los módulos Pro sin licencia (app/pro.py): Informes › Profesionales con su ejemplo, Administración ›
Recolectores y › Alta disponibilidad con la etiqueta PRO, Licencia y módulos (activar con una clave mal escrita, archivo
que no es una licencia, preferencias que ocultan lo Pro) y permisos. Lo gratis sigue igual. Sin internet: la clave
mal escrita se rechaza antes de llamar a la web. Requiere e2e.py (admin)."""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.crypto import hash_password  # noqa: E402
from app.db import SessionLocal, User, select, set_setting  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


adm = requests.Session()
adm.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
try:
    page = adm.get(B + "/reports").text
    check("Informes: la pestaña de siempre y «Profesionales PRO»", 'href="/reports/pro"' in page and "protag" in page
          and "/reports/view/inventory" in page)
    page = adm.get(B + "/reports/pro").text
    check("Profesionales sin licencia: qué incluye, precio, ejemplo y botones", "Informes profesionales" in page
          and "EJEMPLO" in page and 'href="/license#clave"' in page and "comunidad.opennccm.com/pro" in page
          and "Todo lo demás de OpenNCCM sigue gratis" in page and page.index("Documento de la red") < page.index("Informe ejecutivo mensual"))
    for url, title in (("/recolectores", "Recolectores por sede"), ("/alta-disponibilidad", "Alta disponibilidad")):
        page = adm.get(B + url).text
        check(f"{title} sin licencia: oferta y pestaña PRO en Administración", title in page and "Tengo una clave" in page
              and 'href="/recolectores"' in page and 'href="/license"' in page)
    page = adm.get(B + "/license").text
    check("Licencia y módulos: pegar la clave, archivo sin internet y los 3 módulos «No incluido»",
          'name="key"' in page and 'action="/license/upload"' in page and page.count("No incluido") >= 3
          and "inst_" in page and "DISA" not in page)
    r = adm.post(B + "/license/activate", data={"key": "hola"})
    check("clave mal escrita: se explica la forma, sin llamar a la web", "OPNC-XXXX-XXXX-XXXX-XXXX" in r.text)
    r = adm.post(B + "/license/upload", files={"file": ("lic.txt", b"esto no es una licencia")})
    check("archivo que no es una licencia: se rechaza", "No es un archivo de licencia" in r.text)
    adm.post(B + "/license/prefs", data={"notify30": "on"})
    page = adm.get(B + "/reports").text
    check("ocultar lo Pro que no se tiene: sin pestaña Profesionales", 'href="/reports/pro"' not in page)
    page = adm.get(B + "/license").text
    check("…ni Recolectores ni Alta disponibilidad en Administración", 'href="/recolectores"' not in page
          and 'href="/alta-disponibilidad"' not in page and 'href="/license"' in page)
    adm.post(B + "/license/prefs", data={"show_pro": "on", "notify30": "on"})
    check("volver a mostrarlo", 'href="/reports/pro"' in adm.get(B + "/reports").text)

    # documentos de la red hechos cuando era gratis: sin licencia se ven y se descargan; no se generan nuevos
    from app.config import DATA_DIR
    nd = DATA_DIR / "netdocs"
    nd.mkdir(parents=True, exist_ok=True)
    (nd / "20261101120000-abcdef.json").write_text(json.dumps({"id": "20261101120000-abcdef", "file": "Documento-red_Viejo.pdf",
                                                               "fmt": "pdf", "label": "Toda la red", "user": "admin",
                                                               "created": "2026-11-01T12:00:00"}), encoding="utf-8")
    (nd / "20261101120000-abcdef.pdf").write_bytes(b"%PDF-1.4 viejo")
    page = adm.get(B + "/reports").text
    check("tarjeta del Documento de la red: es Pro y avisa que los anteriores siguen", "es parte de Informes profesionales" in page
          and "Tus documentos anteriores siguen disponibles" in page and 'href="/reports/netdoc-anteriores"' in page)
    page = adm.get(B + "/reports/netdoc-anteriores").text
    check("lista de documentos anteriores sin licencia", "Documento-red_Viejo.pdf" in page)
    r = adm.get(B + "/reports/netdoc-anteriores/20261101120000-abcdef/file")
    check("…y se descargan", r.status_code == 200 and r.content == b"%PDF-1.4 viejo")
    check("un id inventado no descarga nada", adm.get(B + "/reports/netdoc-anteriores/..%2F..%2Fsecret/file").status_code in (403, 404))
    check("sin el módulo no se generan nuevos", adm.post(B + "/reports/netdoc", data={"fmt": "pdf"}).status_code in (404, 405))

    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == "pro-lector")):
            db.add(User(username="pro-lector", password_hash=hash_password("Lector-Pro-2026"), role="viewer"))
            db.commit()
    vw = requests.Session()
    vw.post(B + "/login", data={"username": "pro-lector", "password": "Lector-Pro-2026"})
    check("un lector ve la pestaña Profesionales", vw.get(B + "/reports/pro").status_code == 200)
    check("un lector no ve los documentos anteriores de otro", "Documento-red_Viejo.pdf" not in vw.get(B + "/reports/netdoc-anteriores").text
          and vw.get(B + "/reports/netdoc-anteriores/20261101120000-abcdef/file").status_code == 403)
    for url in ("/license", "/recolectores", "/alta-disponibilidad"):
        r = vw.get(B + url, allow_redirects=False)
        check(f"un lector no entra a {url}", r.status_code in (302, 303, 403))
    r = vw.post(B + "/license/activate", data={"key": "OPNC-AAAA-AAAA-AAAA-AAAA"}, allow_redirects=False)
    check("un lector no puede activar una clave", r.status_code in (302, 303, 403))
finally:
    set_setting("licencia_mostrar_pro", "1")
    with SessionLocal() as db:
        for u in db.scalars(select(User).where(User.username == "pro-lector")):
            db.delete(u)
        db.commit()

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
