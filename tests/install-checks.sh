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
  test -f /etc/ssl/opennccm/ca.crt || fail "no existe /etc/ssl/opennccm/ca.crt"
  issuer=$(echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null | openssl x509 -noout -issuer)
  [[ $issuer == *"OpenNCCM CA"* ]] || fail "emisor inesperado: $issuer"
  sudo openssl x509 -in /etc/ssl/opennccm/ca.crt -noout -text | grep -q "Name Constraints" || fail "la CA no tiene restricción de nombres"
  curl -fsS --cacert /etc/ssl/opennccm/ca.crt $B/healthz >/dev/null || fail "el certificado no valida con la CA (como en un PC que la instaló)"
  curl -fsk $B/ca.crt | cmp - /etc/ssl/opennccm/ca.crt || fail "/ca.crt no entrega la CA"
  test "$(sudo stat -c %a /etc/ssl/opennccm/ca.key)" = 600 || fail "permisos de ca.key"
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
  sleep 5  # el alta del equipo lanza su primer respaldo en segundo plano: se deja terminar
  if ! sudo /opt/opennccm/.venv/bin/python - "$dev" "$cookie" <<'PY'
import ssl, sys, time
from websockets.sync.client import connect
dev, cookie = sys.argv[1], sys.argv[2]
ctx = ssl.create_default_context(cafile="/etc/ssl/opennccm/ca.crt")
for intento in (1, 2):
    try:
        t0 = time.time()
        with connect(f"wss://127.0.0.1/devices/{dev}/console/ws?cols=80&rows=24", ssl=ctx, open_timeout=30,
                     additional_headers={"Cookie": f"opennccm={cookie}", "Origin": "https://127.0.0.1"}) as ws:
            out = ""
            while "Usuario:" not in out:
                out += ws.recv(timeout=15)
        print(f"OK consola por nginx ({time.time() - t0:.1f} s):", out.strip().splitlines()[0])
        break
    except Exception as e:  # noqa: BLE001
        print(f"intento {intento}: {e.__class__.__name__}: {e}")
        if intento == 2:
            raise
        time.sleep(5)
PY
  then
    sudo journalctl -u opennccm -n 60 --no-pager; sudo tail -n 20 /var/log/nginx/error.log
    fail "la consola no abrió por nginx"
  fi
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
  # no basta con que termine: tiene que haber hecho el trabajo (sin errores de git ni del entorno de systemd)
  ! sudo grep -qi "fatal:\|not set\|command not found" /var/lib/opennccm/update.log || fail "error del entorno al actualizar"
  sudo grep -q "Buscando actualizaciones" /var/lib/opennccm/update.log || fail "no llegó a buscar actualizaciones"
  sudo grep -qE "OpenNCCM actualizado|volviendo a .* automáticamente|ya es la versión más reciente"     /var/lib/opennccm/update.log || fail "la actualización no se completó ni volvió atrás"
  sudo test ! -f /var/lib/opennccm/update.request || fail "la solicitud quedó pendiente"
  for _ in $(seq 1 60); do curl -fsk $B/healthz >/dev/null 2>&1 && break; sleep 2; done
  curl -fsk $B/healthz || fail "OpenNCCM no responde tras la actualización"
  echo
  echo "OK actualización desde la web"
  ;;
esac
