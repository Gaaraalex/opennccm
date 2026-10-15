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
