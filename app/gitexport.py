"""Copia de los respaldos en un repositorio Git (GitHub, GitLab, Gitea…): cada versión nueva es un commit.

El repositorio se clona en DATA_DIR/git-export. Cada equipo es un archivo «<grupo>/<equipo>.cfg» con su última
configuración en ejecución. Por omisión se ocultan contraseñas y claves (lo mejor posible: revise el repositorio antes de
hacerlo público). La autenticación HTTPS va en una cabecera de la orden (nunca en la URL ni en el disco); con SSH se usan
las claves del usuario del servicio.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess
import threading

from .config import DATA_DIR
from .i18n import _, _f

log = logging.getLogger("opennccm.git")
REPO = DATA_DIR / "git-export"
_lock = threading.Lock()

# contraseñas, claves y comunidades (lo que va detrás de la palabra clave se sustituye por «<oculto>»)
MASKS = [
    (re.compile(r"(?im)^(\s*(?:enable\s+)?(?:secret|password)\s+(?:\d+\s+)?)\S+"), r"\1<oculto>"),
    (re.compile(r"(?im)^(\s*username\s+\S+.*?\b(?:secret|password)\s+(?:\d+\s+)?)\S+"), r"\1<oculto>"),
    (re.compile(r"(?im)^(\s*snmp-server\s+community\s+)\S+"), r"\1<oculto>"),
    (re.compile(r"(?im)\b((?:key|server-key|pre-shared-key|authentication-key)\s+(?:\d+\s+)?)(?!<)\S+"), r"\1<oculto>"),
    (re.compile(r'(?i)\b(encrypted-password|secret|authentication-key|ascii-text|psk)\s+"[^"]*"'), r'\1 "<oculto>"'),
    (re.compile(r'(?i)\b((?:[\w-]*password|secret|[\w-]*pre-shared-key|authentication-key|psk|passphrase)=)("[^"]*"|\S+)'),
     r"\1<oculto>"),
    (re.compile(r"(?im)^(\s*set\s+(?:password|passwd|psksecret|secret|key)\s+(?:ENC\s+)?)\S+"), r"\1<oculto>"),
    (re.compile(r"(?im)\b(password\s+(?:cipher|irreversible-cipher|simple)\s+)\S+"), r"\1<oculto>"),
    (re.compile(r"(?im)^(\s*(?:local-user|user)\s+\S+\s+password\s+\S+\s+)\S+"), r"\1<oculto>"),
]


def mask(text: str) -> str:
    for pat, repl in MASKS:
        text = pat.sub(repl, text)
    return text


def _settings():
    from .db import get_setting
    return {"enabled": get_setting("git_enabled", "0") == "1", "url": get_setting("git_url", "").strip(),
            "branch": get_setting("git_branch", "").strip() or "main", "user": get_setting("git_user", "").strip(),
            "token": get_setting("git_token", ""), "mask": get_setting("git_mask", "1") == "1"}


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name or "").strip("._") or "sin-nombre"


class GitError(Exception):
    pass


def _git(cfg, *args, cwd=REPO, timeout=120) -> str:
    cmd = ["git"]
    if cfg["token"] and cfg["url"].lower().startswith("https://"):
        tok = base64.b64encode(f"{cfg['user'] or 'git'}:{cfg['token']}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {tok}"]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new")
    try:
        r = subprocess.run(cmd + list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise GitError(_("Git no está instalado en el servidor.")) from e
    except subprocess.TimeoutExpired as e:
        raise GitError(_f("Git no respondió a tiempo ({0}).", " ".join(args[:2]))) from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace(cfg["token"], "***") if cfg["token"] else (r.stderr or r.stdout).strip()
        raise GitError(f"git {' '.join(args[:2])}: {msg[-500:]}")
    return r.stdout


def _prepare(cfg) -> None:
    """Repositorio local apuntando al remoto y al día con su rama (si ya existe allí)."""
    if not (REPO / ".git").exists():
        if REPO.exists():
            shutil.rmtree(REPO)
        REPO.mkdir(parents=True)
        _git(cfg, "init", "-q")
        _git(cfg, "remote", "add", "origin", cfg["url"])
    else:
        _git(cfg, "remote", "set-url", "origin", cfg["url"])
    remote_has = bool(_git(cfg, "ls-remote", "--heads", "origin", cfg["branch"]).strip())
    if remote_has:
        _git(cfg, "fetch", "-q", "origin", cfg["branch"])
        _git(cfg, "checkout", "-q", "-B", cfg["branch"], f"origin/{cfg['branch']}")
    else:
        _git(cfg, "checkout", "-q", "-B", cfg["branch"])


def _write(db, dev, cfg) -> bool:
    from .crypto import try_decrypt
    from .services import latest_version
    v = latest_version(db, dev.id)
    if v is None:
        return False
    text = try_decrypt(v.content_enc)
    if text is None:
        return False
    folder = REPO / _safe(dev.group.name if dev.group else "sin-grupo")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{_safe(dev.name)}.cfg").write_text(mask(text) if cfg["mask"] else text, encoding="utf-8", newline="\n")
    return True


def _commit_push(cfg, message: str) -> bool:
    _git(cfg, "add", "-A")
    if not _git(cfg, "status", "--porcelain").strip():
        return False
    _git(cfg, "-c", "user.name=OpenNCCM", "-c", "user.email=opennccm@localhost", "commit", "-q", "-m", message)
    _git(cfg, "push", "-q", "origin", f"HEAD:{cfg['branch']}", timeout=180)
    return True


def export(device_ids: list[int] | None = None, message: str = "") -> tuple[int, bool]:
    """Escribe la última versión de los equipos (todos si device_ids es None) y sube un commit. (archivos, subido)"""
    cfg = _settings()
    if not cfg["url"]:
        raise GitError(_("Falta la dirección del repositorio Git."))
    from .db import Device, SessionLocal, select
    with _lock, SessionLocal() as db:
        _prepare(cfg)
        q = select(Device) if device_ids is None else select(Device).where(Device.id.in_(device_ids))
        n = sum(_write(db, d, cfg) for d in db.scalars(q))
        pushed = _commit_push(cfg, message or _f("OpenNCCM: {0} equipos", n))
    return n, pushed


def on_new_version(dev_id: int, message: str) -> None:
    """Tras un respaldo con cambios (se llama en segundo plano). Un fallo se registra como alarma, sin más."""
    cfg = _settings()
    if not (cfg["enabled"] and cfg["url"]):
        return
    try:
        export([dev_id], message)
    except Exception as e:  # noqa: BLE001
        from .services import add_event
        log.warning("copia en Git: %s", e)
        add_event("warning", "Copia en Git fallida", str(e)[:1500], dev_id)
