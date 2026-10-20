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
  grep -q "j.started !== OLD" /tmp/tz.html || { echo "La página de espera no espera al proceso nuevo"; return 1; }
  local old; old=$(curl -fsS $B/healthz | python3 -c 'import json,sys; print(json.load(sys.stdin)["started"])' || true)
  for _ in $(seq 1 80); do  # esperar a que el servicio se reinicie (otro PID) y vuelva a responder
    sleep 0.5
    if [[ -n $WEB ]]; then  # lo que vería quien pulsa «Volver a Sistema» durante el reinicio (a través de nginx)
      local page; page=$(curl -sk "$WEB/system" || true)
      grep -q "502 Bad Gateway" <<<"$page" && { echo "nginx mostró «502 Bad Gateway» durante el reinicio"; return 1; }
      grep -q "OpenNCCM se está reiniciando" <<<"$page" && SAW_PAGE=1
    fi
    [[ $(systemctl show opennccm -p MainPID --value) != "$pid" ]] || continue
    if [[ $(timedatectl show -p Timezone --value) == "$1" ]] && curl -fsS $B/healthz >/tmp/hz.json 2>/dev/null; then
      grep -q '"started":' /tmp/hz.json && ! grep -q "\"started\":$old[,}]" /tmp/hz.json \
        || { echo "healthz no indica el arranque del proceso nuevo: $(cat /tmp/hz.json)"; return 1; }
      return 0
    fi
  done
  return 1
}
# con nginx delante, durante el reinicio debe verse la página «se está reiniciando» y nunca un 502 en blanco
WEB=""; SAW_PAGE=0
[[ -f /etc/nginx/sites-enabled/opennccm ]] && WEB=https://127.0.0.1

echo "== Zona actual: $ORIG -> cambiando a $NEW desde la interfaz"
set_tz "$NEW" || { echo "La zona no cambió"; journalctl -u opennccm -n 30 --no-pager; exit 1; }
login
curl -s -c "$JAR" -b "$JAR" $B/system | grep "<option selected>$NEW</option>" >/dev/null || { echo "La página no muestra la nueva zona"; exit 1; }
echo "OK zona horaria cambiada y servicio reiniciado"
if [[ -n $WEB ]]; then
  [[ $SAW_PAGE == 1 ]] || { echo "nginx no mostró la página de reinicio mientras el servicio no respondía"; exit 1; }
  echo "OK nginx muestra «OpenNCCM se está reiniciando» en lugar de 502"
fi
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
