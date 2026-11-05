#!/usr/bin/env bash
# Reversión en un MikroTik real: RouterOS CHR (la edición gratuita para máquinas virtuales) en QEMU con KVM.
# Uso en la CI (Ubuntu):  bash tests/mikrotik-real-test.sh VERSIÓN [python]   (VERSIÓN: 7.25, 7.23.8, 6.49.23…)
set -euo pipefail
VER=$1
PY=${2:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=2290
PORT2=2291  # segundo CHR, unido al primero por un cable virtual (ether2 de cada uno): vecino LLDP/MNDP y por RoMON
cleanup() { for f in "$WORK"/qemu*.pid; do [[ -f $f ]] && kill "$(cat "$f")" 2>/dev/null || true; done; }
trap cleanup EXIT

# KVM para el usuario de la CI (sin él, RouterOS tarda minutos en arrancar)
echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm.rules >/dev/null
sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm
sudo apt-get update -qq && sudo apt-get install -y -qq qemu-system-x86 unzip >/dev/null

curl -fsSL -o "$WORK/chr.zip" "https://download.mikrotik.com/routeros/$VER/chr-$VER.img.zip"
unzip -q -o "$WORK/chr.zip" -d "$WORK"
IMG=$(ls "$WORK"/chr-*.img)
cp "$IMG" "$WORK/chr2.img"
qemu-system-x86_64 -enable-kvm -m 256 -smp 1 -drive "file=$IMG,format=raw,if=ide" \
  -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device e1000,netdev=n0 \
  -netdev socket,id=n1,listen=127.0.0.1:2300 -device e1000,netdev=n1,mac=52:54:00:00:01:02 \
  -display none -serial "file:$WORK/console.log" -daemonize -pidfile "$WORK/qemu.pid"
qemu-system-x86_64 -enable-kvm -m 256 -smp 1 -drive "file=$WORK/chr2.img,format=raw,if=ide" \
  -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT2-:22" -device e1000,netdev=n0,mac=52:54:00:00:02:01 \
  -netdev socket,id=n1,connect=127.0.0.1:2300 -device e1000,netdev=n1,mac=52:54:00:00:02:02 \
  -display none -serial "file:$WORK/console2.log" -daemonize -pidfile "$WORK/qemu2.pid"

echo "== Esperando a RouterOS $VER"
for p in $PORT $PORT2; do
  for _ in $(seq 1 60); do
    if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then break; fi
    sleep 3
  done
done
sleep 5
"$PY" tests/chr_init.py $PORT "Clave-CHR-2026"
"$PY" tests/chr_init.py $PORT2 "Clave-CHR-2026"
export ROMON2_PORT=$PORT2

export NCM_DATA_DIR=$WORK/data NCM_KEY_FILE=$WORK/data/secret.key PYTHONUNBUFFERED=1
mkdir -p "$NCM_DATA_DIR"
cd "$ROOT"
"$PY" tests/mikrotik_real.py $PORT "Clave-CHR-2026" "$VER" || { tail -n 40 "$WORK/console.log" || true; exit 1; }
echo "== Firmware"
"$PY" tests/mikrotik_fw.py $PORT "Clave-CHR-2026" "$VER" || { tail -n 40 "$WORK/console.log" || true; exit 1; }
