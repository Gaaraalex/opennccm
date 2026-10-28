#!/usr/bin/env bash
# Importar desde un Zabbix real (imágenes oficiales: PostgreSQL + servidor + interfaz web) con OpenNCCM en marcha.
# Uso en la CI (Ubuntu, con Docker):  bash tests/zabbix-real-test.sh VERSIÓN [python]   (VERSIÓN: 6.0, 7.0, 7.4…)
set -euo pipefail
VER=$1
PY=${2:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
NET=zbx-net
cleanup() {
  kill "${APP_PID:-}" 2>/dev/null || true
  docker rm -f zbx-web zbx-server zbx-db >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create $NET >/dev/null
DBENV=(-e POSTGRES_USER=zabbix -e POSTGRES_PASSWORD=zabbix -e POSTGRES_DB=zabbix)
docker run -d --name zbx-db --network $NET "${DBENV[@]}" postgres:16-alpine >/dev/null
docker run -d --name zbx-server --network $NET -e DB_SERVER_HOST=zbx-db "${DBENV[@]}" \
  "zabbix/zabbix-server-pgsql:alpine-$VER-latest" >/dev/null
docker run -d --name zbx-web --network $NET -p 8081:8080 -e ZBX_SERVER_HOST=zbx-server -e DB_SERVER_HOST=zbx-db \
  "${DBENV[@]}" -e PHP_TZ=UTC "zabbix/zabbix-web-nginx-pgsql:alpine-$VER-latest" >/dev/null

echo "== Esperando a Zabbix $VER (el servidor crea la base de datos la primera vez)"
ok=0
for _ in $(seq 1 90); do
  if curl -fsS -H 'Content-Type: application/json-rpc' \
      -d '{"jsonrpc":"2.0","method":"user.login","params":{"username":"Admin","password":"zabbix"},"id":1}' \
      http://127.0.0.1:8081/api_jsonrpc.php 2>/dev/null | grep -q '"result"'; then ok=1; break; fi
  sleep 5
done
if [[ $ok != 1 ]]; then
  echo "Zabbix no respondió"; docker logs --tail 40 zbx-server; docker logs --tail 40 zbx-web; exit 1
fi

export NCM_DATA_DIR=$WORK/data NCM_KEY_FILE=$WORK/data/secret.key NCM_SYSLOG_PORT=5514 PYTHONUNBUFFERED=1
mkdir -p "$NCM_DATA_DIR"
cd "$ROOT"
"$PY" -c "import sys; sys.path.insert(0,'.'); from app.cli import main; sys.argv=['x','create-admin','admin','Admin12345']; main()"
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$WORK/app.log" 2>&1 & APP_PID=$!
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1; done

"$PY" tests/zabbix_real.py http://127.0.0.1:8081 || { tail -n 60 "$WORK/app.log"; exit 1; }
