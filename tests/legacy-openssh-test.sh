#!/usr/bin/env bash
# Conexión a un OpenSSH REAL configurado como un equipo antiguo (Ubiquiti EdgeSwitch, Cisco IOS 12.x):
# solo clave de host ssh-rsa y un intercambio de claves SHA-1. Requiere sudo.
# Uso (desde la raíz del repositorio):  bash tests/legacy-openssh-test.sh [python]
set -euo pipefail
PY=${1:-python3}
PORT=2230
U=ncmlegacy PW='Legacy-12345'
DIR=$(mktemp -d)
[[ -x /usr/sbin/sshd ]] || { sudo apt-get update -qq; sudo apt-get install -y -qq openssh-server; }
sudo useradd -m -s /bin/bash "$U" 2>/dev/null || true
echo "$U:$PW" | sudo chpasswd
sudo ssh-keygen -q -t rsa -b 2048 -N '' -f "$DIR/host_rsa"
sudo mkdir -p /run/sshd
status=0
for KEX in diffie-hellman-group14-sha1 diffie-hellman-group-exchange-sha1 diffie-hellman-group1-sha1; do
  if ! ssh -Q kex | grep -qx "$KEX"; then echo "-- $KEX no está disponible en este OpenSSH; se omite"; continue; fi
  cat > "$DIR/sshd_config" <<EOF
Port $PORT
ListenAddress 127.0.0.1
HostKey $DIR/host_rsa
HostKeyAlgorithms ssh-rsa
KexAlgorithms $KEX
PasswordAuthentication yes
KbdInteractiveAuthentication yes
UsePAM yes
PidFile $DIR/sshd.pid
EOF
  sudo /usr/sbin/sshd -t -f "$DIR/sshd_config"
  sudo /usr/sbin/sshd -f "$DIR/sshd_config"
  sleep 1
  echo "== Equipo antiguo: hostkey ssh-rsa, kex $KEX"
  "$PY" tests/legacy_ssh_test.py 127.0.0.1 $PORT "$U" "$PW" linux --sin-compat 2>/dev/null || status=1
  "$PY" tests/legacy_ssh_test.py 127.0.0.1 $PORT "$U" "$PW" linux || status=1
  sudo kill "$(sudo cat "$DIR/sshd.pid")"; sleep 1
done
sudo rm -rf "$DIR"
[[ $status -eq 0 ]] && echo "TODO OK" || echo "HUBO FALLOS"
exit $status
