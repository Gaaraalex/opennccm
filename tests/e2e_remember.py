"""«Recordarme» en la aplicación real: casilla en el inicio de sesión, el formulario permite al navegador guardar la
contraseña, la opción se ajusta en Ajustes y con 0 días desaparece. La duración y la inactividad se prueban en
unit_remember.py con un reloj simulado. Requiere haber ejecutado antes e2e.py."""
import re
import sys

import requests

B = "http://127.0.0.1:8000"
fails = []


def settings_form(sess):
    """Todos los valores actuales del formulario de Ajustes (para cambiar uno sin borrar los demás)."""
    html = sess.get(B + "/settings").text
    form = html[html.index('action="/settings"'):]
    form = form[:form.index("</form>")]
    data = {}
    for tag in re.findall(r"<input[^>]*>", form):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        kind = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1]
        if kind in ("checkbox", "radio"):
            if " checked" in tag:
                data[name.group(1)] = (re.search(r'value="([^"]*)"', tag) or [None, "on"])[1]
        elif kind not in ("submit", "button", "file", "password"):
            data[name.group(1)] = (re.search(r'value="([^"]*)"', tag) or [None, ""])[1]
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form, re.S):
        sel = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', body) or re.search(r'<option[^>]*value="([^"]*)"', body)
        if sel:
            data[name] = sel.group(1)
    for name, body in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S):
        data[name] = body
    return data


def set_remember(sess, days):
    data = settings_form(sess)
    data["remember_days"] = str(days)
    sess.post(B + "/settings", data=data)


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def main():
    page = requests.get(B + "/login").text
    check("inicio de sesión: casilla «Recordarme» con los días", 'name="remember"' in page and "Recordarme en este equipo (14 días)" in page)
    check("el navegador puede guardar la contraseña (autocompletado de usuario y contraseña actual)",
          'autocomplete="username"' in page and 'autocomplete="current-password"' in page)
    check("la página avisa de no usarlo en equipos compartidos", "No lo uses en equipos compartidos" in page)
    check("con «Recordarme» se pide al navegador que guarde la contraseña (gestor del navegador, no OpenNCCM)",
          "PasswordCredential" in page and "navigator.credentials.store" in page)

    s = requests.Session()
    r = s.post(B + "/login", data={"username": "admin", "password": "Admin12345", "remember": "on"}, allow_redirects=False)
    cookie = r.headers.get("set-cookie", "")
    m = re.search(r"Max-Age=(\d+)", cookie, re.I)
    check("con «Recordarme» se entra y la cookie puede durar hasta 30 días", r.status_code == 303 and m and int(m.group(1)) == 30 * 86400,
          cookie[:120])
    check("y la sesión funciona", s.get(B + "/account").status_code == 200 and "Mi cuenta" in s.get(B + "/account").text)

    st = s.get(B + "/settings").text
    check("Ajustes: campo de días de «Recordarme»", 'name="remember_days"' in st)
    before = settings_form(s)
    set_remember(s, 0)
    check("con 0 días la casilla desaparece del inicio de sesión", 'name="remember"' not in requests.get(B + "/login").text)
    set_remember(s, 45)
    check("más de 30 días se limita a 30", "Recordarme en este equipo (30 días)" in requests.get(B + "/login").text)
    set_remember(s, 14)
    after = settings_form(s)
    check("cambiar los días no altera ningún otro ajuste", {k: v for k, v in after.items() if k != "remember_days"} ==
          {k: v for k, v in before.items() if k != "remember_days"},
          [k for k in set(after) | set(before) if k != "remember_days" and after.get(k) != before.get(k)])

    print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
