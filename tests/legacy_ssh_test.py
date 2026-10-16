"""Conexión a un equipo que solo ofrece ssh-rsa y kex SHA-1 (como un Ubiquiti EdgeSwitch antiguo).
Uso: legacy_ssh_test.py <host> <puerto> <usuario> <clave> <tipo_netmiko> [--sin-compat]
Con compatibilidad debe conectar (salida 0); con --sin-compat debe fallar (salida 0 si falla como se espera)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
host, port, user, pw, dtype = sys.argv[1:6]
sin = "--sin-compat" in sys.argv
if not sin:
    from app import legacy_ssh

    legacy_ssh.enable()
from netmiko import ConnectHandler

try:
    c = ConnectHandler(device_type=dtype, host=host, port=int(port), username=user, password=pw,
                       conn_timeout=15, auth_timeout=15, banner_timeout=15)
    t = c.remote_conn.get_transport()
    out = c.send_command("show version" if dtype != "linux" else "echo hola")
    print(f"CONECTADO hostkey={t.host_key_type} salida={out.strip()[:40]!r}")
    c.disconnect()
    ok = True
except Exception as e:  # noqa: BLE001
    print("FALLÓ:", type(e).__name__, (str(e).splitlines() or [""])[0][:200])
    ok = False
if sin:
    print("OK: sin compatibilidad falla, como se esperaba" if not ok else "FAIL: conectó sin compatibilidad (la prueba no es válida)")
    sys.exit(0 if not ok else 1)
print("OK: conecta con compatibilidad" if ok else "FAIL: no conecta con compatibilidad")
sys.exit(0 if ok else 1)
