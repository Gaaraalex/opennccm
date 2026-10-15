"""Cambio no autorizado -> syslog -> respaldo -> reversión automática; y escaneo CVE."""
import re
import socket
import sys
import time

import requests
from netmiko import ConnectHandler

B = "http://127.0.0.1:8000"
s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
# activar reversión automática en R1
s.post(B + "/devices/1/edit", data={"name": "R1", "host": "127.0.0.1", "device_type": "cisco_ios",
                                    "protocol": "ssh", "port": "2222", "credential_id": "1", "group_id": "1",
                                    "enabled": "on", "auto_rollback": "on"})
time.sleep(200 if False else 0)
# cambio "a mano" fuera de OpenNCCM
c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
c.send_config_set(["username hacker privilege 15 secret 0 x"], cmd_verify=False)
c.disconnect()
print("cambio no autorizado aplicado")
# el equipo avisa por syslog
u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
u.sendto(b"<189>45: R1: %SYS-5-CONFIG_I: Configured from console by admin on vty0", ("127.0.0.1", 5514))
# la ventana de mantenimiento del cambio previo dura 3 min; esperar a que expire si hace falta
deadline = time.time() + 260
ok_detect = ok_rb = False
while time.time() < deadline:
    time.sleep(5)
    ev = s.get(B + "/events").text
    ok_detect = "Syslog de cambio" in ev
    ok_rb = "Reversión automática" in ev
    if ok_rb:
        break
print("syslog detectado:", ok_detect)
print("reversión automática:", ok_rb)
time.sleep(8)
c = ConnectHandler(device_type="cisco_ios", host="127.0.0.1", port=2222, username="admin", password="cisco")
cfg = c.send_command("show running-config")
c.disconnect()
removed = "hacker" not in cfg
print("usuario 'hacker' eliminado del equipo:", removed)
ev = s.get(B + "/events").text
logged = "+username hacker" in ev
print("cambio no autorizado registrado con diff:", logged)

# Consulta real a NVD (servicio externo): informativa, no hace fallar la prueba
r = s.post(B + "/devices/1/cve")
print("CVE:", re.findall(r'<div class="flash[^"]*">(.*?)</div>', r.text, re.S))
print("CVE listados:", len(set(re.findall(r"CVE-\d{4}-\d+", s.get(B + "/devices/1").text))))

ok = ok_detect and ok_rb and removed and logged
print("\nRESULTADO:", "TODO OK" if ok else "FALLOS")
sys.exit(0 if ok else 1)
