"""Recuperación ante desastres: respaldo y restauración de la base de datos de OpenNCCM."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, DATABASE_URL, KEY_FILE
from .db import engine, get_setting

BACKUP_DIR = DATA_DIR / "system-backups"
NAME_RE = r"opennccm-\d{8}-\d{6}\.tar\.gz(\.enc)?"
ENC_MAGIC = b"OPENNCCM-ENC1:"


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("opennccm-*.tar.gz*"), key=lambda p: p.name.split(".")[0], reverse=True)


def _derive(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())


def encrypt_archive(data: bytes, passphrase: str) -> bytes:
    """AES-256-GCM con clave derivada de la contraseña (scrypt)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, nonce = os.urandom(16), os.urandom(12)
    return ENC_MAGIC + salt + nonce + AESGCM(_derive(passphrase, salt)).encrypt(nonce, data, ENC_MAGIC)


def decrypt_archive(blob: bytes, passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(ENC_MAGIC):
        raise RuntimeError("El archivo no es una copia cifrada de OpenNCCM")
    body = blob[len(ENC_MAGIC):]
    salt, nonce, ct = body[:16], body[16:28], body[28:]
    try:
        return AESGCM(_derive(passphrase, salt)).decrypt(nonce, ct, ENC_MAGIC)
    except InvalidTag:
        raise RuntimeError("Contraseña incorrecta o archivo dañado") from None


def create_backup(include_key: bool = False, passphrase: str | None = None) -> Path:
    """Copia de recuperación. Si incluye la clave de cifrado, el archivo completo se cifra con una contraseña:
    con la base y la clave juntas cualquiera podría descifrar las credenciales de los equipos."""
    from . import __version__
    if include_key and (not passphrase or len(passphrase) < 12):
        raise ValueError("Para incluir la clave de cifrado escriba una contraseña de al menos 12 caracteres "
                         "para proteger la copia.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = BACKUP_DIR / f"opennccm-{stamp}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if engine.dialect.name == "sqlite":
            dump = tmp / "opennccm.db"
            src = sqlite3.connect(engine.url.database)
            dst = sqlite3.connect(dump)
            with dst:
                src.backup(dst)  # copia consistente aunque la app esté escribiendo
            src.close()
            dst.close()
        else:
            dump = tmp / "opennccm.pgdump"
            url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
            subprocess.run(["pg_dump", "--format=custom", f"--file={dump}", url], check=True, timeout=3600)
        manifest = {"version": __version__, "created": stamp, "dialect": engine.dialect.name,
                    "includes_key": include_key}
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))
        with tarfile.open(out, "w:gz") as tar:
            tar.add(dump, arcname=dump.name)
            tar.add(tmp / "manifest.json", arcname="manifest.json")
            if include_key and KEY_FILE.exists():
                tar.add(KEY_FILE, arcname="secret.key")
    if include_key:
        enc = out.with_name(out.name + ".enc")
        enc.write_bytes(encrypt_archive(out.read_bytes(), passphrase))
        out.unlink()
        out = enc
    out.chmod(0o600)
    _apply_retention()
    _copy_remote(out)
    return out


def _apply_retention():
    keep = int(get_setting("dr_keep", "14") or 14)
    for old in list_backups()[keep:]:
        old.unlink(missing_ok=True)


def _copy_remote(path: Path) -> str | None:
    """Copia por SFTP al servidor remoto configurado (opcional)."""
    host = get_setting("dr_remote_host")
    if not host:
        return None
    import paramiko

    from app import legacy_ssh
    legacy_ssh.enable()
    t = paramiko.Transport((host, int(get_setting("dr_remote_port", "22") or 22)))
    try:
        t.connect(username=get_setting("dr_remote_user"), password=get_setting("dr_remote_password"))
        sftp = paramiko.SFTPClient.from_transport(t)
        remote_dir = get_setting("dr_remote_path", ".") or "."
        sftp.put(str(path), f"{remote_dir.rstrip('/')}/{path.name}")
        return f"{host}:{remote_dir}"
    finally:
        t.close()


class KeyWarning(RuntimeError):
    """La base se restauró, pero la clave de este servidor no descifra sus datos (falta el secret.key original)."""


def _restore_key(data: bytes, stamp: str) -> str:
    """Pone la clave de la copia. Si el servidor ya tenía otra (la que crea el instalador), la guarda aparte."""
    if KEY_FILE.exists():
        if KEY_FILE.read_bytes() == data:
            return " La clave de la copia ya era la de este servidor."
        old = KEY_FILE.with_name(f"{KEY_FILE.name}.antes-de-restaurar-{stamp}")
        KEY_FILE.rename(old)
        note = f" Clave restaurada (la que tenía este servidor se guardó en {old})."
    else:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        note = " Clave restaurada."
    KEY_FILE.write_bytes(data)
    KEY_FILE.chmod(0o600)
    return note


def _key_check() -> tuple[int, int]:
    """(datos que no se pueden descifrar, datos probados) con la clave actual: contraseñas y últimas configuraciones."""
    from sqlalchemy import text

    from . import crypto
    crypto._key = None  # la clave pudo cambiar al restaurar
    with engine.connect() as c:
        tokens = [r[0] for r in c.execute(text("SELECT password_enc FROM credentials WHERE password_enc IS NOT NULL "
                                               "ORDER BY id DESC LIMIT 20"))]
        tokens += [r[0] for r in c.execute(text("SELECT content_enc FROM config_versions ORDER BY id DESC LIMIT 20"))]
    bad = sum(crypto.try_decrypt(t, None) is None for t in tokens if t)
    return bad, len([t for t in tokens if t])


def _restore_sqlite(tar: tarfile.TarFile, stamp: str):
    current = Path(engine.url.database)
    engine.dispose()  # cierra conexiones abiertas antes de reemplazar el archivo
    if current.exists():
        current.rename(current.with_suffix(f".db.antes-de-restaurar-{stamp}"))
    for suffix in ("-wal", "-shm"):
        Path(str(current) + suffix).unlink(missing_ok=True)
    with open(current, "wb") as f:
        f.write(tar.extractfile("opennccm.db").read())


def _restore_postgresql(tar: tarfile.TarFile):
    """pg_restore sobre la base configurada: borra y vuelve a crear sus tablas con los datos de la copia."""
    engine.dispose()
    url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "opennccm.pgdump"
        dump.write_bytes(tar.extractfile("opennccm.pgdump").read())
        r = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                            "--single-transaction", f"--dbname={url}", str(dump)],
                           capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError("pg_restore falló: " + (r.stderr.strip().splitlines() or ["sin detalle"])[-1])


def restore_backup(archive: Path, passphrase: str | None = None) -> str:
    """Restaura una copia (solo desde la CLI y con el servicio detenido). Si la clave del servidor no descifra
    los datos restaurados, lanza KeyWarning con el mensaje completo (la base ya quedó restaurada)."""
    if archive.name.endswith(".enc"):
        if not passphrase:
            raise RuntimeError("Esta copia está cifrada: hace falta su contraseña")
        fileobj = io.BytesIO(decrypt_archive(archive.read_bytes(), passphrase))
    else:
        fileobj = open(archive, "rb")
    stamp = f"{datetime.now():%Y%m%d%H%M%S}"
    with fileobj, tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())
        names_db = {"sqlite": "SQLite", "postgresql": "PostgreSQL"}
        if manifest["dialect"] != engine.dialect.name:
            raise RuntimeError(f"La copia es de {names_db.get(manifest['dialect'], manifest['dialect'])} y este "
                               f"servidor usa {names_db.get(engine.dialect.name, engine.dialect.name)}: instale "
                               f"OpenNCCM con la misma base de datos que el servidor original")
        if engine.dialect.name == "sqlite":
            _restore_sqlite(tar, stamp)
        elif engine.dialect.name == "postgresql":
            _restore_postgresql(tar)
        else:
            raise RuntimeError(f"Base de datos no admitida para restaurar: {engine.dialect.name}")
        msg = f"Base restaurada desde {archive.name} (creada {manifest['created']})."
        if "secret.key" in names:
            msg += _restore_key(tar.extractfile("secret.key").read(), stamp)
    bad, total = _key_check()
    if bad:
        raise KeyWarning(
            f"{msg}\nAVISO: la clave de este servidor ({KEY_FILE}) no corresponde a la copia: {bad} de {total} datos "
            f"cifrados probados (contraseñas y configuraciones) no se pueden leer. Copie el secret.key del servidor "
            f"original a {KEY_FILE} (propietario opennccm, permisos 600) y reinicie el servicio, o restaure una copia "
            f"hecha con «--with-key».")
    return msg
