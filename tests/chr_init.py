"""Primer inicio de sesión en un RouterOS CHR recién instalado (admin sin contraseña): responde a la licencia y fija la
contraseña de admin, tanto si RouterOS la pide (7.x) como si no (6.x). Uso: chr_init.py PUERTO CONTRASEÑA"""
import re
import sys
import time

import paramiko

port, password = int(sys.argv[1]), sys.argv[2]
for attempt in range(30):
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect("127.0.0.1", port, "admin", "", look_for_keys=False, allow_agent=False, timeout=10,
                  banner_timeout=20, auth_timeout=20)
        break
    except Exception as e:  # noqa: BLE001 - RouterOS todavía arrancando
        print("esperando SSH:", str(e)[:80])
        time.sleep(4)
else:
    sys.exit("RouterOS no aceptó la conexión SSH")

ch = c.invoke_shell(width=200, height=50)
buf, asked, deadline = "", False, time.time() + 90
while time.time() < deadline:
    time.sleep(0.5)
    while ch.recv_ready():
        buf += ch.recv(65535).decode(errors="replace")
    low = buf.lower()
    if "software license" in low and "[y/n]" in low:
        ch.send("n\r")
        buf = ""
    elif "repeat new password" in low:
        ch.send(password + "\r")
        buf, asked = "", True
    elif "new password" in low:
        ch.send(password + "\r")
        buf = ""
    elif re.search(r"\] >\s*$", buf):
        break
else:
    sys.exit("No apareció el prompt de RouterOS:\n" + buf[-500:])
if not asked:  # RouterOS 6: no pide contraseña nueva
    ch.send(f"/user set admin password={password}\r")
    time.sleep(2)
print("RouterOS listo (contraseña de admin fijada)")
c.close()
