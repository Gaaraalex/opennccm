#!/usr/bin/env bash
# Ejecuta las pruebas de extremo a extremo contra un router Cisco simulado.
# Uso (desde la raíz del repositorio, con las dependencias instaladas):  bash tests/run-tests.sh [python]
set -euo pipefail
PY=${1:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
export NCM_DATA_DIR=$WORK/data NCM_KEY_FILE=$WORK/data/secret.key NCM_SYSLOG_PORT=5514
export PYTHONUNBUFFERED=1   # que el registro de la aplicación quede completo si algo falla
# enlaces de donación de prueba (en producción los fija app/config.py)
export NCM_DONATE_GITHUB=https://github.com/sponsors/prueba NCM_DONATE_KOFI=https://ko-fi.com/prueba NCM_DONATE_OPENCOLLECTIVE=https://opencollective.com/prueba
mkdir -p "$NCM_DATA_DIR"
cd "$ROOT"
cleanup() { kill "${APP_PID:-}" "${DEV_PID:-}" "${DEV2_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

"$PY" -c "import sys; sys.path.insert(0,'.'); from app.cli import main; sys.argv=['x','create-admin','admin','Admin12345']; main(); sys.argv=['x','seed']; main()"
"$PY" tests/fakedev.py 2222 > "$WORK/fakedev.log" 2>&1 & DEV_PID=$!
"$PY" tests/fakedev.py 2223 --legacy > "$WORK/fakedev-legacy.log" 2>&1 & DEV2_PID=$!  # solo ssh-rsa / kex SHA-1
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$WORK/app.log" 2>&1 & APP_PID=$!
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break; sleep 1; done

status=0
for t in e2e e2e_syslog e2e2 e2e_window e2e_users e2e_legacy e2e_enable e2e_edge e2e_sched e2e_panel e2e_console e2e_security e2e_oidc e2e_donate e2e_fwupdates e2e_examples e2e_shells e2e_i18n unit_i18n unit_fastpath unit_inventory unit_vendors unit_contrast unit_syslog unit_extauth unit_nvd unit_packs unit_fwupdates; do
  echo "=================== $t ==================="
  if ! "$PY" "tests/$t.py"; then status=1; fi
done
if [[ $status -ne 0 ]]; then
  echo "--- app.log (respaldos, cambios, reversiones, errores) ---"
  grep -E "opennccm|ERROR|Traceback|Error" "$WORK/app.log" | tail -n 150 || true
  echo "--- eventos ---"
  "$PY" - <<'PY'
import sys; sys.path.insert(0, ".")
from app.db import Event, SessionLocal, select
with SessionLocal() as db:
    for e in db.scalars(select(Event).order_by(Event.id)):
        print(e.created_at, e.severity, e.category, "|", e.message.splitlines()[0][:150])
PY
fi
exit $status
