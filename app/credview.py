"""Ver el usuario y la contraseña de la credencial de un equipo.

Ajustes › Seguridad y acceso decide quién puede («cred_view»): nadie (lo de fábrica), solo los administradores, o
también los operadores. Además, quien tenga permiso:

- debe tener activa la verificación en dos pasos (MFA) en su cuenta; sin ella se le dice que la active primero;
- cada vez que pulsa «Ver» confirma con su usuario y su contraseña de OpenNCCM (las cuentas de Microsoft, que no
  tienen contraseña aquí, con el código de su app de verificación).

Los intentos fallidos cuentan para el bloqueo de la cuenta igual que en el inicio de sesión, y al bloquearla se cierran
sus sesiones. Cada vista y cada intento fallido quedan en la auditoría. La respuesta no se guarda en el navegador.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .auth import ROLES, Forbidden, cached_setting, check_device, require
from .crypto import decrypt, dummy_verify, verify_password
from .db import Credential, Device, SessionLocal, User, now
from .i18n import _, _f
from .web import flash, go, log_action, render, templates

router = APIRouter()

CHOICES = ("off", "admin", "operator")  # nadie | solo administradores | administradores y operadores


def who() -> str:
    v = cached_setting("cred_view", "off")
    return v if v in CHOICES else "off"


def allowed(user) -> bool:
    """El ajuste deja a este rol ver credenciales (todavía falta comprobar su MFA y su contraseña)."""
    w = who()
    return user is not None and w != "off" and ROLES.get(user.role, 0) >= ROLES[w]


templates.env.globals["can_view_creds"] = allowed


def _check_identity(user: User, username: str, secret: str) -> bool:
    """Usuario y contraseña de la propia cuenta: local, de Active Directory o, si es de Microsoft, su código MFA."""
    same = username.strip().lower() == (user.username or "").lower()
    source = user.auth_source or "local"
    if source == "oidc":
        from .security import totp_verify
        return same and totp_verify(user, secret)
    if source == "ldap":
        from . import extauth
        if not same:
            return False
        try:
            return extauth.ldap_authenticate(user.username, secret) is not None
        except extauth.ExtAuthError:
            return False
    if not same:
        dummy_verify(secret)
        return False
    return verify_password(secret, user.password_hash)


def _failed(request: Request, user: User, what: str) -> bool:
    """Suma un intento fallido; True si con este la cuenta queda bloqueada (y sus sesiones cerradas)."""
    from .main import ACCOUNT_LOCK_AFTER, ACCOUNT_LOCK_MINUTES
    from .services import add_event
    log_action(request, user, "credential_view_failed", what)
    with SessionLocal() as db:
        u = db.get(User, user.id)
        u.failed_logins = (u.failed_logins or 0) + 1
        locked = u.failed_logins >= ACCOUNT_LOCK_AFTER
        if locked:
            u.locked_until = now() + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
            u.failed_logins = 0
            u.session_version = (u.session_version or 0) + 1
        db.commit()
    if locked:
        add_event("warning", "Cuenta bloqueada",
                  f"«{user.username}» bloqueada {ACCOUNT_LOCK_MINUTES} min tras {ACCOUNT_LOCK_AFTER} intentos fallidos "
                  f"de confirmar su identidad para ver una credencial ({what}).")
        request.session.clear()
    return locked


def _page(request: Request, user, cred: Credential, target: str, url: str, **ctx):
    resp = render(request, "cred_view.html", user, cred=cred, target=target, url=url, **ctx)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _load(user, device_id: int | None, cid: int | None):
    """La credencial pedida y a qué equipo o nombre se refiere; Forbidden si no se puede ver."""
    if not allowed(user):
        raise Forbidden()
    with SessionLocal() as db:
        if device_id is not None:
            check_device(user, device_id)
            dev = db.get(Device, device_id)
            if dev is None or dev.credential_id is None:
                raise Forbidden()
            cred, target = db.get(Credential, dev.credential_id), dev.name
        else:
            if user.role != "admin":  # la lista de credenciales es de administradores
                raise Forbidden()
            cred = db.get(Credential, cid)
            target = cred.name if cred else ""
            if cred is not None:
                target = f"{cred.name} · " + _f("la usan {0} equipos", db.scalar(
                    select(func.count(Device.id)).where(Device.credential_id == cred.id)) or 0)
    if cred is None:
        raise Forbidden()
    return cred, target


def _guard(request: Request, role: str = "operator"):
    user = require(request, role)
    if getattr(request.state, "api_token", False):  # nunca con un token de API: solo con una sesión y la contraseña
        raise Forbidden()
    return user


async def _view(request: Request, device_id: int | None = None, cid: int | None = None):
    user = _guard(request)
    cred, target = _load(user, device_id, cid)
    url = request.url.path
    if not user.totp_enabled:
        return _page(request, user, cred, target, url, need_mfa=True)
    if request.method == "GET":
        return _page(request, user, cred, target, url)
    form = await request.form()
    if user.locked_until and user.locked_until > now():
        return _page(request, user, cred, target, url, error=_("La cuenta está bloqueada temporalmente por intentos fallidos. Inténtelo más tarde."))
    what = f"{cred.name} ({target.split(' · ')[0]})" if device_id is not None else cred.name
    if not _check_identity(user, form.get("username", ""), form.get("password", "")):
        if _failed(request, user, what):
            flash(request, _("La cuenta quedó bloqueada temporalmente por intentos fallidos y se cerraron sus sesiones."), "error")
            return go("/login")
        return _page(request, user, cred, target, url, error=_("Usuario o contraseña incorrectos") if (user.auth_source or "local") != "oidc"
                     else _("Usuario o código incorrectos"))
    with SessionLocal() as db:
        u = db.get(User, user.id)
        u.failed_logins = 0
        db.commit()
    log_action(request, user, "credential_view", what)
    shown = {"username": cred.username,
             "password": decrypt(cred.password_enc) if cred.password_enc else "",
             "secret": decrypt(cred.secret_enc) if cred.secret_enc else ""}
    return _page(request, user, cred, target, url, shown=shown)


@router.get("/devices/{device_id}/credential/view")
@router.post("/devices/{device_id}/credential/view")
async def device_credential_view(request: Request, device_id: int):
    return await _view(request, device_id=device_id)


@router.get("/credentials/{cid}/view")
@router.post("/credentials/{cid}/view")
async def credential_view(request: Request, cid: int):
    return await _view(request, cid=cid)
