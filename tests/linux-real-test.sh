#!/usr/bin/env bash
# Respaldo de servidores Linux REALES con el perfil «Ubuntu Server / Debian»: contenedores oficiales de Ubuntu 26.04,
# 24.04 y 22.04 y Debian 13 con OpenSSH, sudo, cortafuegos, chrony, cron y un netplan con contraseña de wifi.
# Uso en la CI (Ubuntu, con Docker):  bash tests/linux-real-test.sh [python]
set -euo pipefail
PY=${1:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
IMAGES=(ubuntu:26.04 ubuntu:24.04 ubuntu:22.04 debian:13)
PORT=2240
ARGS=()
cleanup() { for i in "${!IMAGES[@]}"; do docker rm -f "ncm-linux-$i" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT

for i in "${!IMAGES[@]}"; do
  img=${IMAGES[$i]} port=$((PORT + i)) name="ncm-linux-$i"
  echo "== $img: preparar el servidor (puerto $port)"
  docker run -d --name "$name" --cap-add NET_ADMIN -p "127.0.0.1:$port:22" "$img" sleep infinity >/dev/null
  docker exec -e DEBIAN_FRONTEND=noninteractive "$name" bash -c '
    set -e
    apt-get -qq update >/dev/null
    apt-get -qq install -y --no-install-recommends openssh-server sudo iptables nftables ufw chrony cron \
      unattended-upgrades >/dev/null 2>&1
    useradd -m -s /bin/bash ncm && echo "ncm:Clave-Linux-2026" | chpasswd
    useradd -m -s /bin/bash lector && echo "lector:Clave-Lector-2026" | chpasswd
    echo "ncm ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/ncm && chmod 440 /etc/sudoers.d/ncm
    echo "servidor-ci" > /etc/hostname
    mkdir -p /etc/netplan
    printf "network:\n  version: 2\n  ethernets:\n    eth0:\n      dhcp4: true\n  wifis:\n    wlan0:\n      access-points:\n        \"Oficina\":\n          password: \"secreto-wifi-123\"\n" > /etc/netplan/50-ci.yaml
    chmod 600 /etc/netplan/50-ci.yaml
    printf "APT::Periodic::Update-Package-Lists \"1\";\nAPT::Periodic::Unattended-Upgrade \"1\";\n" > /etc/apt/apt.conf.d/20auto-upgrades
    echo "15 3 * * * root /usr/local/bin/respaldo.sh" > /etc/cron.d/respaldo
    iptables -A INPUT -p tcp --dport 22 -j ACCEPT 2>/dev/null || true
    mkdir -p /run/sshd && /usr/sbin/sshd
  '
  ARGS+=("$img" "$port")
done
"$PY" tests/linux_real.py "${ARGS[@]}"
