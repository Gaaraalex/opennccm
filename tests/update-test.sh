#!/usr/bin/env bash
# Prueba de "opennccm update" y "opennccm rollback" sobre una instalación real.
# Crea versiones ficticias (9.9.9 correcta y 9.9.10 rota) en un repositorio local y actualiza contra él.
# Uso:  sudo bash tests/update-test.sh RUTA_REPO
set -euo pipefail
REPO=$(cd "${1:-.}" && pwd)
NEXT=/tmp/opennccm-next
PASS=${ADMIN_PASS:-Admin12345}

health_version() { curl -fsS http://127.0.0.1:8000/healthz | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'; }
login_ok() {  # el usuario y sus datos siguen ahí
  local jar; jar=$(mktemp)
  local dest; dest=$(curl -s -c "$jar" -b "$jar" -o /dev/null -w '%{redirect_url}' \
    --data-urlencode "username=admin" --data-urlencode "password=$PASS" http://127.0.0.1:8000/login)
  [[ $dest == "http://127.0.0.1:8000/" ]]
}
new_version() {  # new_version X.Y.Z [romper]
  cd "$NEXT"
  sed -i "s/__version__ = \".*\"/__version__ = \"$1\"/" app/__init__.py
  printf '# Historial de versiones\n\n## %s\n- Versión de prueba de la actualización automática.\n' "$1" > /tmp/cl
  tail -n +2 CHANGELOG.md >> /tmp/cl && mv /tmp/cl CHANGELOG.md
  [[ ${2:-} == romper ]] && echo 'raise RuntimeError("versión rota a propósito")' >> app/main.py
  git -c user.name=ci -c user.email=ci@example.com commit -qam "v$1"
  git tag "v$1"
  cd - >/dev/null
}

CUR=$(opennccm version)
echo "== Versión instalada: $CUR"
rm -rf "$NEXT"
git clone -q "$REPO" "$NEXT"
git -C "$NEXT" checkout -q -B main "$(git -C "$REPO" rev-parse HEAD)"
git config --global --get-all safe.directory | grep -qx "$NEXT" || git config --global --add safe.directory "$NEXT"
git -C /opt/opennccm remote set-url origin "$NEXT"

echo "== 1) Actualización correcta a 9.9.9"
new_version 9.9.9
opennccm update --check | tee /tmp/check.txt
grep -q "9.9.9" /tmp/check.txt
opennccm update --yes
[[ $(health_version) == 9.9.9 ]] || { echo "No quedó en 9.9.9"; exit 1; }
login_ok || { echo "El inicio de sesión falló tras actualizar"; exit 1; }
opennccm update --check | grep -q "ya es la versión más reciente"
echo "OK actualización"

echo "== 2) Vuelta atrás manual"
opennccm rollback --yes
[[ $(health_version) == "$CUR" ]] || { echo "No volvió a $CUR"; exit 1; }
login_ok
echo "OK vuelta atrás"

echo "== 3) Actualización rota: debe volver sola a la versión anterior"
new_version 9.9.10 romper
if opennccm update --yes; then echo "La actualización rota no debió terminar bien"; exit 1; fi
[[ $(health_version) == "$CUR" ]] || { echo "No se recuperó automáticamente"; exit 1; }
login_ok
echo "OK recuperación automática"

echo "== 4) Copias previas guardadas"
ls -1 /var/lib/opennccm/pre-update/
echo "TODO OK"
