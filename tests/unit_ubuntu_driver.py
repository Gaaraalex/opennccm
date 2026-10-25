"""Driver de Ubuntu Server / Debian: la salida se toma entre las marcas aunque el eco de la orden llegue partido."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from netmiko.linux.linux_ssh import LinuxSSH  # noqa: E402

from app.shell_drivers import UbuntuServerSSH  # noqa: E402

FAILS = []


def check(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


sent = {}


def fake(self, command_string, *args, **kwargs):
    sent.update(cmd=command_string, kw=kwargs)
    # eco partido por la terminal (con « \r»), salida real y prompt
    echo = command_string[:300] + " \r" + command_string[300:]
    return f"{echo}\r\nNCM-INICIO\r\n### /etc/hostname\r\nservidor\r\nNCM-FIN\r\nncm@servidor:~$ "


LinuxSSH.send_command = fake
drv = UbuntuServerSSH.__new__(UbuntuServerSSH)
out = drv.send_command("cat /etc/hostname " + "x" * 1200, read_timeout=180)
check("solo queda la salida entre las marcas", out == "### /etc/hostname\nservidor")
check("el eco no contiene las marcas tal cual", "NCM-INICIO" not in sent["cmd"] and "NCM-FIN" not in sent["cmd"])
check("sin verificar el eco", sent["kw"]["cmd_verify"] is False and sent["kw"]["read_timeout"] == 180)
check("espera la marca final y el prompt",
      re.search(sent["kw"]["expect_string"], "NCM-FIN\r\nncm@servidor:~$ ") is not None
      and re.search(sent["kw"]["expect_string"], sent["cmd"] + "\r\n") is None)
osc = "\x1b]3008;start=3be8aaf5;type=command;user=ncm\x1b\\"


def fake_osc(self, command_string, *args, **kwargs):
    return (f"{command_string}\r\n{osc}NCM-INICIO\r\nservidor\r\nNCM-FIN\r\n"
            "\x1b]3008;end=3be8aaf5;exit=success\x07ncm@servidor:~$ ")


LinuxSSH.send_command = fake_osc
out = drv.send_command("cat /etc/hostname")
check("sin las secuencias OSC 3008 de Ubuntu 26.04 (cambian en cada orden)", out == "servidor")
LinuxSSH.send_command = fake
check("una sola línea (sin eco adelantado a mitad de la salida)", "\n" not in sent["cmd"])

if FAILS:
    print(f"\n{len(FAILS)} FALLOS")
    sys.exit(1)
print("\nTodo OK")
