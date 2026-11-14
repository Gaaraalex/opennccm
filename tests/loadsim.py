"""Muchos equipos Cisco IOS simulados por SSH en un solo puerto, para la prueba de carga (tests/load_test.py).

Uso: loadsim.py [puerto] [--change=0.2]
Todos los equipos de la prueba apuntan a este mismo puerto; el tamaño de su configuración lo decide el usuario con el
que entran (la credencial de cada grupo de tamaño): «carga» ≈ 250 líneas, «carga-m» ≈ 3.000 y «carga-l» ≈ 40.000
(como una OLT grande). Contraseña: «carga». En cada conexión, con probabilidad --change, la configuración trae una
diferencia (descripción de una interfaz y la hora de un NTP), así que una parte de los respaldos crea versión nueva,
como en una red real. Un hilo por conexión: los respaldos a la vez los limita OpenNCCM (backup_concurrency).
Escucha solo en 127.0.0.1."""
import random
import socket
import sys
import threading

import paramiko

CHANGE = 0.2       # --change: parte de las conexiones que traen una diferencia
HOST_KEY = None    # se genera al arrancar (importar el módulo no abre nada: load_test.py usa config())
SIZES = {"carga": 24, "carga-m": 300, "carga-l": 4000}  # interfaces: ~10 líneas cada una
STATS = {"conns": 0, "active": 0, "peak": 0}
_lock = threading.Lock()


def config(user: str, changed: bool) -> str:
    n = SIZES.get(user, 24)
    out = ["Building configuration...", "", f"Current configuration : {n * 300} bytes", "!", "version 15.2",
           "service timestamps log datetime msec", "hostname CARGA", "!", "enable secret 5 $1$abc$xyz", "!",
           "username admin privilege 15 secret 5 $1$abc$xyz", "!"]
    tag = random.randint(1, 10 ** 6) if changed else 0
    for i in range(n):
        out += [f"interface GigabitEthernet{i // 48}/0/{i % 48}",
                f" description Cliente {i}{' r' + str(tag) if changed and i == n // 2 else ''}",
                " switchport mode access", f" switchport access vlan {100 + i % 200}", " spanning-tree portfast",
                " storm-control broadcast level 1.00", " ip dhcp snooping limit rate 50", " no cdp enable",
                f" service-policy input CLIENTE-{i % 8}", "!"]
    out += ["ip ssh version 2", "logging host 10.0.0.5", f"ntp server 10.0.0.{1 + tag % 250 if changed else 1}",
            "snmp-server community s3cret RO", "line vty 0 4", " transport input ssh", "!", "end"]
    return "\n".join(out)


VERSION = """Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E8, RELEASE SOFTWARE (fc3)
ROM: Bootstrap program is C2960X boot loader
CARGA uptime is 12 weeks, 3 days
cisco WS-C2960X-48FPD-L (APM86XXX) processor (revision B0) with 524288K bytes of memory.
Processor board ID FOC1234X0YZ
"""
SHOW = {
    "show version": VERSION,
    "show interfaces status": ("Port      Name               Status       Vlan       Duplex  Speed Type\n"
                               "Gi1/0/1   Cliente 0          connected    100        a-full a-1000 10/100/1000BaseTX\n"
                               "Gi1/0/2   Cliente 1          notconnect   101          auto   auto 10/100/1000BaseTX\n"),
    "show cdp neighbors detail": "",
    "show lldp neighbors detail": "% LLDP is not enabled",
    "show mac address-table": "          Mac Address Table\n-------------------------------------------\n\nVlan    Mac Address       Type        Ports\n",
    "show ip arp": "Protocol  Address          Age (min)  Hardware Addr   Type   Interface\n",
    "show environment all": "FAN 1 is OK\nSYSTEM TEMPERATURE is OK\n",
}


class Server(paramiko.ServerInterface):
    def __init__(self):
        self.user = "carga"
        self.shell = threading.Event()

    def check_auth_password(self, u, p):
        if u in SIZES and p == "carga":
            self.user = u
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, u):
        return "password"

    def check_channel_request(self, kind, cid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *a):
        return True

    def check_channel_shell_request(self, ch):
        self.shell.set()
        return True


def session(client):
    with _lock:
        STATS["conns"] += 1
        STATS["active"] += 1
        STATS["peak"] = max(STATS["peak"], STATS["active"])
    t = paramiko.Transport(client)
    try:
        t.add_server_key(HOST_KEY)
        srv = Server()
        t.start_server(server=srv)
        ch = t.accept(20)
        if ch is None or not srv.shell.wait(10):
            return
        changed = random.random() < CHANGE
        running = config(srv.user, changed)
        ch.send("\r\nCARGA#")
        buf, prev = "", ""
        while True:
            data = ch.recv(4096)
            if not data:
                break
            for c in data.decode(errors="ignore"):
                last, prev = prev, c
                if c not in "\r\n":
                    buf += c
                    continue
                if c == "\n" and last == "\r":
                    continue
                line, buf = buf.strip(), ""
                ch.send(line + "\r\n")
                if line in ("exit", "quit", "logout"):
                    return
                if not line or line.startswith("terminal") or line == "enable":
                    out = ""
                elif line in ("show running-config", "show startup-config"):
                    out = running
                else:
                    out = SHOW.get(line, "% Invalid input detected at '^' marker.")
                if out:
                    ch.sendall((out.replace("\n", "\r\n") + "\r\n").encode())
                ch.send("CARGA#")
    except (EOFError, OSError, paramiko.SSHException):
        pass
    finally:
        t.close()
        with _lock:
            STATS["active"] -= 1


def main():
    global CHANGE, HOST_KEY
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = int(args[0]) if args else 2300
    CHANGE = float(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--change=")), CHANGE))
    HOST_KEY = paramiko.RSAKey.generate(2048)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(256)
    print(f"Equipos simulados para la prueba de carga en 127.0.0.1:{port}", flush=True)
    while True:
        c, _a = sock.accept()
        threading.Thread(target=session, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
