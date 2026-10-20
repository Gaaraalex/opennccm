#!/usr/bin/env bash
# Prueba real del cambio de zona horaria desde la interfaz (servicio sin root + regla de polkit).
# Uso (con OpenNCCM instalado):  bash tests/time-test.sh
set -euo pipefail
B=http://127.0.0.1:8000
PASS=${ADMIN_PASS:-Admin12345}
JAR=$(mktemp)
ORIG=$(timedatectl show -p Timezone --value)
NEW=America/Bogota; [[ $ORIG == "$NEW" ]] && NEW=Europe/Madrid

login() { curl -s -c "$JAR" -b "$JAR" -o /dev/null --data-urlencode "username=admin" --data-urlencode "password=$PASS" $B/login; }
set_tz() {
  local pid; pid=$(systemctl show opennccm -p MainPID --value)
  login
  curl -s -c "$JAR" -b "$JAR" -o /tmp/tz.html -d "action=timezone" --data-urlencode "timezone=$1" $B/system/time
  grep -q "Aplicando la zona horaria" /tmp/tz.html || { echo "No se mostró la página de espera"; return 1; }
  for _ in $(seq 1 40); do  # esperar a que el servicio se reinicie (otro PID) y vuelva a responder
    sleep 1
    [[ $(systemctl show opennccm -p MainPID --value) != "$pid" ]] || continue
    [[ $(timedatectl show -p Timezone --value) == "$1" ]] && curl -fsS $B/healthz >/dev/null 2>&1 && return 0
  done
  return 1
}

echo "== Zona actual: $ORIG -> cambiando a $NEW desde la interfaz"
set_tz "$NEW" || { echo "La zona no cambió"; journalctl -u opennccm -n 30 --no-pager; exit 1; }
login
curl -s -c "$JAR" -b "$JAR" $B/system | grep "<option selected>$NEW</option>" >/dev/null || { echo "La página no muestra la nueva zona"; exit 1; }
echo "OK zona horaria cambiada y servicio reiniciado"
echo "== Restaurando $ORIG"
set_tz "$ORIG"
echo "== NTP: desactivar y volver a activar desde la interfaz"
login
curl -s -c "$JAR" -b "$JAR" -o /dev/null -d "action=ntp" $B/system/time
sleep 1; [[ $(timedatectl show -p NTP --value) == no ]] || { echo "NTP no se desactivó"; exit 1; }
curl -s -c "$JAR" -b "$JAR" -o /dev/null -d "action=ntp" -d "ntp=on" $B/system/time
sleep 1; [[ $(timedatectl show -p NTP --value) == yes ]] || { echo "NTP no se reactivó"; exit 1; }
echo "OK NTP"
echo "TODO OK"
