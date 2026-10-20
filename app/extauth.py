"""Inicio de sesión con directorios externos: Active Directory (LDAP) y Microsoft Entra ID / Azure AD (OpenID Connect).

- Active Directory: se usa el mismo formulario de usuario y contraseña. Con una cuenta de servicio se busca al usuario,
  se comprueba su contraseña haciendo «bind» con su DN y su rol sale de los grupos a los que pertenece (también grupos
  anidados, con la regla LDAP_MATCHING_RULE_IN_CHAIN de AD).
- Azure / Entra ID (o cualquier proveedor OpenID Connect): botón «Entrar con Microsoft». Flujo de código de autorización con
  PKCE, «state» y «nonce»; el token de identidad se valida (firma con las claves del proveedor, emisor, audiencia,
  caducidad y nonce) y el rol sale de los grupos o roles de aplicación del token.

En ambos casos el usuario se crea o actualiza en OpenNCCM con auth_source «ldap» u «oidc» (sin contraseña local utilizable),
así siguen funcionando la restricción por grupos de equipos, la verificación en dos pasos y la auditoría.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

import requests

from .db import get_setting

log = logging.getLogger("opennccm.extauth")
LDAP_STRATEGY = None  # las pruebas usan el directorio simulado de ldap3 (MOCK_SYNC); en producción, el normal
ROLE_ORDER = ("admin", "operator", "viewer")
USERNAME_OK = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")


@dataclass
class ExtUser:
    username: str
    role: str | None          # None: ningún grupo da acceso
    email: str | None = None
    display: str | None = None
    groups: list[str] = field(default_factory=list)


class ExtAuthError(Exception):
    """Error de configuración o de conexión con el directorio (se muestra al administrador, no al usuario)."""


def _role_from(groups: list[str], mapping: dict[str, str], default: str) -> str | None:
    """El rol más alto cuyo grupo (DN, nombre u Object ID, sin distinguir mayúsculas) está en la lista del usuario."""
    have = {g.strip().lower() for g in groups if g}
    for role in ROLE_ORDER:
        for want in (mapping.get(role) or "").split(";"):
            want = want.strip().lower()
            if want and (want in have or any(h.startswith("cn=" + want + ",") for h in have)):
                return role
    return default if default in ROLE_ORDER else None


# --------------------------------------------------------------------------- Active Directory (LDAP)
def ldap_settings() -> dict:
    return {k: get_setting(f"ldap_{k}", d) for k, d in (
        ("enabled", "0"), ("url", ""), ("starttls", "0"), ("verify_tls", "1"), ("bind_dn", ""), ("bind_password", ""),
        ("base_dn", ""), ("user_filter", "(&(objectClass=user)(sAMAccountName={username}))"),
        ("group_admin", ""), ("group_operator", ""), ("group_viewer", ""), ("default_role", ""))}


def ldap_enabled() -> bool:
    return get_setting("ldap_enabled", "0") == "1" and bool(get_setting("ldap_url", ""))


def _ldap_server(cfg: dict):
    import ssl

    from ldap3 import Server, Tls
    url = cfg["url"].strip()
    tls = Tls(validate=ssl.CERT_REQUIRED if cfg.get("verify_tls", "1") == "1" else ssl.CERT_NONE)
    return Server(url, use_ssl=url.lower().startswith("ldaps://"), tls=tls, get_info="NO_INFO", connect_timeout=8)


def _strategy() -> dict:
    return {"client_strategy": LDAP_STRATEGY} if LDAP_STRATEGY else {}


def _escape(value: str) -> str:
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(value)


def ldap_authenticate(username: str, password: str, cfg: dict | None = None, server=None) -> ExtUser | None:
    """Comprueba usuario y contraseña en el directorio. Devuelve None si no son válidos (o el usuario no existe).
    Lanza ExtAuthError si el directorio no responde o la cuenta de servicio no sirve."""
    from ldap3 import SUBTREE, Connection
    from ldap3.core.exceptions import LDAPException
    cfg = cfg or ldap_settings()
    if not password or not username:  # un «bind» sin contraseña sería anónimo y daría por buena cualquier cuenta
        return None
    server = server or _ldap_server(cfg)
    try:
        svc = Connection(server, user=cfg["bind_dn"] or None, password=cfg["bind_password"] or None,
                         auto_bind=False, receive_timeout=10, raise_exceptions=False, **_strategy())
        if cfg.get("starttls") == "1" and not str(cfg["url"]).lower().startswith("ldaps://"):
            svc.open()
            svc.start_tls()
        if not svc.bind():
            raise ExtAuthError(f"La cuenta de servicio no pudo iniciar sesión en el directorio ({svc.result.get('description')})")
        flt = (cfg["user_filter"] or "(sAMAccountName={username})").replace("{username}", _escape(username))
        svc.search(cfg["base_dn"], flt, SUBTREE, attributes=["mail", "displayName", "memberOf", "userPrincipalName"],
                   size_limit=2)
        entries = list(svc.entries)
        if len(entries) != 1:
            svc.unbind()
            return None
        entry = entries[0]
        dn = entry.entry_dn
        groups = [str(g) for g in (entry.memberOf.values if "memberOf" in entry else [])]
        # grupos anidados (Active Directory): pertenencia en cadena a cada grupo configurado
        for role in ROLE_ORDER:
            for gdn in (cfg.get(f"group_{role}") or "").split(";"):
                gdn = gdn.strip()
                if gdn and "=" in gdn and gdn.lower() not in {g.lower() for g in groups}:
                    nested = f"(&(distinguishedName={_escape(dn)})(memberOf:1.2.840.113556.1.4.1941:={_escape(gdn)}))"
                    try:  # solo Active Directory entiende esta regla; otro LDAP simplemente no devuelve nada
                        if svc.search(cfg["base_dn"], nested, SUBTREE, attributes=["cn"], size_limit=1) and svc.entries:
                            groups.append(gdn)
                    except LDAPException:
                        pass
        svc.unbind()
        user = Connection(server, user=dn, password=password, auto_bind=False, receive_timeout=10, raise_exceptions=False, **_strategy())
        if cfg.get("starttls") == "1" and not str(cfg["url"]).lower().startswith("ldaps://"):
            user.open()
            user.start_tls()
        ok = user.bind()
        user.unbind()
        if not ok:
            return None
    except LDAPException as e:
        raise ExtAuthError(f"No se pudo consultar el directorio: {e}") from e
    mapping = {r: cfg.get(f"group_{r}", "") for r in ROLE_ORDER}
    role = _role_from(groups, mapping, cfg.get("default_role", ""))
    def first(attr):
        try:
            v = entry[attr].value
            return str(v) if v else None
        except Exception:  # noqa: BLE001 — atributo ausente en el directorio
            return None
    return ExtUser(username=username, role=role, email=first("mail"), display=first("displayName"), groups=groups)


def ldap_test(cfg: dict, username: str = "", server=None) -> str:
    """«Probar conexión» de Ajustes: la cuenta de servicio entra y, si se indica, encuentra al usuario y sus grupos."""
    from ldap3 import SUBTREE, Connection
    from ldap3.core.exceptions import LDAPException
    try:
        server = server or _ldap_server(cfg)
        c = Connection(server, user=cfg["bind_dn"] or None, password=cfg["bind_password"] or None, auto_bind=False,
                       receive_timeout=10, raise_exceptions=False, **_strategy())
        if cfg.get("starttls") == "1" and not str(cfg["url"]).lower().startswith("ldaps://"):
            c.open()
            c.start_tls()
        if not c.bind():
            raise ExtAuthError(f"La cuenta de servicio no pudo iniciar sesión ({c.result.get('description')})")
        if not username:
            c.unbind()
            return "ok"
        flt = (cfg["user_filter"] or "(sAMAccountName={username})").replace("{username}", _escape(username))
        c.search(cfg["base_dn"], flt, SUBTREE, attributes=["memberOf"], size_limit=2)
        if len(c.entries) != 1:
            c.unbind()
            return "notfound"
        groups = [str(g) for g in (c.entries[0].memberOf.values if "memberOf" in c.entries[0] else [])]
        c.unbind()
        role = _role_from(groups, {r: cfg.get(f"group_{r}", "") for r in ROLE_ORDER}, cfg.get("default_role", ""))
        return f"found:{role or ''}:{len(groups)}"
    except LDAPException as e:
        raise ExtAuthError(f"No se pudo conectar con el directorio: {e}") from e


# --------------------------------------------------------------------------- Azure / Entra ID (OpenID Connect)
def oidc_settings() -> dict:
    return {k: get_setting(f"oidc_{k}", d) for k, d in (
        ("enabled", "0"), ("tenant", ""), ("client_id", ""), ("client_secret", ""), ("discovery", ""),
        ("button", ""), ("group_admin", ""), ("group_operator", ""), ("group_viewer", ""), ("default_role", ""))}


def oidc_enabled() -> bool:
    return get_setting("oidc_enabled", "0") == "1" and bool(get_setting("oidc_client_id", "")) and \
        bool(get_setting("oidc_tenant", "") or get_setting("oidc_discovery", ""))


def discovery_url(cfg: dict) -> str:
    """URL de descubrimiento: la del proveedor indicado o la de Entra ID para el tenant."""
    if cfg.get("discovery"):
        return cfg["discovery"].strip()
    return f"https://login.microsoftonline.com/{cfg['tenant'].strip()}/v2.0/.well-known/openid-configuration"


_cache: dict[str, tuple[float, dict]] = {}


def _get_json(url: str, ttl: int = 3600) -> dict:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    _cache[url] = (time.time(), data)
    return data


def oidc_begin(cfg: dict, redirect_uri: str) -> tuple[str, dict]:
    """URL de autorización y los valores a guardar en la sesión (state, nonce y verificador PKCE)."""
    from urllib.parse import urlencode
    meta = _get_json(discovery_url(cfg))
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": cfg["client_id"], "response_type": "code", "redirect_uri": redirect_uri,
         "scope": "openid profile email", "response_mode": "query", "state": state, "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return meta["authorization_endpoint"] + "?" + urlencode(q), {"state": state, "nonce": nonce, "verifier": verifier,
                                                                  "at": int(time.time())}


def oidc_finish(cfg: dict, redirect_uri: str, code: str, pending: dict) -> ExtUser:
    """Canjea el código, valida el token de identidad y devuelve el usuario con su rol."""
    import jwt
    meta = _get_json(discovery_url(cfg))
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": cfg["client_id"], "code_verifier": pending["verifier"]}
    if cfg.get("client_secret"):
        data["client_secret"] = cfg["client_secret"]
    r = requests.post(meta["token_endpoint"], data=data, timeout=15)
    if r.status_code != 200:
        raise ExtAuthError(f"El proveedor rechazó el código ({r.status_code}): {r.text[:200]}")
    id_token = r.json().get("id_token")
    if not id_token:
        raise ExtAuthError("El proveedor no devolvió un token de identidad")
    jwks = jwt.PyJWKClient(meta["jwks_uri"], cache_keys=True, timeout=10)
    key = jwks.get_signing_key_from_jwt(id_token).key
    issuer = meta["issuer"]
    if "{tenantid}" in issuer:  # descubrimiento «common/organizations» de Entra ID
        issuer = issuer.replace("{tenantid}", jwt.decode(id_token, options={"verify_signature": False}).get("tid", ""))
    claims = jwt.decode(id_token, key=key, algorithms=["RS256", "RS384", "RS512", "ES256", "PS256"],
                        audience=cfg["client_id"], issuer=issuer, leeway=60,
                        options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    if not secrets.compare_digest(str(claims.get("nonce", "")), pending["nonce"]):
        raise ExtAuthError("El token no corresponde a este inicio de sesión (nonce)")
    username = (claims.get("preferred_username") or claims.get("upn") or claims.get("email") or "").strip().lower()
    if not USERNAME_OK.match(username):
        raise ExtAuthError(f"El nombre de usuario del proveedor no es válido para OpenNCCM: «{username[:80]}»")
    groups = [str(g) for g in (claims.get("groups") or [])] + [str(g) for g in (claims.get("roles") or [])]
    mapping = {r: cfg.get(f"group_{r}", "") for r in ROLE_ORDER}
    return ExtUser(username=username, role=_role_from(groups, mapping, cfg.get("default_role", "")),
                   email=claims.get("email"), display=claims.get("name"), groups=groups)


# --------------------------------------------------------------------------- alta / actualización del usuario local
def upsert_user(db, ext: ExtUser, source: str):
    """Crea o actualiza el usuario de OpenNCCM que corresponde a la cuenta del directorio.
    Devuelve (usuario, motivo); usuario None si no puede entrar (sin grupo con acceso, desactivado o es una cuenta local)."""
    from sqlalchemy import func, select

    from .crypto import hash_password
    from .db import User
    if ext.role is None:
        return None, "norole"
    user = db.scalars(select(User).where(func.lower(User.username) == ext.username.lower())).first()
    if user is None:
        user = User(username=ext.username, role=ext.role, email=ext.email, active=True, auth_source=source,
                    password_hash=hash_password(secrets.token_urlsafe(32)))  # contraseña local inutilizable
        db.add(user)
    elif (user.auth_source or "local") == "local":
        return None, "local"  # una cuenta local con el mismo nombre no se «adueña» desde el directorio
    else:
        if not user.active:
            return None, "inactive"
        user.role, user.auth_source = ext.role, source
        if ext.email:
            user.email = ext.email
    db.flush()
    return user, ""
