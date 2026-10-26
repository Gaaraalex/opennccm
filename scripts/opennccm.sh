#!/usr/bin/env bash
# =============================================================================
#  opennccm - comando de administración de OpenNCCM
#  (instalado como /usr/local/bin/opennccm; se actualiza junto con el código)
# =============================================================================
set -Eeuo pipefail
# también al ejecutarse desde systemd (botón «Actualizar» de la web), donde HOME no está definido y git lo necesita
export HOME=${HOME:-/root}

APP_DIR=/opt/opennccm
ETC_DIR=/etc/opennccm
DATA_DIR=/var/lib/opennccm
CONF=$ETC_DIR/install.conf
SVC=opennccm
SVC_USER=opennccm
PRE_DIR=$DATA_DIR/pre-update
KEEP_PRE=5
HEALTH_URL=http://127.0.0.1:8000/healthz

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_err=$'\e[31m'; c_b=$'\e[1m'; c_dim=$'\e[2m'; c_0=$'\e[0m'
step() { echo "${c_b}==>${c_0} $*"; }
ok()   { echo "${c_ok}✓${c_0} $*"; }
warn() { echo "${c_warn}!${c_0} $*"; }
die()  { echo "${c_err}✗ $*${c_0}" >&2; exit 1; }

need_root() { [[ $EUID -eq 0 ]] || die "Este comando requiere privilegios: sudo opennccm $*"; }
load_conf() {
  [[ -d $APP_DIR/.git ]] || die "OpenNCCM no está instalado con git en $APP_DIR (instale con install.sh)."
  CHANNEL=stable; DB_MODE=sqlite
  # shellcheck disable=SC1090
  [[ -r $CONF ]] && . "$CONF"
  git config --global --get-all safe.directory 2>/dev/null | grep -x "$APP_DIR" >/dev/null || \
    git config --global --add safe.directory "$APP_DIR"
}
version_of() { grep -oP '__version__ = "\K[^"]+' "$1" 2>/dev/null || echo "?"; }
current_version() { version_of "$APP_DIR/app/__init__.py"; }
interactive() { [[ -t 0 && -t 1 ]] && command -v whiptail >/dev/null; }
export NEWT_COLORS='root=white,blue window=black,white border=blue,white title=blue,white button=white,blue actbutton=white,cyan textbox=black,white'

as_app() {  # ejecutar como el usuario del servicio, con su configuración
  sudo -u $SVC_USER -- bash -c 'set -a; . "$1"; set +a; cd "$2"; shift 2; exec "$@"' _ \
    "$ETC_DIR/opennccm.env" "$APP_DIR" "$@"
}
pycli() { as_app "$APP_DIR/.venv/bin/python" -m app.cli "$@"; }

health() {  # health [versión esperada] -> 0 si responde (y coincide la versión)
  local want=${1:-} out
  for _ in $(seq 1 90); do
    if out=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null); then
      [[ -z $want || $out == *"\"version\":\"$want\""* ]] && return 0
    fi
    sleep 1
  done
  return 1
}

# ----------------------------------------------------------------- versiones
target_ref() {  # target_ref [versión] -> referencia git a instalar
  local req=${1:-}
  if [[ -n $req ]]; then
    [[ $req == v* ]] || req="v$req"
    git -C $APP_DIR rev-parse -q --verify "refs/tags/$req" >/dev/null || die "No existe la versión $req"
    echo "$req"
  elif [[ ${CHANNEL:-stable} == main ]]; then
    echo origin/main
  else
    local t; t=$(git -C $APP_DIR tag -l 'v*' --sort=-v:refname | sed -n 1p)
    [[ -n $t ]] || t=origin/main
    echo "$t"
  fi
}

changelog_between() {  # changelog_between REF VERSION_ACTUAL
  # awk lee hasta el final (sin «exit»): si dejara de leer, «git show» moriría por SIGPIPE y, con pipefail, la actualización fallaría
  git -C $APP_DIR show "$1:CHANGELOG.md" 2>/dev/null | awk -v cur="## $2" '
    /^## / { if (index($0, cur) == 1) stop=1; else if (!stop) seen=1 }
    seen && !stop { print }' | sed -n 1,60p
}

# ----------------------------------------------------------------- copia previa / vuelta atrás
pre_backup() {  # crea la copia previa y devuelve su carpeta
  local cur_ver=$1 stamp dir
  stamp=$(date +%Y%m%d-%H%M%S)
  dir=$PRE_DIR/${stamp}_v${cur_ver}
  mkdir -p "$dir"
  git -C $APP_DIR rev-parse HEAD > "$dir/commit"
  cp -a $ETC_DIR/opennccm.env "$dir/" 2>/dev/null || true
  cp -a $CONF "$dir/" 2>/dev/null || true
  if [[ ${DB_MODE:-sqlite} == postgresql ]]; then
    # el archivo lo escribe root a propósito (pg_dump solo produce la salida)
    # shellcheck disable=SC2024
    sudo -u postgres pg_dump -Fc opennccm > "$dir/opennccm.pgdump"
  else
    local f
    for f in opennccm.db opennccm.db-wal opennccm.db-shm; do
      [[ -f $DATA_DIR/$f ]] && cp -a "$DATA_DIR/$f" "$dir/"
    done
  fi
  # conservar solo las copias más recientes
  ls -1dt "$PRE_DIR"/*/ 2>/dev/null | tail -n +$((KEEP_PRE + 1)) | xargs -r rm -rf
  echo "$dir"
}

restore_pre() {  # restore_pre CARPETA
  local dir=$1
  [[ -f $dir/commit ]] || die "Copia previa no válida: $dir"
  step "Deteniendo OpenNCCM"
  systemctl stop $SVC || true
  step "Restaurando el código ($(cut -c1-10 "$dir/commit"))"
  git -C $APP_DIR checkout --quiet --force "$(cat "$dir/commit")"
  step "Restaurando la base de datos"
  if [[ -f $dir/opennccm.pgdump ]]; then
    sudo -u postgres pg_restore --clean --if-exists -d opennccm "$dir/opennccm.pgdump"
  elif [[ -f $dir/opennccm.db ]]; then
    local stamp; stamp=$(date +%Y%m%d-%H%M%S)
    [[ -f $DATA_DIR/opennccm.db ]] && mv "$DATA_DIR/opennccm.db" "$DATA_DIR/opennccm.db.descartada-$stamp"
    rm -f "$DATA_DIR/opennccm.db-wal" "$DATA_DIR/opennccm.db-shm"
    cp -a "$dir"/opennccm.db* "$DATA_DIR/"
    chown $SVC_USER:$SVC_USER "$DATA_DIR"/opennccm.db*
  fi
  step "Reaplicando la configuración del sistema"
  bash $APP_DIR/install.sh --apply
  systemctl start $SVC
  local want; want=$(current_version)
  if health "$want"; then ok "OpenNCCM $want restaurado y funcionando."; else die "OpenNCCM no responde tras restaurar: revise 'opennccm logs'"; fi
}

# ----------------------------------------------------------------- comandos
cmd_update() {
  need_root update
  local check=0 yes=0 req="" a
  while [[ $# -gt 0 ]]; do
    case $1 in
      --check) check=1 ;; -y|--yes) yes=1 ;;
      --version) req=$2; shift ;;
      --channel) CHANNEL_ARG=$2; shift ;;
      *) die "Opción desconocida: $1" ;;
    esac; shift
  done
  load_conf
  [[ -n ${CHANNEL_ARG:-} ]] && CHANNEL=$CHANNEL_ARG
  exec 9>/run/opennccm-update.lock
  flock -n 9 || die "Ya hay una actualización en curso."

  step "Buscando actualizaciones (${CHANNEL:-stable})…"
  git -C $APP_DIR fetch --quiet --tags --force origin || die "No se pudo contactar el repositorio: $(git -C $APP_DIR remote get-url origin)"
  local cur_ver cur_commit target tgt_commit new_ver notes
  cur_ver=$(current_version); cur_commit=$(git -C $APP_DIR rev-parse HEAD)
  target=$(target_ref "$req")
  tgt_commit=$(git -C $APP_DIR rev-parse "$target^{commit}")
  new_ver=$(git -C $APP_DIR show "$target:app/__init__.py" | grep -oP '__version__ = "\K[^"]+')
  if [[ $tgt_commit == "$cur_commit" ]]; then
    ok "OpenNCCM $cur_ver ya es la versión más reciente."
    return 0
  fi
  notes=$(changelog_between "$target" "$cur_ver")
  echo
  echo "${c_b}Actualización disponible: $cur_ver → $new_ver${c_0}"
  echo "${c_dim}${notes}${c_0}"
  echo
  [[ $check -eq 1 ]] && return 0

  if [[ $yes -eq 0 ]]; then
    if interactive; then
      whiptail --backtitle "OpenNCCM" --title "Actualizar OpenNCCM" --scrolltext --yes-button "Actualizar" --no-button "Cancelar" \
        --yesno "Versión instalada:  $cur_ver\nVersión nueva:      $new_ver\n\nNovedades:\n$notes\n\nSe hará una copia previa (código + base de datos). Si la versión nueva no arranca, se volverá automáticamente a la anterior. OpenNCCM estará detenido 1-2 minutos." 24 76 \
        || { echo "Cancelado."; return 1; }
    elif [[ -t 0 ]]; then
      read -r -p "¿Actualizar ahora? [s/N] " a; [[ $a =~ ^[sSyY] ]] || { echo "Cancelado."; return 1; }
    else
      die "Use --yes para actualizar sin confirmación."
    fi
  fi

  step "Creando copia previa"
  systemctl stop $SVC
  local pre
  pre=$(pre_backup "$cur_ver") || { systemctl start $SVC; die "No se pudo crear la copia previa: no se actualizó nada."; }
  ok "Copia previa: $pre"
  step "Instalando $new_ver"
  git -C $APP_DIR checkout --quiet --force "$target"
  if ! bash $APP_DIR/install.sh --apply; then
    warn "Falló la configuración de la versión nueva: volviendo a $cur_ver"
    restore_pre "$pre"; return 1
  fi
  step "Iniciando y verificando"
  systemctl start $SVC
  if health "$new_ver"; then
    ok "OpenNCCM actualizado: $cur_ver → $new_ver"
    echo "  Si nota algún problema puede volver atrás con:  sudo opennccm rollback"
  else
    journalctl -u $SVC -n 30 --no-pager || true
    warn "La versión $new_ver no respondió correctamente: volviendo a $cur_ver automáticamente"
    restore_pre "$pre"
    return 1
  fi
}

cmd_rollback() {
  need_root rollback
  load_conf
  local dir="" yes=0
  while [[ $# -gt 0 ]]; do case $1 in -y|--yes) yes=1 ;; *) dir=$1 ;; esac; shift; done
  if [[ -z $dir ]]; then
    mapfile -t list < <(ls -1dt "$PRE_DIR"/*/ 2>/dev/null | sed 's:/$::')
    [[ ${#list[@]} -gt 0 ]] || die "No hay copias previas en $PRE_DIR"
    if interactive && [[ $yes -eq 0 ]]; then
      local items=() d
      for d in "${list[@]}"; do items+=("$(basename "$d")" ""); done
      dir=$(whiptail --backtitle "OpenNCCM" --title "Volver a una versión anterior" \
        --ok-button "Restaurar" --cancel-button "Cancelar" --menu \
        "\nElija la copia previa a restaurar (la más reciente arriba).\nSe restauran el código y la base de datos de ese momento." \
        20 74 8 "${items[@]}" 3>&1 1>&2 2>&3) || { echo "Cancelado."; return 1; }
      dir=$PRE_DIR/$dir
    else
      dir=${list[0]}
    fi
  fi
  [[ -d $dir ]] || dir=$PRE_DIR/$dir
  echo "Se restaurará: $(basename "$dir")"
  echo "${c_warn}Se perderán los cambios registrados en OpenNCCM después de esa copia (la base actual se conserva aparte).${c_0}"
  if [[ $yes -eq 0 ]]; then
    if interactive; then
      whiptail --title "Confirmar" --yes-button "Sí" --no-button "No" --yesno "\n¿Volver a $(basename "$dir")?\n\nLa base de datos actual se conservará como .descartada-…" 12 70 || return 1
    elif [[ -t 0 ]]; then
      local a; read -r -p "¿Continuar? [s/N] " a; [[ $a =~ ^[sSyY] ]] || return 1
    else
      die "Use --yes para confirmar."
    fi
  fi
  restore_pre "$dir"
}

cmd_status() {
  local ver active url="" rev
  ver=$(current_version)
  active=$(systemctl is-active $SVC 2>/dev/null || true)
  if [[ -r $CONF ]]; then
    # shellcheck disable=SC1090
    . "$CONF"
    case ${TLS_MODE:-self-signed} in none) url="http://${NCM_HOST}:8000/";; letsencrypt) url="https://${LE_DOMAIN}/";; *) url="https://${NCM_HOST}/";; esac
  fi
  echo "${c_b}OpenNCCM $ver${c_0}"
  [[ $active == active ]] && ok "Servicio: activo" || warn "Servicio: $active"
  if curl -fsS --max-time 3 $HEALTH_URL >/dev/null 2>&1; then ok "Aplicación: responde"; else warn "Aplicación: no responde"; fi
  [[ -n $url ]] && echo "  Acceso:     $url"
  rev=$(git -C $APP_DIR describe --tags --always 2>/dev/null)   # vacío en una instalación desde el paquete .tar.gz
  echo "  Código:     $APP_DIR${rev:+ ($rev)}"
  echo "  Datos:      $DATA_DIR ($(du -sh $DATA_DIR 2>/dev/null | cut -f1))"
  echo "  Disco libre: $(df -h $DATA_DIR | awk 'NR==2{print $4}')"
  if [[ $EUID -eq 0 && -d $APP_DIR/.git ]] && timeout 15 git -C $APP_DIR fetch --quiet --tags origin 2>/dev/null; then
    local latest; latest=$(git -C $APP_DIR tag -l 'v*' --sort=-v:refname | sed -n 1p)
    if [[ -n $latest && $latest != "v$ver" ]]; then warn "Hay una versión nueva: $latest  →  sudo opennccm update"
    else ok "Al día"; fi
  fi
}

cmd_uninstall() {
  need_root uninstall
  local purge=0; [[ ${1:-} == --purge ]] && purge=1
  echo "${c_warn}Se desinstalará OpenNCCM.${c_0}"
  [[ $purge -eq 1 ]] && echo "${c_err}También se BORRARÁN todos los datos y la clave de cifrado.${c_0}"
  local a; read -r -p "Escriba DESINSTALAR para confirmar: " a
  [[ $a == DESINSTALAR ]] || { echo "Cancelado."; return 1; }
  systemctl disable --now $SVC 2>/dev/null || true
  rm -f /etc/systemd/system/opennccm.service /usr/local/bin/opennccm-cli
  rm -f /etc/nginx/sites-enabled/opennccm /etc/nginx/sites-available/opennccm
  systemctl reload nginx 2>/dev/null || true
  systemctl daemon-reload
  rm -rf $APP_DIR
  if [[ $purge -eq 1 ]]; then rm -rf $DATA_DIR $ETC_DIR; userdel $SVC_USER 2>/dev/null || true
  else echo "Datos conservados en $DATA_DIR y $ETC_DIR"; fi
  rm -f /usr/local/bin/opennccm
  ok "OpenNCCM desinstalado."
}

usage() {
cat <<EOF
${c_b}opennccm${c_0} - administración de OpenNCCM $(current_version)

  ${c_b}status${c_0}                      Estado del servicio, versión y si hay actualizaciones
  ${c_b}update${c_0} [--check] [--yes]    Actualizar a la última versión publicada
         [--version vX.Y.Z]   Instalar una versión concreta
         [--channel main]     Usar la rama de desarrollo
  ${c_b}rollback${c_0} [CARPETA] [--yes]  Volver a la versión anterior (código + base de datos)
  ${c_b}backup${c_0} [--with-key]         Copia de recuperación de la base de datos
  ${c_b}restore${c_0} ARCHIVO             Restaurar una copia de recuperación
  ${c_b}users${c_0}                       Listar usuarios, rol, estado y equipos permitidos
  ${c_b}reset-password${c_0} USUARIO CLAVE Cambiar la contraseña (o crear un administrador)
  ${c_b}reset-2fa${c_0} USUARIO           Quitar la verificación en dos pasos (perdió el teléfono)
  ${c_b}backup-all${c_0}                  Respaldar ahora la configuración de todos los equipos
  ${c_b}seed${c_0}                        Cargar políticas y configlets de ejemplo
  ${c_b}logs${c_0} [-f]                   Ver el registro del servicio
  ${c_b}start | stop | restart${c_0}      Controlar el servicio
  ${c_b}version${c_0}                     Versión instalada
  ${c_b}uninstall${c_0} [--purge]         Desinstalar (--purge borra también los datos)
EOF
}

cmd=${1:-help}; shift || true
case $cmd in
  update|upgrade) cmd_update "$@" ;;
  rollback) cmd_rollback "$@" ;;
  status) cmd_status ;;
  version|--version|-v) current_version ;;
  backup|system-backup) need_root backup; pycli system-backup "$@" ;;
  restore)
    need_root restore; [[ -n ${1:-} ]] || die "Uso: opennccm restore ARCHIVO"
    systemctl stop $SVC; pycli restore "$(readlink -f "$1")"; systemctl start $SVC; health && ok "Restaurado." ;;
  reset-password|create-admin) need_root "$cmd"; pycli create-admin "$@" ;;
  reset-2fa) need_root "$cmd"; pycli reset-2fa "$@" ;;
  users|list-users) need_root users; pycli list-users ;;
  backup-all|seed) need_root "$cmd"; pycli "$cmd" "$@" ;;
  logs) journalctl -u $SVC --no-pager -n 200 "$@" ;;
  start|stop|restart) need_root "$cmd"; systemctl "$cmd" $SVC; [[ $cmd == stop ]] || { health && ok "OpenNCCM responde."; } ;;
  uninstall) cmd_uninstall "$@" ;;
  help|-h|--help) usage ;;
  *) echo "Comando desconocido: $cmd"; echo; usage; exit 2 ;;
esac
