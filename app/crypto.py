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


PBKDF2_ITERATIONS = 600_000  # recomendación OWASP 2023 para PBKDF2-HMAC-SHA256


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _parse_hash(stored: str):
    parts = stored.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        return int(parts[1]), parts[2], parts[3]
    if len(parts) == 3 and parts[0] == "pbkdf2":  # formato anterior a la 1.6.0 (390 000 iteraciones)
        return 390_000, parts[1], parts[2]
    raise ValueError("formato de contraseña desconocido")


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations, salt, dk = _parse_hash(stored)
    except (ValueError, AttributeError):
        return False
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(test.hex(), dk)


def needs_rehash(stored: str) -> bool:
    try:
        return _parse_hash(stored)[0] < PBKDF2_ITERATIONS
    except (ValueError, AttributeError):
        return True


_DUMMY_HASH = None


def dummy_verify(password: str) -> None:
    """Mismo tiempo de respuesta cuando el usuario no existe (no revela qué usuarios existen)."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("x")
    verify_password(password, _DUMMY_HASH)


def password_problem(password: str, username: str = "") -> str | None:
    """Política de contraseñas: None si es aceptable, o el motivo en español."""
    if len(password) < 10:
        return "La contraseña debe tener al menos 10 caracteres."
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        return "La contraseña debe combinar letras y números."
    if username and username.lower() in password.lower():
        return "La contraseña no puede contener el nombre de usuario."
    if password.lower() in {"admin12345", "password123", "contraseña123", "1234567890a", "opennccm123"}:
        return "Esa contraseña es demasiado común."
    return None
