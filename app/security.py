"""Verificación en dos pasos (TOTP), certificado propio del sitio y páginas públicas de privacidad y licencias."""
from __future__ import annotations

import io
import time

import pyotp
import segno
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

from .auth import require, start_session
from .config import TLS_CA
from .crypto import decrypt, encrypt, verify_password
from .db import SessionLocal, User
from .web import flash, go, log_action, render

router = APIRouter()
ISSUER = "OpenNCCM"


# --------------------------------------------------------------------------- TOTP
def totp_verify(user: User, code: str) -> bool:
    """Valida el código de 6 dígitos (±30 s) y rechaza reutilizar uno ya aceptado."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if not user.totp_secret_enc or len(code) != 6:
        return False
    totp = pyotp.TOTP(decrypt(user.totp_secret_enc))
    now_step = int(time.time()) // 30
    for step in (now_step - 1, now_step, now_step + 1):
        if step > (user.totp_last_step or 0) and pyotp.utils.strings_equal(totp.generate_otp(step), code):
            with SessionLocal() as db:
                u = db.get(User, user.id)
                u.totp_last_step = step
                db.commit()
            return True
    return False


def _qr_svg(uri: str) -> str:
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000000", light="#ffffff",
                                    xmldecl=False, svgns=True, title="Código QR para la app de autenticación")
    return buf.getvalue().decode()


@router.get("/account/2fa/setup")
def twofa_setup(request: Request):
    user = require(request)
    if user.totp_enabled:
        return go("/account#dospasos")
    secret = request.session.get("totp_new") or pyotp.random_base32()
    request.session["totp_new"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=f"{ISSUER} ({request.url.hostname})")
    return render(request, "twofa_setup.html", user, secret=secret, qr=_qr_svg(uri))


@router.post("/account/2fa/setup")
async def twofa_enable(request: Request):
    user = require(request)
    form = await request.form()
    secret = request.session.get("totp_new")
    if not secret:
        return go("/account/2fa/setup")
    code = "".join(ch for ch in form.get("code", "") if ch.isdigit())
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        flash(request, "El código no coincide. Revise que la hora del teléfono sea correcta y pruebe con el código "
                       "que se muestra ahora.", "error")
        return go("/account/2fa/setup")
    with SessionLocal() as db:
        u = db.get(User, user.id)
        u.totp_secret_enc = encrypt(secret)
        u.totp_enabled = True
        u.totp_last_step = int(time.time()) // 30
        u.session_version = (u.session_version or 0) + 1  # cierra sesiones abiertas en otros equipos
        db.commit()
        start_session(request, u)
    request.session.pop("totp_new", None)
    log_action(request, user, "2fa_enable")
    flash(request, "Verificación en dos pasos activada. A partir de ahora se pedirá el código al ingresar.")
    return go("/account#dospasos")


@router.post("/account/2fa/disable")
async def twofa_disable(request: Request):
    user = require(request)
    form = await request.form()
    with SessionLocal() as db:
        u = db.get(User, user.id)
        if not verify_password(form.get("password", ""), u.password_hash) or not totp_verify(u, form.get("code", "")):
            flash(request, "Para desactivarla escriba su contraseña y un código válido de la app.", "error")
            return go("/account#dospasos")
        u.totp_enabled, u.totp_secret_enc = False, None
        db.commit()
    log_action(request, user, "2fa_disable")
    flash(request, "Verificación en dos pasos desactivada.")
    return go("/account#dospasos")


@router.post("/users/{uid}/2fa-reset")
def twofa_reset(request: Request, uid: int):
    """Un administrador quita la verificación en dos pasos a un usuario que perdió su teléfono."""
    user = require(request, "admin")
    with SessionLocal() as db:
        u = db.get(User, uid)
        if u is None:
            return go("/users")
        u.totp_enabled, u.totp_secret_enc = False, None
        u.session_version = (u.session_version or 0) + 1
        name = u.username
        db.commit()
    log_action(request, user, "2fa_reset", name)
    flash(request, f"Verificación en dos pasos quitada a «{name}». Deberá activarla de nuevo desde «Mi cuenta».")
    return go(f"/users/{uid}/edit")


# --------------------------------------------------------------------------- certificado y páginas públicas
@router.get("/ca.crt", include_in_schema=False)
def ca_certificate():
    if not TLS_CA.is_file():
        return HTMLResponse("Este servidor no usa el certificado propio de OpenNCCM.", 404)
    return FileResponse(TLS_CA, media_type="application/x-x509-ca-cert", filename="OpenNCCM-CA.crt")


@router.get("/certificado")
def certificate_help(request: Request):
    from .auth import current_user
    return render(request, "certificado.html", current_user(request), ca_available=TLS_CA.is_file())


@router.get("/acerca")
def about(request: Request):
    from .auth import current_user
    from .db import get_setting
    org = {"name": get_setting("org_name"), "contact": get_setting("org_contact"), "address": get_setting("org_address")}
    return render(request, "acerca.html", current_user(request), org=org)
