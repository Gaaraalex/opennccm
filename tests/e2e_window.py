"""Cambio no autorizado durante una ventana de mantenimiento: debe diferirse y revertirse al cerrarse."""
import os
import socket
import sys
import tempfile
import time
from datetime import datetime, timedelta

import requests
from netmiko import ConnectHandler

sys.path.insert(0, ".")
os.environ.setdefault("NCM_DATA_DIR", os.path.join(tempfile.gettempdir(), "ncmtest"))
os.environ.setdefault("NCM_KEY_FILE", os.path.join(os.environ["NCM_DATA_DIR"], "secret.key"))
from app.db import Device, SessionLocal  # noqa: E402

with SessionLocal() as db:
    d = db.get(Device, 1)
    d.maintenance_until = datetime.now() + timedelta(seconds=70)
    d.auto_rollback = True
    db.commit()
c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
c.send_config_set(["username intruso privilege 15 secret 0 y"], cmd_verify=False)
c.disconnect()
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(
    b"<189>46: R1: %SYS-5-CONFIG_I: Configured from console by x on vty0", ("127.0.0.1", 5514))
t0 = time.time()
s = requests.Session()
s.post("http://127.0.0.1:8000/login", data={"username": "admin", "password": "Admin12345"})
time.sleep(40)
c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
during = "intruso" in c.send_command("show running-config")
c.disconnect()
print("durante la ventana el cambio sigue (diferido, no revertido aún):", during)
done = False
while time.time() - t0 < 200:
    time.sleep(5)
    c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
    gone = "intruso" not in c.send_command("show running-config")
    c.disconnect()
    if gone:
        done = True
        break
print(f"tras cerrarse la ventana se revirtió: {done} (a los {int(time.time() - t0)} s)")
sys.exit(0 if during and done else 1)
