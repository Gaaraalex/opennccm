"""Drivers de Netmiko propios para sistemas basados en un shell Unix que Netmiko no trae.

- pfSense y OPNsense: al entrar por SSH como admin/root muestran un menú de consola numerado
  («Enter an option:»). Se elige «8) Shell», se cambia a /bin/sh y se fija un prompt conocido, porque el
  prompt de tcsh/csh de estos sistemas («[2.7.2-RELEASE][admin@pfSense]/root: ») no termina en $ ni #.
  Un usuario con shell propio (sin menú) entra igual: se salta el paso del menú.
- Alpine Linux: el shell es ash (BusyBox); sirve el driver de Linux tal cual.
- Ubuntu Server / Debian: bash; también el driver de Linux.

app.drivers llama a register() para que Netmiko los reconozca como cualquier otro device_type.
"""
from __future__ import annotations

import re
import time

from netmiko.ssh_dispatcher import CLASS_MAPPER, platforms
from netmiko.linux.linux_ssh import LinuxSSH

PROMPT = "ncm# "
MENU = re.compile(r"Enter an option:\s*$")
SHELL = re.compile(r"[#$%>:]\s*$")


class FreeBSDFirewallSSH(LinuxSSH):
    """pfSense / OPNsense."""

    def _read_until(self, pattern: re.Pattern, timeout: float) -> str:
        out, end = "", time.time() + timeout
        while time.time() < end:
            out += self.read_channel()
            if pattern.search(out):
                return out
            time.sleep(0.2)
        return out

    def session_preparation(self) -> None:
        self.ansi_escape_codes = True
        first = self._read_until(re.compile(r"(Enter an option:|[#$%>:])\s*$"), 25)
        if MENU.search(first):
            self.write_channel("8" + self.RETURN)          # 8) Shell
            self._read_until(SHELL, 15)
        # sh con un prompt fijo; se escribe partido para que el eco del comando no parezca el prompt
        self.write_channel("exec /bin/sh" + self.RETURN)
        time.sleep(0.5)
        self.write_channel("PS1='ncm''# '; export PS1; unset HISTFILE" + self.RETURN)
        self._read_until(re.compile(re.escape(PROMPT.strip()) + r"\s*$"), 15)
        self.clear_buffer()
        self.set_base_prompt()


DRIVERS = {"pfsense": FreeBSDFirewallSSH, "opnsense": FreeBSDFirewallSSH, "alpine_linux": LinuxSSH,
           "ubuntu_server": LinuxSSH}


def register() -> None:
    for key, cls in DRIVERS.items():
        if key not in CLASS_MAPPER:
            CLASS_MAPPER[key] = cls
            platforms.append(key)  # ConnectHandler valida device_type contra esta lista
