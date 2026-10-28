#!/usr/bin/env bash
# Reversión en un MikroTik real: RouterOS CHR (la edición gratuita para máquinas virtuales) en QEMU con KVM.
# Uso en la CI (Ubuntu):  bash tests/mikrotik-real-test.sh VERSIÓN [python]   (VERSIÓN: 7.25, 7.23.8, 6.49.23…)
set -euo pipefail
VER=$1
PY=${2:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=2290
cleanup() { [[ -f $WORK/qemu.pid ]] && kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true; }
trap cleanup EXIT

# KVM para el usuario de la CI (sin él, RouterOS tarda minutos en arrancar)
echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm.rules >/dev/null
sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm
sudo apt-get update -qq && sudo apt-get install -y -qq qemu-system-x86 unzip >/dev/null

curl -fsSL -o "$WORK/chr.zip" "https://download.mikrotik.com/routeros/$VER/chr-$VER.img.zip"
unzip -q -o "$WORK/chr.zip" -d "$WORK"
IMG=$(ls "$WORK"/chr-*.img)
qemu-system-x86_64 -enable-kvm -m 256 -smp 1 -drive "file=$IMG,format=raw,if=ide" \
  -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device e1000,netdev=n0 \
  -display none -serial "file:$WORK/console.log" -daemonize -pidfile "$WORK/qemu.pid"

echo "== Esperando a RouterOS $VER"
for _ in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then break; fi
  sleep 3
done
sleep 5
"$PY" tests/chr_init.py $PORT "Clave-CHR-2026"

export NCM_DATA_DIR=$WORK/data NCM_KEY_FILE=$WORK/data/secret.key PYTHONUNBUFFERED=1
mkdir -p "$NCM_DATA_DIR"
cd "$ROOT"
"$PY" tests/mikrotik_real.py $PORT "Clave-CHR-2026" "$VER" || { tail -n 40 "$WORK/console.log" || true; exit 1; }
echo "== Firmware"
"$PY" tests/mikrotik_fw.py $PORT "Clave-CHR-2026" "$VER" || { tail -n 40 "$WORK/console.log" || true; exit 1; }
