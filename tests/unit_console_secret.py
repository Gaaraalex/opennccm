"""Consola: lo que se escribe cuando el equipo pide una contraseña («Password:» de enable, «Contraseña:»…) no queda en la
grabación ni en su exportación ni se encuentra al buscar. Las grabaciones de antes (que la guardaban tal cual) se
muestran con «********». Sin red."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import consolecore as cc  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


for prompt in ("(SW-CORE-01) >en\r\nPassword:", "Password: ", "Enter password:", "Contraseña:", "Shared secret: ",
               "\x1b[0mPassword:\x1b[0m "):
    check(f"pide contraseña: {prompt!r}", cc.asks_secret(prompt))
for prompt in ("Router#", "(SW-CORE-01) #", "login: ", "Username:", "R1(config)#", "[admin@MikroTik] > "):
    check(f"no pide contraseña: {prompt!r}", not cc.asks_secret(prompt))

ev = [[0.0, "o", "(SW-CORE-01) >"], [3.7, "i", "en"], [3.8, "o", "en\r\nPassword:"], [11.4, "i", "MiClave2323"],
      [11.5, "o", "*********\r\n(SW-CORE-01) #"], [49.0, "i", "show run"], [49.1, "o", "...\r\n(SW-CORE-01) #"]]
m = cc.mask_secrets(ev)
check("la contraseña escrita tras «Password:» queda «********»", m[3][2] == cc.SECRET_MASK, m[3])
check("los comandos quedan como se escribieron", m[1][2] == "en" and m[5][2] == "show run")
check("en ningún evento queda la contraseña", all("MiClave2323" not in str(e) for e in m))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
