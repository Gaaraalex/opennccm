"""Restaurar una copia de recuperación en un servidor NUEVO, que ya tiene la clave que creó su instalador: con la clave
en la copia se reemplaza (la anterior se guarda aparte) y los datos se leen; sin ella, el comando avisa (código 3) de que
hay que copiar el secret.key original en vez de decir solo «restaurado»; una copia de otra base de datos se rechaza."""
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def server():
    """Carpeta de datos y clave propias, como un servidor recién instalado (install.sh crea la clave)."""
    d = tempfile.mkdtemp()
    open(os.path.join(d, "secret.key"), "wb").write(os.urandom(32))
    return dict(os.environ, NCM_DATA_DIR=d, NCM_KEY_FILE=os.path.join(d, "secret.key"),
                NCM_BACKUP_PASSPHRASE="Clave-de-copia-2026", PYTHONIOENCODING="utf-8"), d


def py(env, code, *args):
    return subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); " + code, *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def restore(env, path):
    return py(env, "from app.cli import main; sys.argv = ['x', 'restore', sys.argv[1]]; main()", path)


READ = ("from app.db import SessionLocal, Credential, select; from app.crypto import try_decrypt; "
        "db = SessionLocal(); print(try_decrypt(db.scalars(select(Credential)).first().password_enc))")

# servidor original con una credencial y dos copias: con la clave (cifrada) y sin ella
orig, orig_dir = server()
r = py(orig, "from app.db import init_db, SessionLocal, Credential; from app.crypto import encrypt; init_db(); "
             "db = SessionLocal(); db.add(Credential(name='c', username='u', password_enc=encrypt('Pa55-equipos'))); "
             "db.commit(); from app.sysbackup import create_backup; "
             "print(create_backup(include_key=True, passphrase='Clave-de-copia-2026')); print(create_backup())")
paths = r.stdout.split()
check("copias creadas en el servidor original", len(paths) == 2, r.stdout + r.stderr)
with_key, without_key = (paths + ["", ""])[:2]

# 1) copia con la clave en un servidor nuevo: la clave del instalador se reemplaza y se guarda aparte
new, new_dir = server()
installer_key = open(new["NCM_KEY_FILE"], "rb").read()
r = restore(new, with_key)
check("con la clave: restaura sin avisos", r.returncode == 0 and "Clave restaurada" in r.stdout, r.stdout + r.stderr)
check("la clave del servidor pasa a ser la de la copia",
      open(new["NCM_KEY_FILE"], "rb").read() == open(orig["NCM_KEY_FILE"], "rb").read())
saved = glob.glob(os.path.join(new_dir, "secret.key.antes-de-restaurar-*"))
check("la clave anterior se guarda como secret.key.antes-de-restaurar-FECHA",
      len(saved) == 1 and open(saved[0], "rb").read() == installer_key, saved)
check("y las contraseñas restauradas se leen", py(new, READ).stdout.strip() == "Pa55-equipos")
r = restore(new, with_key)
check("restaurarla otra vez no guarda otra copia de la clave (ya coincide)",
      r.returncode == 0 and len(glob.glob(os.path.join(new_dir, "secret.key.antes-de-restaurar-*"))) == 1,
      r.stdout + r.stderr)

# 2) copia sin la clave en un servidor nuevo: avisa claramente y sale con código 3
new2, _ = server()
r = restore(new2, without_key)
check("sin la clave: avisa que la clave del servidor no corresponde (código 3)",
      r.returncode == 3 and "Base restaurada" in r.stdout and "no corresponde a la copia" in r.stdout
      and "secret.key" in r.stdout, (r.returncode, r.stdout + r.stderr))
# ... y al copiar el secret.key original, se lee sin avisos
open(new2["NCM_KEY_FILE"], "wb").write(open(orig["NCM_KEY_FILE"], "rb").read())
r = restore(new2, without_key)
check("con el secret.key original copiado antes, restaura sin avisos", r.returncode == 0, r.stdout + r.stderr)

# 3) la copia del servidor original en el mismo servidor: sin avisos
r = restore(orig, without_key)
check("en el mismo servidor, la copia sin clave restaura sin avisos", r.returncode == 0, r.stdout + r.stderr)

# 4) una copia de PostgreSQL en un servidor con SQLite se rechaza con un motivo claro (sin tocar la base)
r = py(new, "import io, json, tarfile; from pathlib import Path; buf = io.BytesIO(); "
            "t = tarfile.open(fileobj=buf, mode='w:gz'); m = json.dumps({'dialect': 'postgresql', 'created': 'x'}).encode(); "
            "i = tarfile.TarInfo('manifest.json'); i.size = len(m); t.addfile(i, io.BytesIO(m)); t.close(); "
            "p = Path(sys.argv[1]); p.write_bytes(buf.getvalue()); "
            "from app.sysbackup import restore_backup\ntry:\n    restore_backup(p)\nexcept RuntimeError as e:\n    print(e)",
       os.path.join(new_dir, "pg.tar.gz"))
check("copia de PostgreSQL en un servidor SQLite: se rechaza con el motivo",
      "La copia es de PostgreSQL y este servidor usa SQLite" in r.stdout, r.stdout + r.stderr)
check("y la base del servidor sigue intacta", py(new, READ).stdout.strip() == "Pa55-equipos")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
