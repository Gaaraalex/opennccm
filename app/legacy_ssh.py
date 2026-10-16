"""Compatibilidad SSH con equipos de red antiguos.

Paramiko 5 eliminó los algoritmos basados en SHA-1 que todavía usan muchos switches y routers
(p. ej. Ubiquiti EdgeSwitch, Cisco IOS 12.x, HP ProCurve antiguos):
  - clave de host / firma  «ssh-rsa»
  - intercambio de claves «diffie-hellman-group14-sha1», «-group1-sha1» y «-group-exchange-sha1»

Este módulo los vuelve a registrar AL FINAL de la lista de preferencias: la negociación SSH elige el
primer algoritmo del cliente que el equipo también admite, así que los equipos modernos siguen usando
algoritmos modernos y estos solo se usan cuando el equipo no ofrece nada mejor.
"""
from __future__ import annotations

import logging
from hashlib import sha1

import paramiko
from cryptography.hazmat.primitives import hashes
from paramiko.kex_gex import KexGexSHA256
from paramiko.kex_group14 import KexGroup14SHA256
from paramiko.rsakey import RSAKey

log = logging.getLogger("opennccm")


class KexGroup14SHA1(KexGroup14SHA256):
    name = "diffie-hellman-group14-sha1"
    hash_algo = sha1


class KexGroup1SHA1(KexGroup14SHA256):
    # RFC 2409 §6.2 – Oakley Group 2 (1024 bits)
    P = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE65381FFFFFFFFFFFFFFFF  # noqa: E501
    G = 2
    name = "diffie-hellman-group1-sha1"
    hash_algo = sha1


class KexGexSHA1(KexGexSHA256):
    name = "diffie-hellman-group-exchange-sha1"
    hash_algo = sha1


LEGACY_KEX = (KexGexSHA1, KexGroup14SHA1, KexGroup1SHA1)  # en orden de preferencia
_enabled = False


def enable() -> None:
    """Registra los algoritmos antiguos en Paramiko (idempotente)."""
    global _enabled
    if _enabled:
        return
    try:
        _register()
    except Exception:  # noqa: BLE001 - si una versión futura de Paramiko cambia, conectar igual con lo moderno
        log.exception("SSH: no se pudieron habilitar los algoritmos de compatibilidad")
    _enabled = True


def _register() -> None:
    T = paramiko.Transport
    # firma/clave de host ssh-rsa (RSA con SHA-1)
    if "ssh-rsa" not in RSAKey.HASHES:
        RSAKey.HASHES = {**RSAKey.HASHES, "ssh-rsa": hashes.SHA1, "ssh-rsa-cert-v01@openssh.com": hashes.SHA1}
    T._key_info = {**T._key_info, "ssh-rsa": RSAKey}
    if "ssh-rsa" not in T._preferred_keys:
        T._preferred_keys = tuple(T._preferred_keys) + ("ssh-rsa",)
    # intercambio de claves con SHA-1
    kex_info = dict(T._kex_info)
    preferred = list(T._preferred_kex)
    for cls in LEGACY_KEX:
        kex_info.setdefault(cls.name, cls)
        if cls.name not in preferred:
            preferred.append(cls.name)
    T._kex_info, T._preferred_kex = kex_info, tuple(preferred)
    log.debug("SSH: algoritmos de compatibilidad habilitados (ssh-rsa, kex SHA-1) como última opción")


def offered() -> dict:
    """Algoritmos que OpenNCCM ofrece al conectar (para diagnóstico)."""
    T = paramiko.Transport
    return {"keys": list(T._preferred_keys), "kex": list(T._preferred_kex),
            "ciphers": list(T._preferred_ciphers), "macs": list(T._preferred_macs)}
