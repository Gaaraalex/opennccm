#!/usr/bin/env bash
# =============================================================================
#  OpenNCCM - instalador para Ubuntu Server 22.04 / 24.04
#
#  Instalación guiada (menús):
#    curl -fsSL https://raw.githubusercontent.com/Gaaraalex/opennccm/main/install.sh -o install.sh && sudo bash install.sh
#
#  Instalación desatendida (ejemplo):
#    curl -fsSL .../install.sh | sudo bash -s -- --unattended --host 10.0.0.5 --admin-pass 'Clave12345'
#
#  Ayuda:  sudo bash install.sh --help
# =============================================================================
# Todo el script está dentro de main(): bash lo lee completo antes de ejecutarlo.
# Nota: con "curl | sudo bash" sudo no pasa el teclado al instalador (lee la tubería de curl),
# por eso el asistente con menús se ejecuta descargando primero el archivo.

main() {
set -Eeuo pipefail

# ----------------------------------------------------------------- valores por defecto
REPO_URL="${OPENNCCM_REPO:-https://github.com/Gaaraalex/opennccm.git}"
APP_DIR=/opt/opennccm
ETC_DIR=/etc/opennccm
DATA_DIR=/var/lib/opennccm
SVC_USER=opennccm
LOG=/var/log/opennccm-install.log
CONF=$ETC_DIR/install.conf
MIN_DISK_MB=3000
MIN_RAM_MB=1800

UNATTENDED=0
APPLY_ONLY=0          # --apply: reaplica la configuración del sistema (lo usa "opennccm update")
VERSION_REQ=""        # tag a instalar (vacío = última versión publicada)
CHANNEL="stable"      # stable = releases (tags v*), main = rama principal
SOURCE_DIR=""         # instalar desde una copia local del repositorio
NCM_HOST=""
TIMEZONE=""
TLS_MODE="self-signed"   # self-signed | letsencrypt | custom | none
LE_DOMAIN=""; LE_EMAIL=""; CERT_FILE=""; KEY_FILE_TLS=""
DB_MODE="sqlite"         # sqlite | postgresql
FIREWALL="auto"          # auto | yes | no
UPGRADE_OS=1
ADMIN_USER="admin"; ADMIN_PASS=""
SMTP_HOST=""; SMTP_PORT="587"; SMTP_SEC="starttls"; SMTP_USER=""; SMTP_PASS=""; SMTP_FROM=""; ALERT_EMAILS=""
SYSLOG_PORT=514
FORCE=0
REINSTALL=0

# Reinstalación: partir de las opciones guardadas (los argumentos las pueden cambiar)
if [[ -r $CONF ]]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi

usage() {
cat <<'EOF'
Uso: sudo bash install.sh [opciones]

Sin opciones abre un asistente con menús. Opciones para instalación desatendida:
  --unattended              No hace preguntas (usa las opciones o los valores por defecto)
  --host IP|NOMBRE          Dirección con la que se accederá a OpenNCCM (por defecto: IP principal)
  --timezone ZONA           Zona horaria, p. ej. America/Mexico_City (por defecto: la actual)
  --tls MODO                self-signed (defecto) | letsencrypt | custom | none
  --domain NOMBRE --email CORREO   Para --tls letsencrypt
  --cert ARCHIVO --key ARCHIVO     Para --tls custom
  --db sqlite|postgresql    Base de datos (defecto: sqlite)
  --firewall yes|no         Configurar ufw (defecto: solo si ya está activo)
  --admin-user USUARIO      Usuario administrador inicial (defecto: admin)
  --admin-pass CLAVE        Contraseña inicial (defecto: se genera una aleatoria)
  --smtp-host H --smtp-port P --smtp-user U --smtp-pass C --smtp-from F --alert-emails a@b,c@d
  --syslog-port PUERTO      Puerto UDP del receptor syslog (defecto: 514)
  --no-upgrade              No ejecutar apt upgrade del sistema
  --version vX.Y.Z          Instalar una versión concreta
  --channel stable|main     stable = versiones publicadas (defecto); main = desarrollo
  --repo URL                Repositorio Git (defecto: el oficial)
  --source DIRECTORIO       Instalar desde una copia local del repositorio
  --force                   Continuar aunque la versión de Ubuntu no sea 22.04/24.04
  --reinstall               Volver a ejecutar la instalación sobre una existente (conserva los datos)

Si OpenNCCM ya está instalado, para actualizar use:  sudo opennccm update
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --unattended) UNATTENDED=1 ;;
    --apply) APPLY_ONLY=1; UNATTENDED=1 ;;
    --host) NCM_HOST="$2"; shift ;;
    --timezone) TIMEZONE="$2"; shift ;;
    --tls) TLS_MODE="$2"; shift ;;
    --no-nginx) TLS_MODE="none" ;;
    --domain) LE_DOMAIN="$2"; shift ;;
    --email) LE_EMAIL="$2"; shift ;;
    --cert) CERT_FILE="$2"; shift ;;
    --key) KEY_FILE_TLS="$2"; shift ;;
    --db) DB_MODE="$2"; shift ;;
    --firewall) FIREWALL="$2"; shift ;;
    --admin-user) ADMIN_USER="$2"; shift ;;
    --admin-pass) ADMIN_PASS="$2"; shift ;;
    --smtp-host) SMTP_HOST="$2"; shift ;;
    --smtp-port) SMTP_PORT="$2"; shift ;;
    --smtp-user) SMTP_USER="$2"; shift ;;
    --smtp-pass) SMTP_PASS="$2"; shift ;;
    --smtp-from) SMTP_FROM="$2"; shift ;;
    --alert-emails) ALERT_EMAILS="$2"; shift ;;
    --syslog-port) SYSLOG_PORT="$2"; shift ;;
    --no-upgrade) UPGRADE_OS=0 ;;
    --version) VERSION_REQ="$2"; shift ;;
    --channel) CHANNEL="$2"; shift ;;
    --repo) REPO_URL="$2"; shift ;;
    --source) SOURCE_DIR="$2"; shift ;;
    --force) FORCE=1 ;;
    --reinstall) REINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Opción desconocida: $1"; usage; exit 2 ;;
  esac
  shift
done

# ----------------------------------------------------------------- interfaz
# Paleta del asistente (whiptail/newt)
export NEWT_COLORS='
root=white,blue
window=black,white
border=blue,white
shadow=black,black
title=blue,white
button=white,blue
actbutton=white,cyan
compactbutton=black,white
checkbox=black,white
actcheckbox=white,blue
entry=black,lightgray
disentry=gray,lightgray
label=black,white
listbox=black,white
actlistbox=white,blue
sellistbox=white,blue
actsellistbox=white,cyan
textbox=black,white
acttextbox=white,blue
helpline=white,blue
roottext=white,blue
emptyscale=lightgray,lightgray
fullscale=cyan,blue
'
BACKTITLE="OpenNCCM · Gestión de configuración, cambios y cumplimiento de red"
TITLE="Instalación de OpenNCCM"
GAUGE_ON=0
CUR_STEP="inicio"

# "curl ... | sudo bash": sudo lee la tubería y NO pasa el teclado al instalador (los menús no responderían).
# Se detecta y se explica cómo ejecutarlo, en lugar de mostrar menús que no reciben teclas.
if [[ $UNATTENDED -eq 0 && ! -t 0 && "$(ps -o comm= -p "$PPID" 2>/dev/null | tr -d ' ')" == "sudo" ]]; then
  RAW="https://raw.githubusercontent.com/Gaaraalex/opennccm/main/install.sh"
  cat <<EOF

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  El asistente con menús necesita el teclado, pero con "curl | sudo bash"  │
  │  sudo no se lo pasa al instalador. No se ha modificado nada.               │
  └──────────────────────────────────────────────────────────────────────────┘

  Para la instalación guiada (con menús), descargue primero el instalador:

     curl -fsSL $RAW -o install.sh && sudo bash install.sh

  Para instalar sin preguntas (valores recomendados), agregue --unattended:

     curl -fsSL $RAW | sudo bash -s -- --unattended

EOF
  exit 1
fi

INTERACTIVE=0
if [[ $UNATTENDED -eq 0 ]] && { : </dev/tty; } 2>/dev/null && [[ -t 1 || -w /dev/tty ]]; then
  INTERACTIVE=1
fi

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_err=$'\e[31m'; c_b=$'\e[1m'; c_0=$'\e[0m'
say()  { echo -e "${c_b}==>${c_0} $*"; }
warn() { echo -e "${c_warn}AVISO:${c_0} $*"; }

# Diálogos: la interfaz se dibuja en /dev/tty y la respuesta sale por stderr -> capturada
ui() { whiptail --backtitle "$BACKTITLE" --ok-button "Aceptar" --cancel-button "Cancelar" "$@" 2>&1 >/dev/tty </dev/tty; }
msg() { ui --title "${2:-$TITLE}" --msgbox "$1" "${3:-14}" 74 || true; }
yesno() { whiptail --backtitle "$BACKTITLE" --title "${2:-$TITLE}" --yes-button "Sí" --no-button "No" \
  --yesno "$1" "${3:-12}" 74 >/dev/tty </dev/tty 2>&1; }
noyes() { whiptail --backtitle "$BACKTITLE" --title "${2:-$TITLE}" --yes-button "Sí" --no-button "No" --defaultno \
  --yesno "$1" "${3:-12}" 74 >/dev/tty </dev/tty 2>&1; }   # como yesno, con «No» preseleccionado

progress() {  # progress PORCENTAJE "mensaje"
  CUR_STEP="$2"
  echo "[$(date '+%F %T')] ($1%) $2" >>"$LOG"
  if [[ $GAUGE_ON -eq 1 ]]; then
    printf 'XXX\n%s\n%s\nXXX\n' "$1" "$2" >&3
  else
    printf '%s[%3s%%]%s %s\n' "$c_b" "$1" "$c_0" "$2"
  fi
}
gauge_start() {
  if [[ $INTERACTIVE -eq 1 ]]; then
    exec 3> >(whiptail --backtitle "$BACKTITLE" --title "$TITLE" --gauge "Preparando…" 9 74 0 >/dev/tty 2>&1)
    GAUGE_ON=1
  fi
}
gauge_stop() {
  if [[ $GAUGE_ON -eq 1 ]]; then exec 3>&-; GAUGE_ON=0; sleep 0.4; fi
}
run() { "$@" >>"$LOG" 2>&1; }   # ejecuta registrando la salida en el log

on_error() {
  local code=$? line=$1
  trap - ERR
  gauge_stop
  local tail_log; tail_log=$(tail -n 15 "$LOG" 2>/dev/null || true)
  echo -e "\n${c_err}ERROR${c_0} en el paso: ${c_b}${CUR_STEP}${c_0} (línea $line, código $code)"
  echo "Últimas líneas de $LOG:"; echo "$tail_log"
  if [[ $INTERACTIVE -eq 1 ]]; then
    msg "La instalación se detuvo en el paso:\n\n   $CUR_STEP\n\nÚltimas líneas del registro:\n\n$tail_log\n\nRegistro completo: $LOG" "Error" 24
  fi
  exit "$code"
}
trap 'on_error $LINENO' ERR

# ----------------------------------------------------------------- comprobaciones previas
[[ $EUID -eq 0 ]] || { echo "Ejecute como root:  sudo bash install.sh"; exit 1; }
mkdir -p "$(dirname "$LOG")"; touch "$LOG"; chmod 600 "$LOG"
echo "=== OpenNCCM install $(date) args: $* ===" >>"$LOG"

# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
  if [[ $FORCE -eq 0 ]]; then
    echo "Este instalador es para Ubuntu Server 22.04 o 24.04 (detectado: ${PRETTY_NAME:-desconocido})."
    echo "Use --force para intentarlo de todos modos."; exit 1
  fi
  warn "Sistema no soportado oficialmente: ${PRETTY_NAME:-?}"
fi
ARCH=$(uname -m)
[[ "$ARCH" == "x86_64" || "$ARCH" == "aarch64" ]] || { echo "Arquitectura no soportada: $ARCH"; exit 1; }

[[ $APPLY_ONLY -eq 1 && ! -f $CONF ]] && { echo "--apply requiere una instalación existente"; exit 1; }

# Ya instalado: ofrecer la actualización en lugar de reinstalar
if [[ -d $APP_DIR/.git && $APPLY_ONLY -eq 0 && $REINSTALL -eq 0 ]]; then
  cur_ver=$(grep -oP '__version__ = "\K[^"]+' $APP_DIR/app/__init__.py 2>/dev/null || echo "?")
  if [[ $INTERACTIVE -eq 1 ]] && command -v whiptail >/dev/null && \
     yesno "\nOpenNCCM $cur_ver ya está instalado en este servidor.\n\n¿Desea buscar e instalar actualizaciones ahora?\n\n(Para repetir la instalación completa use --reinstall.)" "OpenNCCM ya instalado" 14; then
    exec /usr/local/bin/opennccm update
  fi
  echo "OpenNCCM $cur_ver ya está instalado."
  echo "  Actualizar:               sudo opennccm update"
  echo "  Repetir la instalación:   sudo bash install.sh --reinstall"
  exit 0
fi

# whiptail es imprescindible para el asistente
if [[ $INTERACTIVE -eq 1 ]] && ! command -v whiptail >/dev/null; then
  say "Instalando whiptail para el asistente…"
  run apt-get update -qq; run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq whiptail
fi

check_preflight() {
  local problems=() notes=()
  # Conectividad (con --source el código ya está en el servidor: GitHub solo hará falta para actualizar)
  if ! timeout 10 bash -c 'exec 3<>/dev/tcp/github.com/443' 2>/dev/null; then
    if [[ -n $SOURCE_DIR ]]; then notes+=("Sin conexión a GitHub: se instala desde $SOURCE_DIR.")
    else problems+=("Sin conexión a github.com:443 (internet, DNS o proxy).")
    fi
  fi
  # Recursos
  local disk ram
  disk=$(df -Pm /opt | awk 'NR==2{print $4}')
  ram=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
  (( disk >= MIN_DISK_MB )) || problems+=("Espacio libre insuficiente en /opt: ${disk} MB (mínimo ${MIN_DISK_MB} MB).")
  (( ram >= MIN_RAM_MB )) || notes+=("Memoria: ${ram} MB. Se recomiendan al menos 2 GB.")
  # Puertos ocupados por otros servicios
  local tcp443 udpsys
  tcp443=$(ss -ltnpH 'sport = :443' 2>/dev/null || true)
  udpsys=$(ss -lunpH "sport = :$SYSLOG_PORT" 2>/dev/null || true)
  if [[ $TLS_MODE != none && -n $tcp443 && $tcp443 != *nginx* ]]; then
    problems+=("El puerto TCP 443 está ocupado por otro servicio: $(grep -oP 'users:\(\("\K[^"]+' <<<"$tcp443" | head -n1).")
  fi
  if [[ -n $udpsys && $udpsys != *python* && $udpsys != *uvicorn* ]]; then
    notes+=("El puerto UDP $SYSLOG_PORT ya está en uso (¿rsyslog con recepción remota?). La detección en tiempo real no funcionará hasta liberarlo.")
  fi
  # Bloqueo de apt
  if command -v fuser >/dev/null && fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; then
    notes+=("Otro proceso está usando apt; se esperará a que termine.")
  fi
  PRE_PROBLEMS=("${problems[@]+"${problems[@]}"}"); PRE_NOTES=("${notes[@]+"${notes[@]}"}")
}

# ----------------------------------------------------------------- asistente
detect_ips() { ip -o -4 addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print $2" "a[1]}'; }

ask_host() {
  local items=() first="" dev ipaddr
  while read -r dev ipaddr; do
    [[ -z "$ipaddr" ]] && continue
    [[ -z $first ]] && first=$ipaddr
    items+=("$ipaddr" "interfaz $dev" "$([[ $ipaddr == "${NCM_HOST:-$first}" ]] && echo ON || echo OFF)")
  done < <(detect_ips)
  items+=("otro" "Escribir un nombre DNS u otra dirección" OFF)
  local choice
  choice=$(ui --title "Dirección del servidor" --radiolist \
    "\n¿Con qué dirección accederán los usuarios a OpenNCCM?\n\nSe usa para el certificado HTTPS y en los mensajes. Muévase con las flechas, marque con ESPACIO y confirme con ENTER." \
    20 74 8 "${items[@]}") || return 1
  if [[ $choice == "otro" ]]; then
    choice=$(ui --title "Dirección del servidor" --inputbox "\nEscriba el nombre DNS o la IP (p. ej. opennccm.miempresa.local):" 10 74 "${NCM_HOST:-$(hostname -f)}") || return 1
  fi
  NCM_HOST=$choice
}

ask_timezone() {
  local cur region city regions=() cities=()
  cur=$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
  if yesno "\nZona horaria actual del servidor:\n\n        $cur\n\nLas fechas de los cambios y las tareas programadas usan esta hora.\n¿Es correcta?" "Zona horaria" 14; then
    TIMEZONE=$cur; return 0
  fi
  while read -r region; do regions+=("$region" ""); done < <(timedatectl list-timezones | cut -d/ -f1 | sort -u | grep -v '^UTC$\|^Etc$')
  region=$(ui --title "Zona horaria" --menu "\nElija la región:" 20 60 11 "${regions[@]}") || return 1
  while read -r city; do cities+=("$city" ""); done < <(timedatectl list-timezones | grep "^$region/" | cut -d/ -f2-)
  city=$(ui --title "Zona horaria · $region" --menu "\nElija la ciudad:" 22 60 14 "${cities[@]}") || return 1
  TIMEZONE="$region/$city"
}

ask_tls() {
  local choice
  choice=$(ui --title "Acceso HTTPS" --radiolist "\n¿Cómo se protegerá el acceso web?" 17 74 4 \
    "self-signed" "Certificado autofirmado (recomendado en redes internas)" ON \
    "letsencrypt" "Let's Encrypt (requiere DNS público y puerto 80 abierto)" OFF \
    "custom" "Certificado propio de su empresa (archivos .crt y .key)" OFF \
    "none" "Sin HTTPS: puerto 8000 (solo laboratorio)" OFF) || return 1
  TLS_MODE=$choice
  if [[ $TLS_MODE == letsencrypt ]]; then
    LE_DOMAIN=$(ui --title "Let's Encrypt" --inputbox "\nNombre DNS público del servidor:" 10 74 "${LE_DOMAIN:-$NCM_HOST}") || return 1
    LE_EMAIL=$(ui --title "Let's Encrypt" --inputbox "\nCorreo para avisos de vencimiento.\nAl continuar acepta los términos de servicio de Let's Encrypt:" 11 74 "$LE_EMAIL") || return 1
  elif [[ $TLS_MODE == custom ]]; then
    CERT_FILE=$(ui --title "Certificado propio" --inputbox "\nRuta del certificado (.crt/.pem, con la cadena intermedia):" 10 74 "${CERT_FILE:-/root/opennccm.crt}") || return 1
    KEY_FILE_TLS=$(ui --title "Certificado propio" --inputbox "\nRuta de la clave privada (.key):" 10 74 "${KEY_FILE_TLS:-/root/opennccm.key}") || return 1
    [[ -r $CERT_FILE && -r $KEY_FILE_TLS ]] || { msg "No se encuentran los archivos indicados.\nCópielos al servidor y vuelva a intentarlo." "Certificado propio"; return 1; }
  fi
}

ask_db() {
  DB_MODE=$(ui --title "Base de datos" --radiolist "\n¿Qué base de datos usará OpenNCCM?" 14 74 2 \
    "sqlite" "SQLite: integrada, sin mantenimiento (hasta ~2 000 equipos)" ON \
    "postgresql" "PostgreSQL: para redes grandes (se instala y configura sola)" OFF) || return 1
}

ask_firewall() {
  if yesno "\n¿Configurar el cortafuegos (ufw)?\n\nSe permitirán SSH, HTTPS y syslog (UDP $SYSLOG_PORT) y se activará ufw.\nElija «No» si el servidor ya tiene otro cortafuegos." "Cortafuegos" 13; then
    FIREWALL=yes; else FIREWALL=no; fi
}

ask_admin() {
  ADMIN_USER=$(ui --title "Administrador" --inputbox "\nNombre del usuario administrador de OpenNCCM:" 10 74 "${ADMIN_USER:-admin}") || return 1
  local mode
  mode=$(ui --title "Contraseña del administrador" --radiolist "\n¿Cómo se define la contraseña?" 13 74 2 \
    "generar" "Generar una contraseña segura (se muestra al final)" ON \
    "escribir" "Escribirla ahora" OFF) || return 1
  if [[ $mode == escribir ]]; then
    local p1 p2
    while true; do
      p1=$(ui --title "Contraseña del administrador" --passwordbox "\nEscriba la contraseña (mínimo 8 caracteres):" 10 74) || return 1
      p2=$(ui --title "Contraseña del administrador" --passwordbox "\nRepita la contraseña:" 10 74) || return 1
      if [[ ${#p1} -lt 8 ]]; then msg "La contraseña debe tener al menos 8 caracteres." "Contraseña"
      elif [[ "$p1" != "$p2" ]]; then msg "Las contraseñas no coinciden." "Contraseña"
      else ADMIN_PASS=$p1; break; fi
    done
  else
    ADMIN_PASS=""
  fi
}

ask_smtp() {
  noyes "\n¿Configurar ahora el correo para alertas e informes?\n\nPuede hacerlo más tarde en Ajustes dentro de OpenNCCM." "Notificaciones por correo" 12 || return 0
  SMTP_HOST=$(ui --title "Correo" --inputbox "\nServidor SMTP:" 10 74 "$SMTP_HOST") || return 1
  SMTP_SEC=$(ui --title "Correo" --radiolist "\nSeguridad de la conexión:" 13 74 3 \
    "starttls" "STARTTLS (puerto 587)" ON "ssl" "SSL/TLS (puerto 465)" OFF "none" "Sin cifrado (puerto 25)" OFF) || return 1
  case $SMTP_SEC in starttls) SMTP_PORT=587;; ssl) SMTP_PORT=465;; none) SMTP_PORT=25;; esac
  SMTP_PORT=$(ui --title "Correo" --inputbox "\nPuerto:" 10 74 "$SMTP_PORT") || return 1
  SMTP_USER=$(ui --title "Correo" --inputbox "\nUsuario (vacío si no requiere autenticación):" 10 74 "$SMTP_USER") || return 1
  if [[ -n $SMTP_USER ]]; then SMTP_PASS=$(ui --title "Correo" --passwordbox "\nContraseña SMTP:" 10 74) || return 1; fi
  SMTP_FROM=$(ui --title "Correo" --inputbox "\nRemitente:" 10 74 "${SMTP_FROM:-opennccm@${NCM_HOST}}") || return 1
  ALERT_EMAILS=$(ui --title "Correo" --inputbox "\nDestinatarios de las alertas (separados por coma):" 10 74 "$ALERT_EMAILS") || return 1
}

wizard() {
  msg "\nBienvenido al instalador de OpenNCCM.\n\nEste asistente:\n  •  comprobará que el servidor cumpla los requisitos,\n  •  actualizará Ubuntu e instalará lo necesario,\n  •  le hará unas pocas preguntas,\n  •  y dejará OpenNCCM funcionando con HTTPS.\n\nUse las flechas y TAB para moverse, ESPACIO para marcar y ENTER para aceptar." "Bienvenido" 19
  local report="" p
  for p in "${PRE_PROBLEMS[@]+"${PRE_PROBLEMS[@]}"}"; do report+="  ✗  $p\n"; done
  for p in "${PRE_NOTES[@]+"${PRE_NOTES[@]}"}"; do report+="  !  $p\n"; done
  if [[ ${#PRE_PROBLEMS[@]} -gt 0 ]]; then
    msg "Se encontraron problemas que impiden instalar:\n\n$report\nCorríjalos y vuelva a ejecutar el instalador." "Comprobación del servidor" 18
    exit 1
  fi
  msg "Comprobación del servidor: correcta.\n\n  ✓  ${PRETTY_NAME}\n  ✓  Arquitectura $ARCH\n  ✓  Conexión a internet\n  ✓  Espacio en disco y memoria\n${report:+\n$report}" "Comprobación del servidor" 16
  if yesno "\n¿Actualizar Ubuntu ahora (apt upgrade)?\n\nRecomendado en servidores nuevos. Puede tardar varios minutos." "Actualizaciones del sistema" 12; then
    UPGRADE_OS=1; else UPGRADE_OS=0; fi
  ask_host && ask_timezone && ask_tls && ask_db && ask_firewall && ask_admin && ask_smtp || { msg "Instalación cancelada." "Cancelado" 8; exit 1; }
  local fw_txt; [[ $FIREWALL == yes ]] && fw_txt="sí" || fw_txt="no"
  yesno "Resumen de la instalación:\n\n  Dirección           $NCM_HOST\n  Zona horaria        $TIMEZONE\n  Acceso web          $TLS_MODE\n  Base de datos       $DB_MODE\n  Cortafuegos         $fw_txt\n  Administrador       $ADMIN_USER\n  Correo              ${SMTP_HOST:-(más tarde)}\n  Actualizar Ubuntu   $([[ $UPGRADE_OS -eq 1 ]] && echo sí || echo no)\n  Versión             ${VERSION_REQ:-la última publicada}\n\n¿Comenzar la instalación?" "Confirmar" 22 || { msg "Instalación cancelada." "Cancelado" 8; exit 1; }
}

# ----------------------------------------------------------------- pasos de instalación
apt_wait() {
  command -v fuser >/dev/null || return 0
  local i=0
  while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 && (( i++ < 150 )); do sleep 2; done
}

install_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt_wait
  progress 5 "Actualizando la lista de paquetes de Ubuntu…"
  run apt-get update
  if [[ $UPGRADE_OS -eq 1 ]]; then
    progress 10 "Actualizando Ubuntu (puede tardar varios minutos)…"
    run apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade
  fi
  local pkgs=(git curl ca-certificates openssl rsync python3 python3-venv python3-pip whiptail sqlite3 psmisc)
  [[ $TLS_MODE != none ]] && pkgs+=(nginx)
  [[ $TLS_MODE == letsencrypt ]] && pkgs+=(certbot python3-certbot-nginx)
  [[ $DB_MODE == postgresql ]] && pkgs+=(postgresql postgresql-client)
  [[ $FIREWALL == yes ]] && pkgs+=(ufw)
  local missing=() p
  for p in "${pkgs[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if [[ ${#missing[@]} -gt 0 ]]; then
    progress 20 "Instalando dependencias: ${missing[*]}"
    run apt-get install -y -o Dpkg::Options::=--force-confold "${missing[@]}"
  else
    progress 20 "Dependencias del sistema: todas presentes."
  fi
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "Se requiere Python 3.10 o superior"; return 1; }
}

fetch_code() {
  progress 30 "Descargando OpenNCCM…"
  if [[ -n $SOURCE_DIR && ! -d $SOURCE_DIR/.git ]]; then
    # paquete .tar.gz de una versión publicada (sin historial git): se copia y se crea un repositorio
    # local apuntando a GitHub, para que "opennccm update" funcione cuando haya conexión
    if [[ ! -d $APP_DIR/.git && -d $APP_DIR/app ]]; then
      mv "$APP_DIR" "${APP_DIR}.pre-git-$(date +%Y%m%d%H%M%S)"
    fi
    mkdir -p "$APP_DIR"
    run rsync -a --no-owner --no-group --delete --exclude .venv --exclude .git "$SOURCE_DIR/" "$APP_DIR/"
    local ver; ver=$(grep -oP '__version__ = "\K[^"]+' "$APP_DIR/app/__init__.py")
    if [[ ! -d $APP_DIR/.git ]]; then run git -C "$APP_DIR" init -q -b main; fi
    run git -C "$APP_DIR" -c user.name=opennccm -c user.email=opennccm@localhost add -A
    run git -C "$APP_DIR" -c user.name=opennccm -c user.email=opennccm@localhost commit -q -m "OpenNCCM $ver (paquete local)" --allow-empty
    git -C "$APP_DIR" remote get-url origin >/dev/null 2>&1 || run git -C "$APP_DIR" remote add origin "$REPO_URL"
    run git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    SOURCE_DIR=""   # en install.conf queda el repositorio oficial
    return 0
  fi
  if [[ ! -d $APP_DIR/.git ]]; then
    if [[ -d $APP_DIR/app ]]; then   # instalación antigua sin git: se conserva una copia
      mv "$APP_DIR" "${APP_DIR}.pre-git-$(date +%Y%m%d%H%M%S)"
    fi
    local src=${SOURCE_DIR:-$REPO_URL}
    run git clone --quiet "$src" "$APP_DIR"
  fi
  git config --global --get-all safe.directory 2>/dev/null | grep -qx "$APP_DIR" || \
    git config --global --add safe.directory "$APP_DIR"
  if [[ -n $SOURCE_DIR ]]; then
    git config --global --get-all safe.directory | grep -qx "$SOURCE_DIR" || git config --global --add safe.directory "$SOURCE_DIR"
    run git -C "$APP_DIR" remote set-url origin "$SOURCE_DIR"
  else
    run git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  fi
  run git -C "$APP_DIR" fetch --quiet --tags --force origin
  local target
  if [[ -n $SOURCE_DIR && -z $VERSION_REQ ]]; then
    # copia local (desarrollo/pruebas): exactamente el commit que tiene la copia
    run git -C "$APP_DIR" fetch --quiet origin HEAD
    target=FETCH_HEAD
  elif [[ -n $VERSION_REQ ]]; then
    target=$VERSION_REQ
  elif [[ $CHANNEL == main ]]; then
    target=origin/main
  else
    target=$(git -C "$APP_DIR" tag -l 'v*' --sort=-v:refname | head -n1)
    [[ -n $target ]] || target=origin/$(git -C "$APP_DIR" remote show origin 2>/dev/null | awk '/HEAD branch/{print $NF}')
    [[ $target != "origin/" ]] || target=HEAD
  fi
  run git -C "$APP_DIR" checkout --quiet --force "$target"
}

setup_python() {
  progress 40 "Preparando el entorno de Python (dependencias de OpenNCCM)…"
  [[ -x $APP_DIR/.venv/bin/python ]] || run python3 -m venv "$APP_DIR/.venv"
  run "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
  run "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
  [[ $DB_MODE == postgresql ]] && run "$APP_DIR/.venv/bin/pip" install --quiet psycopg2-binary
  return 0
}

setup_user_and_dirs() {
  progress 50 "Creando usuario de servicio y carpetas…"
  id -u $SVC_USER >/dev/null 2>&1 || run useradd --system --home $DATA_DIR --shell /usr/sbin/nologin $SVC_USER
  mkdir -p $ETC_DIR $DATA_DIR
  if [[ ! -f $ETC_DIR/secret.key ]]; then
    head -c 32 /dev/urandom > $ETC_DIR/secret.key
  fi
  if [[ -n $TIMEZONE && $APPLY_ONLY -eq 0 ]]; then
    run timedatectl set-timezone "$TIMEZONE" || true
  fi
}

setup_database() {
  if [[ $DB_MODE == postgresql ]] && ! grep -q '^NCM_DATABASE_URL=' $ETC_DIR/opennccm.env 2>/dev/null; then
    progress 55 "Configurando PostgreSQL…"
    local pg_pass; pg_pass=$(openssl rand -hex 16)
    run systemctl enable --now postgresql
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='opennccm'" | grep -q 1 \
      || run sudo -u postgres psql -c "CREATE USER opennccm WITH PASSWORD '$pg_pass';"
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='opennccm'" | grep -q 1 \
      || run sudo -u postgres psql -c "CREATE DATABASE opennccm OWNER opennccm;"
    run sudo -u postgres psql -c "ALTER USER opennccm WITH PASSWORD '$pg_pass';"
    PG_URL="postgresql+psycopg2://opennccm:${pg_pass}@localhost/opennccm"
  fi
}

write_env() {
  progress 60 "Escribiendo la configuración…"
  if [[ ! -f $ETC_DIR/opennccm.env ]]; then
    cat > $ETC_DIR/opennccm.env <<EOF
NCM_DATA_DIR=$DATA_DIR
NCM_KEY_FILE=$ETC_DIR/secret.key
NCM_SYSLOG_PORT=$SYSLOG_PORT
NCM_SYSLOG_ENABLED=1
NCM_WORKERS=16
# Clave gratuita de https://nvd.nist.gov/developers/request-an-api-key (acelera el escaneo CVE)
NCM_NVD_API_KEY=
EOF
  fi
  if [[ -n ${PG_URL:-} ]]; then
    sed -i '/^NCM_DATABASE_URL=/d' $ETC_DIR/opennccm.env
    echo "NCM_DATABASE_URL=$PG_URL" >> $ETC_DIR/opennccm.env
  fi
  # opciones de instalación (las reutilizan las actualizaciones)
  cat > $CONF <<EOF
# Generado por install.sh - opciones de la instalación de OpenNCCM
REPO_URL='${SOURCE_DIR:-$REPO_URL}'
CHANNEL='$CHANNEL'
NCM_HOST='$NCM_HOST'
TLS_MODE='$TLS_MODE'
LE_DOMAIN='$LE_DOMAIN'
LE_EMAIL='$LE_EMAIL'
CERT_FILE='$CERT_FILE'
KEY_FILE_TLS='$KEY_FILE_TLS'
DB_MODE='$DB_MODE'
FIREWALL='$FIREWALL'
SYSLOG_PORT='$SYSLOG_PORT'
EOF
  chown -R $SVC_USER:$SVC_USER $DATA_DIR $ETC_DIR
  chmod 700 $ETC_DIR; chmod 600 $ETC_DIR/secret.key $ETC_DIR/opennccm.env $CONF
}

setup_service() {
  progress 65 "Instalando el servicio del sistema…"
  local listen="127.0.0.1"; [[ $TLS_MODE == none ]] && listen="0.0.0.0"
  cat > /etc/systemd/system/opennccm.service <<EOF
[Unit]
Description=OpenNCCM - Network Configuration & Change Management
After=network-online.target$([[ $DB_MODE == postgresql ]] && echo " postgresql.service")
Wants=network-online.target

[Service]
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ETC_DIR/opennccm.env
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host $listen --port 8000 --workers 1 --proxy-headers
Restart=on-failure
RestartSec=5
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR $ETC_DIR

[Install]
WantedBy=multi-user.target
EOF
  ln -sf $APP_DIR/scripts/opennccm.sh /usr/local/bin/opennccm
  ln -sf $APP_DIR/scripts/opennccm.sh /usr/local/bin/opennccm-cli   # compatibilidad con 1.x
  chmod 755 $APP_DIR/scripts/opennccm.sh
  run systemctl daemon-reload
  run systemctl enable opennccm
}

setup_nginx() {
  [[ $TLS_MODE == none ]] && return 0
  progress 72 "Configurando nginx y HTTPS…"
  local crt=/etc/ssl/certs/opennccm.crt key=/etc/ssl/private/opennccm.key
  if [[ $TLS_MODE == custom && $APPLY_ONLY -eq 0 ]]; then
    install -m 644 "$CERT_FILE" $crt; install -m 600 "$KEY_FILE_TLS" $key
  elif [[ ! -f $key ]]; then
    run openssl req -x509 -nodes -newkey rsa:2048 -days 3650 -subj "/CN=${NCM_HOST}" \
      -addext "subjectAltName=$( [[ $NCM_HOST =~ ^[0-9.]+$ ]] && echo "IP:$NCM_HOST" || echo "DNS:$NCM_HOST")" \
      -keyout $key -out $crt
  fi
  local http2_listen="listen 443 ssl; listen [::]:443 ssl; http2 on;"
  nginx -v 2>&1 | grep -qE 'nginx/1\.(2[5-9]|[3-9][0-9])' || http2_listen="listen 443 ssl http2; listen [::]:443 ssl http2;"
  cat > /etc/nginx/sites-available/opennccm <<EOF
# Generado por el instalador de OpenNCCM
server { listen 80; listen [::]:80; server_name _; location / { return 301 https://\$host\$request_uri; } }
server {
  $http2_listen
  server_name ${LE_DOMAIN:-_};
  ssl_certificate $crt;
  ssl_certificate_key $key;
  ssl_protocols TLSv1.2 TLSv1.3;
  client_max_body_size 4g;   # imágenes de firmware
  client_body_timeout 900s;
  add_header X-Frame-Options DENY;
  add_header X-Content-Type-Options nosniff;
  add_header Strict-Transport-Security "max-age=31536000" always;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_read_timeout 900s;
  }
}
EOF
  ln -sf /etc/nginx/sites-available/opennccm /etc/nginx/sites-enabled/opennccm
  rm -f /etc/nginx/sites-enabled/default
  run nginx -t
  run systemctl enable nginx
  run systemctl reload-or-restart nginx
  if [[ $TLS_MODE == letsencrypt && ! -d /etc/letsencrypt/live/$LE_DOMAIN ]]; then
    progress 76 "Obteniendo certificado de Let's Encrypt para $LE_DOMAIN…"
    run certbot --nginx -n --agree-tos -m "$LE_EMAIL" -d "$LE_DOMAIN" --redirect
  fi
}

setup_firewall() {
  if [[ $FIREWALL == auto ]]; then
    command -v ufw >/dev/null && ufw status | grep -q "Status: active" && FIREWALL=yes || FIREWALL=no
  fi
  [[ $FIREWALL == yes ]] || return 0
  progress 80 "Configurando el cortafuegos…"
  run ufw allow OpenSSH
  run ufw allow "$SYSLOG_PORT/udp" comment 'OpenNCCM syslog'
  if [[ $TLS_MODE == none ]]; then run ufw allow 8000/tcp; else run ufw allow 'Nginx Full'; fi
  run ufw --force enable
}

start_and_check() {
  progress 85 "Iniciando OpenNCCM…"
  run systemctl restart opennccm
  local i
  for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  journalctl -u opennccm -n 40 --no-pager >>"$LOG" 2>&1 || true
  echo "OpenNCCM no respondió en 60 segundos" >>"$LOG"
  return 1
}

# Ejecuta Python de OpenNCCM como el usuario del servicio, con su configuración
as_app() {
  sudo -u $SVC_USER -- bash -c 'set -a; . "$1"; set +a; cd "$2"; shift 2; exec "$@"' _ \
    "$ETC_DIR/opennccm.env" "$APP_DIR" "$@"
}
cli() { as_app "$APP_DIR/.venv/bin/python" -m app.cli "$@"; }

initial_data() {
  [[ -f $DATA_DIR/.admin_created ]] && return 0
  progress 92 "Creando el administrador y los datos de ejemplo…"
  [[ -n $ADMIN_PASS ]] || ADMIN_PASS=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-14)
  run cli create-admin "$ADMIN_USER" "$ADMIN_PASS"
  run cli seed
  if [[ -n $SMTP_HOST ]]; then
    as_app env SMTP_HOST="$SMTP_HOST" SMTP_PORT="$SMTP_PORT" SMTP_SEC="$SMTP_SEC" SMTP_USER="$SMTP_USER" \
      SMTP_PASS="$SMTP_PASS" SMTP_FROM="$SMTP_FROM" ALERT_EMAILS="$ALERT_EMAILS" \
      "$APP_DIR/.venv/bin/python" - <<'PY' >>"$LOG" 2>&1
import os
from app.db import init_db, set_setting
init_db()
for key, env in [("smtp_host", "SMTP_HOST"), ("smtp_port", "SMTP_PORT"), ("smtp_security", "SMTP_SEC"),
                 ("smtp_user", "SMTP_USER"), ("smtp_password", "SMTP_PASS"), ("smtp_from", "SMTP_FROM"),
                 ("alert_emails", "ALERT_EMAILS")]:
    if os.environ.get(env):
        set_setting(key, os.environ[env])
PY
  fi
  touch $DATA_DIR/.admin_created; chown $SVC_USER:$SVC_USER $DATA_DIR/.admin_created
  umask 077
  cat > /root/opennccm-credenciales.txt <<EOF
OpenNCCM - acceso inicial ($(date '+%F %T'))
URL:        $URL
Usuario:    $ADMIN_USER
Contraseña: $ADMIN_PASS
Cambie la contraseña en «Mi cuenta» y luego borre este archivo:  sudo rm /root/opennccm-credenciales.txt
EOF
  NEW_ADMIN=1
}

finish() {
  local ver; ver=$(grep -oP '__version__ = "\K[^"]+' $APP_DIR/app/__init__.py)
  progress 100 "¡Listo! OpenNCCM $ver instalado."
  sleep 0.6; gauge_stop
  local creds=""
  if [[ ${NEW_ADMIN:-0} -eq 1 ]]; then
    creds="\n  Usuario:     $ADMIN_USER\n  Contraseña:  $ADMIN_PASS\n\n(guardados también en /root/opennccm-credenciales.txt)\n"
  fi
  echo
  echo "${c_ok}${c_b}  OpenNCCM $ver está funcionando${c_0}"
  echo "  Acceso:      $URL"
  [[ ${NEW_ADMIN:-0} -eq 1 ]] && { echo "  Usuario:     $ADMIN_USER"; echo "  Contraseña:  $ADMIN_PASS"; }
  echo "  Comandos:    opennccm status | opennccm update | opennccm help"
  echo "  Registro:    $LOG"
  echo "  Respalde SIEMPRE $ETC_DIR/secret.key junto con $DATA_DIR"
  if [[ $INTERACTIVE -eq 1 ]]; then
    msg "OpenNCCM $ver está funcionando.\n\n  Abra en su navegador:\n\n      $URL\n$creds\nComandos útiles:\n  opennccm status     estado del servicio\n  opennccm update     actualizar a la última versión\n  opennccm help       todos los comandos\n\nIMPORTANTE: respalde $ETC_DIR/secret.key en un lugar seguro." "Instalación completada" 24
  fi
}

# ----------------------------------------------------------------- flujo principal
PRE_PROBLEMS=(); PRE_NOTES=()
if [[ $APPLY_ONLY -eq 0 ]]; then
  check_preflight
  if [[ $INTERACTIVE -eq 1 ]]; then
    wizard
  else
    if [[ ${#PRE_PROBLEMS[@]} -gt 0 ]]; then
      for p in "${PRE_PROBLEMS[@]}"; do echo "${c_err}✗${c_0} $p"; done; exit 1
    fi
    for p in "${PRE_NOTES[@]+"${PRE_NOTES[@]}"}"; do warn "$p"; done
    [[ $UNATTENDED -eq 0 ]] && warn "Sin terminal interactiva: instalación desatendida con valores por defecto."
    [[ -n $NCM_HOST ]] || NCM_HOST=$(detect_ips | awk 'NR==1{print $2}')
    [[ -n $NCM_HOST ]] || NCM_HOST=$(hostname -f)
  fi
fi
case $TLS_MODE in none) URL="http://$NCM_HOST:8000/";; letsencrypt) URL="https://$LE_DOMAIN/";; *) URL="https://$NCM_HOST/";; esac

gauge_start
if [[ $APPLY_ONLY -eq 1 ]]; then
  # usado por "opennccm update": el código ya está en su versión; reaplicar sistema
  setup_python; setup_user_and_dirs; write_env; setup_service; setup_nginx
  progress 100 "Configuración del sistema reaplicada."
  gauge_stop
  exit 0
fi
install_packages
fetch_code
setup_python
setup_user_and_dirs
setup_database
write_env
setup_service
setup_nginx
setup_firewall
start_and_check
initial_data
finish
}

main "$@"
