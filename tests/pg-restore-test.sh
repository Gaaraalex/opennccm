#!/usr/bin/env bash
# «opennccm restore» con PostgreSQL real: copia (pg_dump), se borran datos y se restauran con pg_restore desde el comando.
# Uso: bash tests/pg-restore-test.sh [python]   (necesita sudo y PostgreSQL instalado, como en los ejecutores de GitHub)
set -Eeuo pipefail
PY=${1:-python3}
cd "$(dirname "$0")/.."

sudo systemctl start postgresql
sudo -u postgres psql -qc "DROP DATABASE IF EXISTS ncmtest;" -c "DROP USER IF EXISTS ncmtest;"
sudo -u postgres psql -qc "CREATE USER ncmtest WITH PASSWORD 'ncmtest';" -c "CREATE DATABASE ncmtest OWNER ncmtest;"
"$PY" -m pip install -q psycopg2-binary

tmp=$(mktemp -d)
export NCM_DATA_DIR=$tmp NCM_KEY_FILE=$tmp/secret.key NCM_BACKUP_PASSPHRASE=Clave-de-copia-2026 \
  NCM_DATABASE_URL=postgresql+psycopg2://ncmtest:ncmtest@localhost/ncmtest
cli() { "$PY" -c "import sys; sys.path.insert(0, '.'); from app.cli import main; sys.argv = ['x'] + sys.argv[1:]; main()" "$@"; }

"$PY" -c "import sys; sys.path.insert(0, '.')
from app.db import init_db, SessionLocal, Credential; from app.crypto import encrypt
init_db(); db = SessionLocal(); db.add(Credential(name='c', username='u', password_enc=encrypt('Pa55-equipos'))); db.commit()"
copy=$(cli system-backup --with-key | tail -n 1)
echo "copia: $copy"
"$PY" -c "import sys; sys.path.insert(0, '.')
from app.db import SessionLocal, Credential; db = SessionLocal(); db.query(Credential).delete(); db.commit()"
# servidor «nuevo»: otra clave, como la que crea el instalador
head -c 32 /dev/urandom > "$NCM_KEY_FILE"
out=$(cli restore "$copy")
echo "$out"
grep -q "Base restaurada" <<<"$out"
grep -q "Clave restaurada" <<<"$out"
got=$("$PY" -c "import sys; sys.path.insert(0, '.')
from app.db import SessionLocal, Credential, select; from app.crypto import decrypt
print(decrypt(SessionLocal().scalars(select(Credential)).first().password_enc))")
[[ $got == Pa55-equipos ]] || { echo "FAIL datos restaurados: '$got'"; exit 1; }
echo "OK   PostgreSQL: copia restaurada con pg_restore, clave reemplazada y contraseñas legibles"
