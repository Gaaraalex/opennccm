"""Motivo corto y traducido de un error de conexión o de respaldo (Panel «Necesita tu atención», chat del asistente).

Los mensajes de netmiko y paramiko son largos y en inglés («TCP connection to device failed. Common causes of this
problem are…»). Aquí se reconocen los habituales y se resumen en pocas palabras, en el idioma del usuario; el mensaje
completo sigue en la ficha del equipo (Eventos).
"""
from __future__ import annotations

import re

from .i18n import N_, _

RULES = [
    (r"host ?key|known_hosts|REMOTE HOST IDENTIFICATION|BadHostKey", N_("cambió la clave SSH del equipo")),
    (r"authentication (failed|rejected)|Authentication failed|auth.*fail|Bad authentication|incorrect password|"
     r"rechaz.*contraseña|Permission denied|Usuario o contraseña", N_("credencial rechazada")),
    (r"enable mode|modo privilegiado|enable / secret|privileged|Password:\s*$", N_("falta la contraseña enable")),
    (r"no tiene credencial|no credential", N_("sin credencial asignada")),
    (r"Pattern not detected|pattern not found|incomplete|truncated|incompleto|--More--", N_("respaldo incompleto")),
    (r"timed? ?out|timeout|tiempo de espera", N_("tiempo agotado")),
    (r"TCP connection to device failed|Connection refused|No route to host|Network is unreachable|Unable to connect|"
     r"Errno 1(10|11|13)|Errno 10060|Errno 10061|No existing session|Error reading SSH protocol banner", N_("sin respuesta por SSH")),
    (r"Name or service not known|getaddrinfo|nodename nor servname", N_("no se pudo resolver el nombre")),
    (r"Telnet no soportado|telnet", N_("Telnet no soportado")),
    (r"Invalid input|Unknown command|bad command|syntax error|Unrecognized command", N_("el equipo no reconoce el comando de respaldo")),
]
_COMPILED = [(re.compile(p, re.I | re.M), msg) for p, msg in RULES]


def short_error(text: str | None) -> str:
    """«sin respuesta por SSH», «credencial rechazada»… o «falló el respaldo» si no se reconoce."""
    t = text or ""
    for pat, msg in _COMPILED:
        if pat.search(t):
            return _(msg)
    return _("falló el respaldo")
