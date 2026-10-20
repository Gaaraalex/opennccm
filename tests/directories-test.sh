#!/usr/bin/env bash
# Prueba del inicio de sesión contra directorios REALES: un controlador de dominio Samba AD (LDAPS y StartTLS, grupos
# anidados) y Keycloak como proveedor OpenID Connect (el mismo protocolo que Microsoft Entra ID).
# Uso en la CI (Ubuntu, con sudo y Docker):  bash tests/directories-test.sh [python]
set -euo pipefail
PY=${1:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
export DEBIAN_FRONTEND=noninteractive

echo "== Samba AD: instalar y crear el dominio EMPRESA.LOCAL"
sudo apt-get -qq update >/dev/null
sudo apt-get -qq install -y samba samba-ad-dc ldap-utils >/dev/null 2>&1 || sudo apt-get -qq install -y samba ldap-utils >/dev/null
sudo systemctl stop smbd nmbd winbind samba-ad-dc 2>/dev/null || true
sudo rm -f /etc/samba/smb.conf
sudo samba-tool domain provision --realm=EMPRESA.LOCAL --domain=EMPRESA --server-role=dc --dns-backend=NONE \
  --use-rfc2307 --adminpass='Admin-Dominio-2026' >/dev/null
sudo samba -D
for _ in $(seq 1 30); do (echo > /dev/tcp/127.0.0.1/636) 2>/dev/null && break; sleep 1; done
ST="sudo samba-tool"
$ST user create svc-nccm 'Servicio-Nccm-2026' >/dev/null
$ST user create ana.admin 'Clave-Ana-2026' --mail-address=ana@empresa.local >/dev/null
$ST user create oscar 'Clave-Oscar-2026' >/dev/null
$ST user create nico 'Clave-Nico-2026' >/dev/null      # admin por grupo ANIDADO
$ST user create visita 'Clave-Visita-2026' >/dev/null  # sin grupos con acceso
$ST group add NCCM-Admins >/dev/null
$ST group add NCCM-Operadores >/dev/null
$ST group add Redes >/dev/null
$ST group addmembers NCCM-Admins ana.admin,Redes >/dev/null
$ST group addmembers Redes nico >/dev/null
$ST group addmembers NCCM-Operadores oscar >/dev/null
echo "OK dominio con usuarios y grupos"

echo "== Keycloak: proveedor OpenID Connect"
docker run -d --name kc -p 8080:8080 -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:26.0 start-dev >/dev/null
for _ in $(seq 1 120); do curl -fs http://127.0.0.1:8080/realms/master >/dev/null 2>&1 && break; sleep 2; done
"$PY" tests/kc_setup.py

echo "== OpenNCCM"
WORK=$(mktemp -d)
export NCM_DATA_DIR=$WORK/data NCM_KEY_FILE=$WORK/data/secret.key NCM_SYSLOG_PORT=5515
mkdir -p "$NCM_DATA_DIR"
"$PY" -c "import sys; sys.path.insert(0,'.'); from app.cli import main; sys.argv=['x','create-admin','admin','Admin12345']; main()"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$WORK/app.log" 2>&1 & APP=$!
trap 'kill $APP 2>/dev/null || true; docker rm -f kc >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1; done
if ! "$PY" tests/e2e_directories.py; then
  echo "--- app.log"; tail -n 80 "$WORK/app.log"; exit 1
fi
