import time

from fastapi import Request
from sqlalchemy import select

from .db import Device, SessionLocal, User, UserGroup, get_setting

ROLES = {"viewer": 0, "operator": 1, "admin": 2}
# páginas accesibles mientras un administrador está obligado a activar la verificación en dos pasos
_SETUP_2FA_PATHS = ("/account", "/logout")


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


class TwoFactorSetupRequired(Exception):
    pass


_settings_cache: dict[str, tuple[float, str]] = {}


def cached_setting(key: str, default: str) -> str:
    """Ajustes que se consultan en cada petición: se leen de la base como mucho cada 30 s."""
    hit = _settings_cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    value = get_setting(key, default)
    _settings_cache[key] = (time.time(), value)
    return value


def clear_settings_cache():
    _settings_cache.clear()


SESSION_HOURS = 12      # una sesión normal dura como mucho esto (y se cierra antes por inactividad)
REMEMBER_MAX_DAYS = 30  # tope de «Recordarme» (también es la vida máxima de la cookie)


def remember_days() -> int:
    """Días que dura una sesión con «Recordarme» (0: la opción está desactivada en Ajustes)."""
    try:
        return min(max(int(cached_setting("remember_days", "14") or 0), 0), REMEMBER_MAX_DAYS)
    except ValueError:
        return 14


def start_session(request: Request, user: User) -> None:
    """Sesión nueva tras un ingreso correcto (contraseña y, si aplica, segundo paso).
    Con «Recordarme» (marcado en el formulario o guardado mientras se espera el segundo paso) dura varios días y no se
    cierra por inactividad; si no, como mucho SESSION_HOURS horas y se cierra tras los minutos de inactividad."""
    lang = getattr(user, "lang", None) or request.session.get("lang")
    remember = bool(getattr(request.state, "remember", False) or request.session.get("pending_rem")) and remember_days() > 0
    request.session.clear()
    if lang:
        request.session["lang"] = lang
    now = int(time.time())
    request.session["uid"] = user.id
    request.session["sv"] = user.session_version or 0
    request.session["seen"] = now
    request.session["exp"] = now + (remember_days() * 86400 if remember else SESSION_HOURS * 3600)
    if remember:
        request.session["rem"] = 1


def current_user(request: Request) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    now = int(time.time())
    idle = int(cached_setting("session_idle_minutes", "30") or 30) * 60
    seen = int(request.session.get("seen", now))
    remembered = bool(request.session.get("rem")) and remember_days() > 0  # si se desactiva, vuelven a las reglas normales
    exp = int(request.session.get("exp") or seen + SESSION_HOURS * 3600)
    if remembered:
        exp = min(exp, seen + remember_days() * 86400)  # bajar los días en Ajustes también acorta las ya abiertas
    if now > exp or (not remembered and idle > 0 and now - seen > idle):  # caducidad o cierre por inactividad
        request.session.clear()
        request.session["expired"] = 1
        return None
    with SessionLocal() as db:
        user = db.get(User, uid)
        # cuenta desactivada, borrada o contraseña cambiada en otra sesión: se cierra esta sesión
        if user is None or not user.active or request.session.get("sv", 0) != (user.session_version or 0):
            request.session.clear()
            return None
    if now - seen > 60:  # no reescribir la cookie en cada petición
        request.session["seen"] = now
    return user


def require(request: Request, role: str = "viewer") -> User:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    if (user.role == "admin" and not user.totp_enabled and cached_setting("require_2fa_admins", "0") == "1"
            and not request.url.path.startswith(_SETUP_2FA_PATHS)):
        raise TwoFactorSetupRequired()
    if ROLES.get(user.role, 0) < ROLES[role]:
        raise Forbidden()
    return user


def user_group_ids(user_id: int) -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user_id)))


def device_scope(user: User) -> set[int] | None:
    """Equipos visibles para el usuario. None = todos (admin o usuario sin grupos asignados)."""
    if user.role == "admin":
        return None
    gids = user_group_ids(user.id)
    if not gids:
        return None
    with SessionLocal() as db:
        return set(db.scalars(select(Device.id).where(Device.group_id.in_(gids))))


def check_device(user: User, device_id: int) -> None:
    scope = device_scope(user)
    if scope is not None and device_id not in scope:
        raise Forbidden()


def filter_ids(user: User, ids) -> list[int]:
    scope = device_scope(user)
    return [i for i in ids if scope is None or i in scope]
