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
from netmiko.exceptions import ReadTimeout
from netmiko.mikrotik.mikrotik_ssh import MikrotikRouterOsSSH

PROMPT = "ncm# "
MENU = re.compile(r"Enter an option:\s*$")
SHELL = re.compile(r"[#$%>:]\s*$")
# secuencias OSC de la terminal; Ubuntu 26.04 (systemd 257+) manda «ESC ]3008;start=<uuid>;...» con un
# identificador nuevo en cada orden, que haría ver un cambio en cada respaldo
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")


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


class UbuntuServerSSH(LinuxSSH):
    """Ubuntu Server / Debian: la orden de respaldo es larga y la terminal la parte en varias líneas, así que Netmiko
    no reconoce su eco. Cada orden va entre dos marcas que solo salen así en la salida real (en el eco van partidas
    por comillas) y se toma lo que queda entre ellas."""

    BEGIN, END = "NCM-INICIO", "NCM-FIN"

    def send_command(self, command_string, *args, **kwargs):
        wrapped = f'echo {self.BEGIN[:7]}""{self.BEGIN[7:]}; {command_string}; echo {self.END[:6]}""{self.END[6:]}'
        kwargs.update(cmd_verify=False, strip_command=False, strip_prompt=True,
                      expect_string=rf"{self.END}\r?\n[^\n]*[#$]\s*$")
        out = super().send_command(wrapped, *args, **kwargs)
        out = OSC.sub("", out)
        lines = [line.rstrip() for line in out.replace("\r\n", "\n").replace("\r", "").split("\n")]
        if self.BEGIN in lines and self.END in lines:
            start = len(lines) - 1 - lines[::-1].index(self.BEGIN)
            lines = lines[start + 1:lines.index(self.END, start)]
        return "\n".join(lines)


class RouterOSSSH(MikrotikRouterOsSSH):
    """MikroTik RouterOS. Desde RouterOS 7.25, al entrar pregunta al terminal su tamaño (ESC[6n) y no muestra nada más
    hasta recibir una tecla, aunque el usuario lleve «+t» (sin detección de terminal): Netmiko esperaba el prompt y
    agotaba el tiempo. Un Enter lo desbloquea; en versiones anteriores solo añade un prompt vacío."""

    def special_login_handler(self, delay_factor: float = 1.0) -> None:
        """Como el de Netmiko (licencia, aviso sin licencia, configuración por defecto, contraseña nueva) más la
        pregunta del tamaño del terminal de RouterOS 7.25, que se contesta con un Enter."""
        notices = {'Please press "Enter" to continue!': self.RETURN, "Do you want to see the software license": "n",
                   'remove this default configuration type "r"': self.RETURN, "new password>": "\x03",
                   "\x1b[6n": self.RETURN}
        pattern = "(?:" + "|".join([self.prompt_pattern] + [re.escape(k) for k in notices]) + ")"
        for _ in range(15):
            try:
                data = self.read_until_pattern(pattern=pattern, re_flags=re.I, read_timeout=5)
            except ReadTimeout:
                self.write_channel(self.RETURN)  # RouterOS 7.25 espera una tecla antes de mostrar nada
                continue
            for text, answer in notices.items():
                if text.lower() in data.lower():
                    self.write_channel(answer)
                    break
            else:
                if re.search(self.prompt_pattern, data, re.I):
                    return
        raise ValueError("Unexpected output in special_login_handler")


DRIVERS = {"pfsense": FreeBSDFirewallSSH, "opnsense": FreeBSDFirewallSSH, "alpine_linux": LinuxSSH,
           "ubuntu_server": UbuntuServerSSH,
           # Ubiquiti: airOS, airFiber y AP UniFi son BusyBox (prompt «XW.v6.3.11#»); el USG es EdgeOS
           "ubiquiti_airos": LinuxSSH, "ubiquiti_airfiber": LinuxSSH, "ubiquiti_unifi_ap": LinuxSSH,
           "ubiquiti_usg": CLASS_MAPPER["ubiquiti_edgerouter"]}


# sustituyen al driver de Netmiko para ese device_type
OVERRIDES = {"mikrotik_routeros": RouterOSSSH, "mikrotik_routeros_ssh": RouterOSSSH}


def register() -> None:
    for key, cls in DRIVERS.items():
        if key not in CLASS_MAPPER:
            CLASS_MAPPER[key] = cls
            platforms.append(key)  # ConnectHandler valida device_type contra esta lista
    CLASS_MAPPER.update(OVERRIDES)
