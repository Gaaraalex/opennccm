"""«Recordarme» en el inicio de sesión, con un reloj simulado: la sesión recordada dura los días de Ajustes y no se cierra
por inactividad; la normal se cierra por inactividad y a las 12 horas; 0 días desactiva la opción (también para las
sesiones ya abiertas); con verificación en dos pasos se aplica al terminar el segundo paso."""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["NCM_DATA_DIR"] = tmp
os.environ["NCM_KEY_FILE"] = os.path.join(tmp, "secret.key")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import auth  # noqa: E402
from app.crypto import hash_password  # noqa: E402
from app.db import SessionLocal, User, init_db, set_setting  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


class Clock:
    t = 2_000_000_000.0

    def __call__(self):
        return self.t


clock = Clock()
auth.time.time = clock


class State:
    pass


class Req:
    def __init__(self, remember=False, session=None):
        self.session = dict(session or {})
        self.state = State()
        if remember:
            self.state.remember = True


def setting(k, v):
    set_setting(k, v)
    auth.clear_settings_cache()


init_db()
with SessionLocal() as db:
    u = User(username="ana", password_hash=hash_password("Clave-de-prueba-1"), role="operator")
    db.add(u)
    db.commit()
    user = db.get(User, u.id)
setting("session_idle_minutes", "30")
setting("remember_days", "14")
H, D = 3600, 86400

# sesión normal
r = Req()
auth.start_session(r, user)
check("sesión normal: caduca a las 12 horas y no queda marcada como recordada",
      r.session["exp"] - clock.t == 12 * H and "rem" not in r.session)
clock.t += 20 * 60
check("sesión normal: sigue abierta tras 20 min de uso", auth.current_user(r) is not None)
clock.t += 31 * 60
check("sesión normal: se cierra tras 30 min sin actividad", auth.current_user(r) is None and r.session.get("expired"))

# sesión recordada
r = Req(remember=True)
auth.start_session(r, user)
check("«Recordarme»: la sesión dura 14 días", r.session["exp"] - clock.t == 14 * D and r.session.get("rem") == 1)
clock.t += 3 * H
check("«Recordarme»: no se cierra por 3 horas de inactividad", auth.current_user(r) is not None)
clock.t += 2 * D
check("«Recordarme»: sigue abierta al volver dos días después", auth.current_user(r) is not None)
clock.t += 13 * D
check("«Recordarme»: se cierra al pasar los 14 días", auth.current_user(r) is None)

# cambiar los días en Ajustes
r = Req(remember=True)
auth.start_session(r, user)
setting("remember_days", "3")
clock.t += 4 * D
check("bajar los días en Ajustes también acorta las sesiones ya abiertas", auth.current_user(r) is None)
setting("remember_days", "14")
r = Req(remember=True)
auth.start_session(r, user)
setting("remember_days", "0")
clock.t += 2 * H
check("con 0 días, las sesiones recordadas vuelven a cerrarse por inactividad", auth.current_user(r) is None)
r = Req(remember=True)
auth.start_session(r, user)
check("con 0 días, marcar «Recordarme» no tiene efecto", "rem" not in r.session and r.session["exp"] - clock.t == 12 * H)
setting("remember_days", "14")

# segundo paso: la elección se guarda mientras se espera el código
r = Req(session={"pending_uid": user.id, "pending_rem": 1})
auth.start_session(r, user)
check("con verificación en dos pasos se aplica «Recordarme» al terminar el segundo paso",
      r.session.get("rem") == 1 and "pending_rem" not in r.session)

# sesiones abiertas antes de esta versión (sin «exp»)
r = Req(session={"uid": user.id, "sv": 0, "seen": int(clock.t)})
check("una sesión abierta antes de actualizar sigue funcionando", auth.current_user(r) is not None)
clock.t += 31 * 60
check("y se sigue cerrando por inactividad", auth.current_user(r) is None)

# cerrar sesión en todos los equipos (cambio de contraseña) también cierra las recordadas
r = Req(remember=True)
auth.start_session(r, user)
with SessionLocal() as db:
    db.get(User, user.id).session_version = (user.session_version or 0) + 1
    db.commit()
check("cambiar la contraseña cierra también las sesiones recordadas", auth.current_user(r) is None)

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
