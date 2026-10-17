#!/usr/bin/env bash
# Comprobaciones sobre una instalación real (Ubuntu + nginx + systemd), tras install.sh.
#   bash tests/install-checks.sh tls       certificado propio, cookie segura y consola por nginx (WebSocket)
#   bash tests/install-checks.sh webupdate el botón «Actualizar» dispara el servicio de systemd
set -euo pipefail
B=https://127.0.0.1
PASS=${ADMIN_PASS:-Admin12345}
JAR=$(mktemp)
fail() { echo "FALLO: $*"; exit 1; }
login() { curl -sk -c "$JAR" -b "$JAR" -o /dev/null --data-urlencode "username=admin" --data-urlencode "password=$PASS" $B/login; }

case ${1:-tls} in
tls)
  echo "== Certificado emitido por la CA propia de OpenNCCM (con restricción de nombres)"
  test -f /etc/opennccm/tls/ca.crt || fail "no existe /etc/opennccm/tls/ca.crt"
  issuer=$(echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null | openssl x509 -noout -issuer)
  [[ $issuer == *"OpenNCCM CA"* ]] || fail "emisor inesperado: $issuer"
  sudo openssl x509 -in /etc/opennccm/tls/ca.crt -noout -text | grep -q "Name Constraints" || fail "la CA no tiene restricción de nombres"
  curl -fsS --cacert /etc/opennccm/tls/ca.crt $B/healthz >/dev/null || fail "el certificado no valida con la CA (como en un PC que la instaló)"
  curl -fsk $B/ca.crt | cmp - /etc/opennccm/tls/ca.crt || fail "/ca.crt no entrega la CA"
  test "$(sudo stat -c %a /etc/opennccm/tls/ca.key)" = 600 || fail "permisos de ca.key"
  echo "OK certificado"

  echo "== Cookie de sesión solo por HTTPS y cabeceras de seguridad"
  hdr=$(curl -sk -D - -o /dev/null --data-urlencode "username=admin" --data-urlencode "password=$PASS" $B/login)
  echo "$hdr" | grep -i '^set-cookie: opennccm=' | grep -qi 'secure' || fail "la cookie no es Secure"
  echo "$hdr" | grep -qi '^content-security-policy:' || fail "falta Content-Security-Policy"
  echo "$hdr" | grep -qi '^strict-transport-security:' || fail "falta HSTS"
  echo "OK cookie y cabeceras"

  echo "== Consola SSH web a través de nginx (WebSocket)"
  login
  curl -sk -c "$JAR" -b "$JAR" -o /dev/null -d "name=prueba" -d "username=admin" -d "password=x" $B/credentials
  curl -sk -c "$JAR" -b "$JAR" -o /dev/null -d "name=consola-ci" -d "host=127.0.0.1" -d "device_type=linux" \
    -d "protocol=ssh" -d "port=22" -d "credential_id=1" -d "enabled=on" $B/devices/new
  dev=$(curl -sk -b "$JAR" $B/api/devices | python3 -c 'import json,sys; print([d["id"] for d in json.load(sys.stdin) if d["name"]=="consola-ci"][0])')
  cookie=$(awk '$6=="opennccm"{print $7}' "$JAR")
  sudo /opt/opennccm/.venv/bin/python - "$dev" "$cookie" <<'PY'
import ssl, sys
from websockets.sync.client import connect
dev, cookie = sys.argv[1], sys.argv[2]
ctx = ssl.create_default_context(cafile="/etc/opennccm/tls/ca.crt")
with connect(f"wss://127.0.0.1/devices/{dev}/console/ws?cols=80&rows=24", ssl=ctx,
             additional_headers={"Cookie": f"opennccm={cookie}", "Origin": "https://127.0.0.1"}) as ws:
    out = ""
    while "Usuario:" not in out:
        out += ws.recv(timeout=15)
print("OK consola por nginx:", out.strip().splitlines()[0])
PY
  ;;
webupdate)
  echo "== Actualizar desde la web (opennccm-webupdate.path → opennccm update)"
  systemctl is-enabled opennccm-webupdate.path >/dev/null || fail "opennccm-webupdate.path no está habilitado"
  sudo rm -f /var/lib/opennccm/update.log
  login
  loc=$(curl -sk -b "$JAR" -o /dev/null -w '%{redirect_url}' -X POST $B/system/update)
  [[ $loc == */system/updating ]] || fail "no se abrió la página de progreso ($loc)"
  for _ in $(seq 1 180); do
    sudo test -f /var/lib/opennccm/update.log && sudo grep -q "FIN=" /var/lib/opennccm/update.log && break
    sleep 2
  done
  sudo cat /var/lib/opennccm/update.log || fail "el servicio de actualización no se ejecutó"
  sudo grep -q "FIN=" /var/lib/opennccm/update.log || fail "la actualización no terminó"
  sudo test ! -f /var/lib/opennccm/update.request || fail "la solicitud quedó pendiente"
  for _ in $(seq 1 60); do curl -fsk $B/healthz >/dev/null 2>&1 && break; sleep 2; done
  curl -fsk $B/healthz || fail "OpenNCCM no responde tras la actualización"
  echo
  echo "OK actualización desde la web"
  ;;
esac
