"""Equipo Cisco IOS simulado (SSH) para pruebas de OpenNCCM.
Uso: fakedev.py [puerto] [--legacy]   (--legacy: solo ofrece ssh-rsa y kex SHA-1, como un EdgeSwitch antiguo)"""
import os
import re
import socket
import sys
import threading

import paramiko

HOST_KEY = paramiko.RSAKey.generate(2048)
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT = int(ARGS[0]) if ARGS else 2222
LEGACY = "--legacy" in sys.argv
# --enable-pass=X: arranca en modo usuario (R1>) y «enable» pide esa contraseña, como un EdgeSwitch
ENABLE_PASS = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--enable-pass=")), None)
EDGE = "--edgeswitch" in sys.argv  # simula un Ubiquiti EdgeSwitch (FastPath) en lugar de un Cisco IOS
# --shell=pfsense|opnsense|alpine|airos: sistema con shell Unix (pfSense/OPNsense entran a su menú de consola)
SHELL_DEV = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--shell=")), None)
EXOS = "--exos" in sys.argv  # Extreme EXOS (máquina virtual de GNS3)
if EDGE:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
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
    if SHELL_DEV or EXOS:
        return line_session(ch, t)
    mode = ["user" if ENABLE_PASS is not None else "exec"]
    ctx = [None]

    def prompt():
        if EDGE:
            return {"user": "(SW-Prueba) >", "enpw": "Password:", "exec": "(SW-Prueba) #",
                    "config": "(SW-Prueba) (Config)#", "sub": "(SW-Prueba) (Interface)#",
                    "vlandb": "(SW-Prueba) (Vlan)#"}[mode[0]]
        return {"user": "R1>", "enpw": "Password:", "exec": "R1#", "config": "R1(config)#",
                "sub": "R1(config-if)#"}[mode[0]]

    ch.send("\r\n" + prompt())
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
                ch.send(("" if mode[0] == "enpw" else line) + "\r\n")  # la contraseña no se muestra
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
    if mode[0] == "enpw":
        ok = s == ENABLE_PASS
        mode[0] = "exec" if ok else "user"
        return "" if ok else "% Access denied"
    if not s:
        return ""
    if EDGE:
        return edge_handle(s, mode, ctx)
    if mode[0] == "user":
        if s == "enable":
            mode[0] = "enpw"
            return ""
        if s.startswith("terminal") or s == "show version":
            return "" if s.startswith("terminal") else VERSION
        return "% Invalid input detected at '^' marker."
    if mode[0] == "exec":
        if s.startswith("terminal") or s == "enable":
            return ""
        if s == "show running-config":
            return "\n".join(RUNNING)
        if s == "show startup-config":
            return "\n".join(STARTUP)
        if s == "show version":
            return VERSION
        if s == "show cdp neighbors detail":  # mapa de la red: un vecino que no está en OpenNCCM
            return ("-------------------------\nDevice ID: SW-Vecino.empresa.local\nEntry address(es):\n"
                    "  IP address: 192.0.2.77\nPlatform: cisco WS-C2960X-48FPD-L,  Capabilities: Switch IGMP\n"
                    "Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet1/0/48\n")
        if s == "show lldp neighbors detail":
            return "% LLDP is not enabled"
        if s == "show interfaces status":  # puertos de la ficha del equipo
            return ("Port      Name               Status       Vlan       Duplex  Speed Type\n"
                    "Gi0/0     WAN                connected    routed     a-full a-1000 10/100/1000BaseTX\n"
                    "Gi0/1     LAN                connected    routed     a-full  a-100 10/100/1000BaseTX\n"
                    "Gi0/2                        disabled     1            auto   auto 10/100/1000BaseTX\n"
                    "Gi0/3                        notconnect   1            auto   auto 10/100/1000BaseTX\n")
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


# --------------------------------------------------------------------------- Ubiquiti EdgeSwitch (FastPath)
# Configuración sin sangría con tres ámbitos: modo privilegiado, «vlan database» y «configure» (bloques con «exit»).
# Como el equipo real, rechaza los comandos escritos en el modo equivocado.
EDGE_VERSION = """
Switch: 1

System Description............................. EdgeSwitch 16-Port 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe
Machine Type................................... EdgeSwitch 16-Port 150W
Machine Model.................................. ES-16-150W
Serial Number.................................. 7483C2ABCDEF
Burned In MAC Address.......................... 74:83:C2:AB:CD:EF
Software Version............................... 1.9.3.5263085
"""
EDGE_STATE = {
    "exec": ["network protocol none", "network parms 10.0.0.223 255.255.255.0 10.0.0.1"],
    "vlans": {1, 10},
    "vlan": ['vlan name 10 "Datos"'],
    "top": ['username "admin" password 0123456789abcdef level 15 encrypted', 'snmp-server sysname "SW-Prueba"'],
    "blocks": {"line console": [], "interface 0/1": ["description 'Uplink'", "vlan participation include 10",
                                                     "vlan tagging 10"], "interface 0/2": ["description 'PC'"]},
    "uptime": 0,
}
INVALID = "% Invalid input detected at '^' marker."


def edge_render():
    from app.rollback import _vlan_spec
    st = EDGE_STATE
    st["uptime"] += 1
    out = ["!Current Configuration:", "!",
           '!System Description "EdgeSwitch 16-Port 150W, 1.9.3.5263085, Linux 3.6.5-03de2dbe, 1.0.0.4857129"',
           '!System Software Version "1.9.3.5263085"',
           f'!System Up Time          "0 days 1 hrs {st["uptime"]} mins 0 secs"',
           "!Additional Packages     QOS,IPv6 Management,Routing",
           f"!Current SNTP Synchronized Time: Oct 16 12:{st['uptime'] % 60:02d}:00 2026 UTC", "!"]
    out += st["exec"] + ["vlan database", f"vlan {_vlan_spec(st['vlans'] - {1})}"] + st["vlan"] + ["exit", "",
                                                                                                   "configure"]
    out += st["top"]
    for header, children in st["blocks"].items():
        if header.startswith("interface ") and not children:
            continue  # un puerto con valores por omisión no aparece
        out += [header] + children + ["exit", ""]
    out.append("exit")
    return "\n".join(out)


def edge_apply(items, line):
    from app.rollback import _fp_key
    if line.startswith("no "):
        rest = line[3:]
        items[:] = [x for x in items if x != rest and not x.startswith(rest + " ")]
        return
    t = line.split()
    if t[:2] == ["vlan", "participation"] and len(t) > 3 and t[2] == "auto":
        items[:] = [x for x in items if not re.fullmatch(rf"vlan participation (include|exclude) {t[3]}", x)]
        return
    key = _fp_key(line) or (tuple(t[:2]) if t[:1] == ["network"] else None)
    if key:
        items[:] = [x for x in items if (_fp_key(x) or (tuple(x.split()[:2]) if x.startswith("network") else None))
                    != key]
    if line not in items:
        items.append(line)


def edge_handle(s, mode, ctx):
    from app.rollback import FP_OPENER, _vlan_ids
    st, m = EDGE_STATE, mode[0]
    if m == "user":
        if s == "enable":
            mode[0] = "enpw"
            return ""
        return EDGE_VERSION if s == "show version" else ("" if s.startswith("terminal") else INVALID)
    if m == "exec":
        if s.startswith("terminal") or s == "enable" or s == "exit":
            return ""
        if s == "show running-config":
            return edge_render()
        if s == "show version":
            return EDGE_VERSION
        if s == "show interfaces status all":  # puertos de la ficha (FastPath): 0/N del frente, 3/N LAG
            return ("\n                                    Link    Physical    Physical    Media       Flow\n"
                    "Port       Name                     State   Mode        Status      Type        Control     VLAN\n"
                    "---------  -----------------------  ------  ----------  ----------  ----------  ----------  ----------\n"
                    "0/1        Servidor                 Up      Auto        1000 Full   Copper      Inactive    1\n"
                    "0/2                                 Down    Auto                    Copper      Inactive    1\n"
                    "0/17                                Up      Auto        1000 Full   Fiber       Inactive    Trunk\n"
                    "0/18                                Down    Auto                    Unknown     Inactive    1\n"
                    "3/1                                 Down\n")
        if s == "configure":
            mode[0] = "config"
            return ""
        if s == "vlan database":
            mode[0] = "vlandb"
            return ""
        if s == "write memory":
            return "Configuration Saved!"
        if s.startswith("network ") or s.startswith("no network "):
            edge_apply(st["exec"], s)
            return ""
        return INVALID
    if m == "vlandb":
        if s == "exit":
            mode[0] = "exec"
            return ""
        if v := re.fullmatch(r"(no )?vlan ([\d,\-]+)", s):
            ids = _vlan_ids(v.group(2))
            if v.group(1):
                st["vlans"] -= ids
                st["vlan"] = [x for x in st["vlan"] if int(x.split()[2]) not in ids]
                for children in st["blocks"].values():  # al borrar una VLAN, los puertos dejan de usarla
                    children[:] = [c for c in children if not (
                        (m := re.fullmatch(r"vlan (?:participation (?:include|exclude)|tagging) ([\d,\-]+)", c))
                        and _vlan_ids(m.group(1)) <= ids)]
            else:
                st["vlans"] |= ids
            return ""
        if re.match(r"(no )?vlan name \d+", s):
            edge_apply(st["vlan"], s)
            return ""
        return INVALID
    if m == "config":
        if s == "exit":
            mode[0] = "exec"
            return ""
        if s.startswith(("vlan database", "network ", "configure", "show running")):
            return INVALID  # comandos de modo privilegiado: el equipo real los rechaza aquí
        if s.startswith("no ") and s[3:] in st["blocks"]:
            del st["blocks"][s[3:]]
            return ""
        if FP_OPENER.match(s):
            st["blocks"].setdefault(s, [])
            mode[0], ctx[0] = "sub", s
            return ""
        edge_apply(st["top"], s)
        return ""
    if m == "sub":
        if s == "exit":
            mode[0], ctx[0] = "config", None
            return ""
        if s.startswith(("vlan database", "network ", "interface ")):
            return INVALID
        if v := re.fullmatch(r"vlan (participation (?:include|exclude|auto)|tagging) ([\d,\-]+)", s):
            if not _vlan_ids(v.group(2)) <= st["vlans"]:  # como el equipo real: la VLAN debe existir
                what = "participation" if v.group(1).startswith("participation") else "tagging"
                return f"Failed to set VLAN {what}. Please refer system log for more information."
        edge_apply(st["blocks"][ctx[0]], s)
        return ""
    return INVALID


# --------------------------------------------------------------------------- pfSense / OPNsense / Alpine / EXOS
FW_MENU = {
    "pfsense": ("*** Welcome to pfSense 2.7.2-RELEASE (amd64) on pfSense ***\n\n"
                " WAN (wan)       -> vtnet0     -> v4/DHCP4: 10.0.0.150/24\n"
                " LAN (lan)       -> vtnet1     -> v4: 192.168.1.1/24\n\n"
                " 0) Logout (SSH only)                  9) pfTop\n"
                " 1) Assign Interfaces                 10) Filter Logs\n"
                " 7) Ping host                         15) Restore recent configuration\n"
                " 8) Shell                             16) Restart PHP-FPM\n\n"),
    "opnsense": ("----------------------------------------------\n"
                 "|      Hello, this is OPNsense 24.7          |\n"
                 "----------------------------------------------\n\n"
                 "  0) Logout                              7) Ping host\n"
                 "  1) Assign interfaces                   8) Shell\n"
                 "  2) Set interface IP address            9) pfTop\n\n"),
}
FW_SHELL_PROMPT = {"pfsense": "[2.7.2-RELEASE][admin@pfSense.home.arpa]/root: ", "opnsense": "root@OPNsense:~ # ",
                   "alpine": "alpine:~# ", "airos": "XW.v6.3.11# "}
FW_CONFIG = {
    "pfsense": ("/cf/conf/config.xml", '<?xml version="1.0"?>\n<pfsense>\n\t<version>23.9</version>\n'
                "\t<system>\n\t\t<hostname>pfSense</hostname>\n\t\t<domain>home.arpa</domain>\n\t</system>\n"
                "\t<interfaces>\n\t\t<wan>\n\t\t\t<if>vtnet0</if>\n\t\t\t<ipaddr>dhcp</ipaddr>\n\t\t</wan>\n"
                "\t</interfaces>\n</pfsense>"),
    "opnsense": ("/conf/config.xml", '<?xml version="1.0"?>\n<opnsense>\n\t<system>\n'
                 "\t\t<hostname>OPNsense</hostname>\n\t\t<domain>localdomain</domain>\n\t</system>\n</opnsense>"),
}
FW_INVENTORY = {
    "pfsense": "Version: 2.7.2-RELEASE\nModel: Netgate 6100\nSerial: 2134567890",
    "opnsense": "Version: 24.7.5_1\nModel: DEC740\nSerial: DEC7401234",
    "alpine": "Version: 3.20.3\nModel: Standard PC (Q35 + ICH9, 2009)\nSerial: ",
}
# Ubiquiti airMAX M (airOS 6): toda la configuración en /tmp/system.cfg
AIROS_CFG = ("aaa.1.status=enabled\naaa.status=disabled\nbridge.1.devname=br0\nbridge.status=enabled\n"
             "netconf.1.devname=eth0\nnetconf.1.ip=192.168.1.20\nnetconf.1.netmask=255.255.255.0\n"
             "radio.1.freq=5745\nradio.1.mode=managed\nresolv.host.1.name=NS-Torre-Norte\n"
             "users.1.name=ubnt\nusers.1.password=$1$Mn4yIetm$Pp9qKnHgV0oT3hB2Wp1kq.\n"
             "wireless.1.ssid=Enlace-Norte\nwireless.1.security.type=wpa2")
AIROS_INVENTORY = "XW.v6.3.11\nboard.sysid=0xe866\nboard.name=NanoStation loco M5\nboard.shortname=LM5\n"
ALPINE_BACKUP = ("### /etc/hostname\nalpine\n### /etc/network/interfaces\nauto lo\niface lo inet loopback\n"
                 "auto eth0\niface eth0 inet dhcp\n### /etc/apk/world\nalpine-base\nopenssh\n"
                 "### servicio:                 sshd | default\n"
                 "# Generated by iptables-save v1.8.10 on Sun Oct 18 12:00:00 2026\n*filter\n:INPUT ACCEPT [0:0]\nCOMMIT")
EXOS_VERSION = """Switch         : PN:1N2039    SIME6B7-0000 Rev 01 BootROM: 1.2
PSUCTRL-1      : PN:MEAD      SN:MD1      Rev 01 BootROM: 2.1
PSU-1      : Internal PSU-2 PN:1N2039 SN:12345

Image   : ExtremeXOS version 33.6.1.14 by release-manager
          on Tue Mar 24 02:04:32 PM UTC 2026
BootROM : 1.2"""
EXOS_SWITCH = """SysName:          EXOS-VM
SysLocation:
System Type:      EXOS-VM
System Mode:      Standalone"""
EXOS_CONFIG = """#
# Module vlan configuration.
#
configure vlan default delete ports all
create vlan "Datos"
configure vlan Datos tag 10
#
# Module devmgr configuration.
#
configure snmp sysName "EXOS-VM\""""


def line_session(ch, t):
    """Sesión de línea para los equipos con shell (eco de cada línea, prompt variable)."""
    kind = SHELL_DEV or "exos"
    st = {"mode": "menu" if kind in FW_MENU else "sh",
          "prompt": "EXOS-VM.1 # " if EXOS else FW_SHELL_PROMPT.get(kind, "# "), "n": 1}

    def prompt():
        return "Enter an option: " if st["mode"] == "menu" else st["prompt"]

    ch.send((FW_MENU[kind].replace("\n", "\r\n") if st["mode"] == "menu" else "\r\n") + prompt())
    buf, prev = "", ""
    while True:
        data = ch.recv(1024)
        if not data:
            break
        for c in data.decode(errors="ignore"):
            last, prev = prev, c
            if c not in "\r\n":
                buf += c
                continue
            if c == "\n" and last == "\r":
                continue
            line, buf = buf, ""
            ch.send(line + "\r\n")
            out = shell_handle(line.strip(), st, kind)
            if out is None:  # exit
                t.close()
                return
            if out:
                ch.send(out.replace("\n", "\r\n") + "\r\n")
            if st["mode"] == "menu":
                ch.send("\r\n" + FW_MENU[kind].replace("\n", "\r\n"))
            ch.send(prompt())
    t.close()


def shell_handle(s, st, kind):
    if st["mode"] == "menu":
        if s == "8":
            st["mode"] = "sh"
        elif s == "0":
            return None
        return ""
    if not s:
        return ""
    if s in ("exit", "logout"):
        return None
    if EXOS:
        st["n"] += 1
        st["prompt"] = f"EXOS-VM.{st['n']} # "
        return {"disable clipaging": "", "disable cli prompting": "", "show version": EXOS_VERSION,
                "show switch": EXOS_SWITCH, "show configuration": EXOS_CONFIG}.get(
            s, "\n% Invalid input detected at '^' marker.")
    if s == "exec /bin/sh":
        st["prompt"] = "# "
        return ""
    if s.startswith("PS1="):
        st["prompt"] = "".join(re.findall(r"'([^']*)'", s.split(";")[0]))
        return ""
    if kind in FW_CONFIG and s == "cat " + FW_CONFIG[kind][0]:
        return FW_CONFIG[kind][1]
    if "kenv" in s or "alpine-release" in s:
        return FW_INVENTORY[kind]
    if kind == "alpine" and s.startswith("for f in"):
        return ALPINE_BACKUP
    if kind == "airos" and s == "cat /tmp/system.cfg":
        return AIROS_CFG
    if kind == "airos" and s == "cat /etc/version; cat /etc/board.info":
        return AIROS_INVENTORY
    if kind == "airos" and s == "ls -l /tmp/fwupdate.bin":  # imagen copiada por SCP (actualización de firmware)
        data = FILES.get("/tmp/fwupdate.bin")
        return (f"-rw-r--r--    1 ubnt     admin    {len(data)} Oct 27 12:00 /tmp/fwupdate.bin" if data is not None
                else "ls: /tmp/fwupdate.bin: No such file or directory")
    if kind == "airos" and s == "fwupdate -m":
        FILES["grabado"] = FILES.get("/tmp/fwupdate.bin", b"")
        return "Writing 'kernel' to /dev/mtd2(kernel) ... [%100]\nDone, rebooting..."
    return f"sh: {s.split()[0]}: not found"


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
