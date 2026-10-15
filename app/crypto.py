"""Cifrado AES-256-GCM de credenciales y configuraciones almacenadas."""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import KEY_FILE

_key: bytes | None = None


def _load() -> bytes:
    global _key
    if _key is None:
        if not KEY_FILE.exists():
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            KEY_FILE.write_bytes(AESGCM.generate_key(bit_length=256))
            os.chmod(KEY_FILE, 0o600)
        _key = KEY_FILE.read_bytes()
        if len(_key) != 32:
            raise RuntimeError(f"La clave {KEY_FILE} debe tener 32 bytes (AES-256)")
    return _key


def encrypt(text: str | None) -> str | None:
    if text is None or text == "":
        return None
    nonce = os.urandom(12)
    ct = AESGCM(_load()).encrypt(nonce, text.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    raw = base64.b64decode(token)
    return AESGCM(_load()).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def session_secret() -> str:
    return hmac.new(_load(), b"opennccm-session", hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 390000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 390000)
    return hmac.compare_digest(test.hex(), dk)
