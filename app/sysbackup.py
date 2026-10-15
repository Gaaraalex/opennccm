"""Recuperación ante desastres: respaldo y restauración de la base de datos de OpenNCCM."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, DATABASE_URL, KEY_FILE
from .db import engine, get_setting

BACKUP_DIR = DATA_DIR / "system-backups"


def list_backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("opennccm-*.tar.gz"), reverse=True)


def create_backup(include_key: bool = False) -> Path:
    from . import __version__
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
    t = paramiko.Transport((host, int(get_setting("dr_remote_port", "22") or 22)))
    try:
        t.connect(username=get_setting("dr_remote_user"), password=get_setting("dr_remote_password"))
        sftp = paramiko.SFTPClient.from_transport(t)
        remote_dir = get_setting("dr_remote_path", ".") or "."
        sftp.put(str(path), f"{remote_dir.rstrip('/')}/{path.name}")
        return f"{host}:{remote_dir}"
    finally:
        t.close()


def restore_backup(archive: Path) -> str:
    """Restaura una copia (solo desde la CLI y con el servicio detenido)."""
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())
        if manifest["dialect"] != "sqlite" or engine.dialect.name != "sqlite":
            raise RuntimeError("La restauración automática solo admite SQLite; use pg_restore para PostgreSQL")
        current = Path(engine.url.database)
        engine.dispose()  # cierra conexiones abiertas antes de reemplazar el archivo
        if current.exists():
            current.rename(current.with_suffix(f".db.antes-de-restaurar-{datetime.now():%Y%m%d%H%M%S}"))
        for suffix in ("-wal", "-shm"):
            Path(str(current) + suffix).unlink(missing_ok=True)
        with open(current, "wb") as f:
            f.write(tar.extractfile("opennccm.db").read())
        msg = f"Base restaurada desde {archive.name} (creada {manifest['created']})."
        if "secret.key" in names and not KEY_FILE.exists():
            KEY_FILE.write_bytes(tar.extractfile("secret.key").read())
            KEY_FILE.chmod(0o600)
            msg += " Clave restaurada."
        return msg
