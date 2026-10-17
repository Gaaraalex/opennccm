import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("NCM_DATA_DIR", "/var/lib/opennccm"))
KEY_FILE = Path(os.environ.get("NCM_KEY_FILE", "/etc/opennccm/secret.key"))
DATABASE_URL = os.environ.get("NCM_DATABASE_URL", f"sqlite:///{DATA_DIR / 'opennccm.db'}")
SYSLOG_PORT = int(os.environ.get("NCM_SYSLOG_PORT", "514"))
SYSLOG_ENABLED = os.environ.get("NCM_SYSLOG_ENABLED", "1") == "1"
WORKERS = int(os.environ.get("NCM_WORKERS", "16"))
NVD_API_KEY = os.environ.get("NCM_NVD_API_KEY", "")
# autoridad certificadora propia creada por el instalador (se descarga desde el login para confiar en el sitio)
TLS_CA = Path(os.environ.get("NCM_TLS_CA", "/etc/opennccm/tls/ca.crt"))
# repositorio del que se descargan las actualizaciones (para avisar de versiones nuevas)
UPDATE_REPO = os.environ.get("NCM_UPDATE_REPO", "Gaaraalex/opennccm")
# petición de actualización desde la web: la atiende opennccm-webupdate.path (systemd, como root)
UPDATE_REQUEST = DATA_DIR / "update.request"
UPDATE_LOG = DATA_DIR / "update.log"
