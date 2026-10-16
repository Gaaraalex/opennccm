"""Equipo Cisco IOS simulado (SSH) para pruebas de OpenNCCM.
Uso: fakedev.py [puerto] [--legacy]   (--legacy: solo ofrece ssh-rsa y kex SHA-1, como un EdgeSwitch antiguo)"""
import os
import socket
import sys
import threading

import paramiko

HOST_KEY = paramiko.RSAKey.generate(2048)
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = int(ARGS[0]) if ARGS else 2222
LEGACY = "--legacy" in sys.argv
DISABLED = None
if LEGACY:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from app import legacy_ssh

    legacy_ssh.enable()
    T = paramiko.Transport
    DISABLED = {"keys": [k for k in T._preferred_keys if k != "ssh-rsa"],
                # group-exchange se excluye: el servidor de Paramiko lo anuncia aunque no tenga primos (moduli)
                "kex": [k for k in T._preferred_kex if not k.endswith("-sha1") or "exchange" in k]}

RUNNING = """Building configuration...

Current configuration : 1200 bytes
!
! Last configuration change at 10:00:00 UTC Mon Oct 13 2026
!
version 15.2
hostname R1
!
enable secret 5 $1$abc$xyz
!
username admin privilege 15 secret 5 $1$abc$xyz
!
interface GigabitEthernet0/0
 description WAN
 ip address 10.0.0.1 255.255.255.0
!
interface GigabitEthernet0/1
 description LAN
 ip address 192.168.1.1 255.255.255.0
!
ip http server
snmp-server community public RO
!
line vty 0 4
 transport input telnet ssh
!
end""".splitlines()
STARTUP = list(RUNNING)
VERSION = """Cisco IOS Software, C2900 Software (C2900-UNIVERSALK9-M), Version 15.2(4)M7, RELEASE SOFTWARE (fc2)
ROM: System Bootstrap, Version 15.0(1r)M15
R1 uptime is 2 weeks
cisco CISCO2911/K9 (revision 1.0) with 491520K/32768K bytes of memory.
Processor board ID FTX1234ABCD
"""


class Server(paramiko.ServerInterface):
    def check_auth_password(self, u, p):
        return paramiko.AUTH_SUCCESSFUL if (u, p) == ("admin", "cisco") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, u):
        return "password"

    def check_channel_request(self, kind, cid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *a):
        return True

    def __init__(self):
        self.is_shell = threading.Event()
        self.decided = threading.Event()

    def check_channel_shell_request(self, ch):
        self.is_shell.set()
        self.decided.set()
        return True

    def check_channel_exec_request(self, ch, command):
        cmd = command.decode() if isinstance(command, bytes) else command
        if cmd.startswith("scp") and " -t" in cmd:
            threading.Thread(target=scp_sink, args=(ch, cmd.split()[-1]), daemon=True).start()
            self.decided.set()
            return True
        return False


FILES = {}  # ruta -> bytes (flash simulada)


def scp_sink(ch, path):
    """Lado receptor del protocolo SCP (scp -t)."""
    import time as _t
    _t.sleep(0.1)
    ch.sendall(b"\0")
    buf = b""
    while b"\n" not in buf:
        buf += ch.recv(1024)
    header, rest = buf.split(b"\n", 1)
    _, size, name = header.decode().split(" ", 2)
    size = int(size)
    ch.sendall(b"\0")
    data = rest
    while len(data) < size + 1:
        chunk = ch.recv(65536)
        if not chunk:
            break
        data += chunk
    FILES[path] = data[:size]
    ch.sendall(b"\0")
    ch.send_exit_status(0)
    _t.sleep(0.2)
    ch.close()


def apply(cmd, ctx):
    global RUNNING
    s = cmd.strip()
    body = RUNNING[:-1] if RUNNING[-1] == "end" else RUNNING
    if s.startswith("no "):
        target = s[3:]
        if target == "boot system":  # como IOS: borra todas las líneas boot system
            RUNNING = [l for l in RUNNING if not l.startswith("boot system")]
            return
        RUNNING = [l for l in RUNNING if l.strip() != target]
        if ctx[0] is None and any(l == target for l in body):
            pass
        return
    if cmd.startswith(" ") or ctx[0]:
        line = " " + s
        parent = ctx[0]
        if parent and parent in RUNNING:
            i = RUNNING.index(parent) + 1
            while i < len(RUNNING) and RUNNING[i].startswith(" "):
                i += 1
            RUNNING.insert(i, line)
        return
    if s.startswith(("interface ", "line ", "router ", "vlan ")):
        ctx[0] = s
        if s not in RUNNING:
            RUNNING.insert(len(RUNNING) - 1, s)
        return
    if s not in RUNNING:
        RUNNING.insert(len(RUNNING) - 1, s)


def session(client):
    t = paramiko.Transport(client, disabled_algorithms=DISABLED)
    t.add_server_key(HOST_KEY)
    srv = Server()
    t.start_server(server=srv)
    ch = t.accept(20)
    if ch is None:
        return
    srv.decided.wait(10)
    if not srv.is_shell.is_set():  # canal exec (SCP): lo atiende scp_sink
        while t.is_active():
            threading.Event().wait(1)
        return
    mode = ["exec"]
    ctx = [None]

    def prompt():
        return {"exec": "R1#", "config": "R1(config)#", "sub": "R1(config-if)#"}[mode[0]]

    ch.send("\r\nR1#")
    buf = ""
    prev = ""
    while True:
        data = ch.recv(1024)
        if not data:
            break
        for c in data.decode(errors="ignore"):
            last, prev = prev, c
            if c in "\r\n":
                if c == "\n" and last == "\r":
                    continue
                line, buf = buf, ""
                ch.send(line + "\r\n")
                if mode[0] == "reload":  # confirmación de reinicio: se corta la sesión
                    ch.send("\r\n%SYS-5-RELOAD: Reload requested\r\n")
                    t.close()
                    return
                out = handle(line, mode, ctx)
                if mode[0] == "reload":
                    ch.send(out)
                    continue
                if out:
                    ch.send(out.replace("\n", "\r\n") + "\r\n")
                ch.send(prompt())
            else:
                buf += c
    t.close()


def handle(line, mode, ctx):
    global STARTUP
    s = line.strip()
    if not s:
        return ""
    if mode[0] == "exec":
        if s.startswith("terminal") or s == "enable":
            return ""
        if s == "show running-config":
            return "\n".join(RUNNING)
        if s == "show startup-config":
            return "\n".join(STARTUP)
        if s == "show version":
            return VERSION
        if s in ("configure terminal", "conf t"):
            mode[0] = "config"
            return "Enter configuration commands, one per line.  End with CNTL/Z."
        if s in ("write mem", "write memory", "copy running-config startup-config"):
            STARTUP = list(RUNNING)
            return "Building configuration...\n[OK]"
        if s.startswith("dir "):
            target = s[4:].strip()
            if target.endswith(":"):
                names = "\n".join(f"  1  -rw-  {len(v)}  Oct 15 2026 10:00:00 +00:00  {k.split('/')[-1]}"
                                  for k, v in FILES.items())
                return f"Directory of {target}/\n\n{names}\n\n256000000 bytes total (200000000 bytes free)"
            if target in FILES:
                return (f"Directory of {target}\n\n    1  -rw-  {len(FILES[target])}  Oct 15 2026 10:00:00 +00:00  "
                        f"{target.split('/')[-1]}\n\n256000000 bytes total (200000000 bytes free)")
            return f"%Error opening {target} (No such file or directory)"
        if s.startswith("verify /md5 "):
            import hashlib
            target = s.split()[-1]
            if target not in FILES:
                return f"%Error opening {target} (No such file or directory)"
            return f"................Done!\nverify /md5 ({target}) = {hashlib.md5(FILES[target]).hexdigest()}"
        if s == "reload":
            mode[0] = "reload"
            return "Proceed with reload? [confirm]"
        if s.startswith("show "):
            return f"(salida simulada de '{s}')"
        if s == "exit":
            return ""
        return "% Invalid input detected at '^' marker."
    # modo configuración
    if s == "end":
        mode[0], ctx[0] = "exec", None
        return ""
    if s == "exit":
        if mode[0] == "sub":
            mode[0], ctx[0] = "config", None
        else:
            mode[0] = "exec"
        return ""
    if s.startswith("bogus"):
        return "% Invalid input detected at '^' marker."
    if not line.startswith(" ") and mode[0] == "sub" and not s.startswith("no "):
        mode[0], ctx[0] = "config", None
    apply(line, ctx)
    if ctx[0]:
        mode[0] = "sub"
    return ""


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", PORT))
    sock.listen(20)
    print(f"Equipo simulado escuchando en 127.0.0.1:{PORT}", flush=True)
    while True:
        c, _ = sock.accept()
        threading.Thread(target=session, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
