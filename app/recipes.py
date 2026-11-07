"""Recetas incluidas en OpenNCCM (Operar › Consola › Recetas): guías paso a paso con sus comandos.

Cada receta se revisó contra la documentación oficial del fabricante, que va en «doc». Las variables se escriben
{{nombre}} en los comandos y se sustituyen con lo que el usuario escribe al abrir la receta. Un paso con «warn» se
muestra en ámbar («Ojo») con su advertencia; el último paso «check» es el de comprobar.
Las recetas de cada usuario se guardan en la tabla «recipes» con el mismo formato (y pueden partir de una de estas).
"""
from __future__ import annotations

from .i18n import N_

CATEGORIES = {"sistema": N_("Sistema"), "seguridad": N_("Seguridad"), "interfaces": N_("Interfaces y VLAN"),
              "servicios": N_("Servicios"), "diagnostico": N_("Diagnóstico")}

BUILTIN = [
    {
        "key": "ios-ssh", "vendor": "Cisco", "os": "ios", "os_label": "Cisco IOS / IOS-XE", "cat": "seguridad",
        "name": N_("Configurar SSH versión 2 y dejar solo SSH en las líneas vty (Cisco IOS)"),
        "intro": N_("Crea un usuario local, genera la clave RSA del equipo, activa SSH versión 2 y deja las líneas vty solo con SSH. Necesita una imagen con criptografía (k9)."),
        "doc": "https://www.cisco.com/c/en/us/support/docs/security-vpn/secure-shell-ssh/4145-ssh.html",
        "vars": [{"n": "hostname", "l": N_("Nombre del equipo"), "v": "R1"},
                 {"n": "dominio", "l": N_("Dominio"), "v": "empresa.local"},
                 {"n": "usuario", "l": N_("Usuario local"), "v": "admin"},
                 {"n": "clave", "l": N_("Contraseña del usuario"), "v": ""}],
        "steps": [
            {"t": N_("Entrar en modo configuración."), "c": "configure terminal"},
            {"t": N_("La clave RSA se nombra con el nombre del equipo y el dominio: los dos tienen que estar."),
             "c": "hostname {{hostname}}\nip domain name {{dominio}}"},
            {"t": N_("Usuario local para entrar por SSH."), "c": "username {{usuario}} privilege 15 secret {{clave}}"},
            {"t": N_("Generar la clave RSA del equipo. Con 4096 bits puede tardar un par de minutos."),
             "c": "crypto key generate rsa general-keys modulus 4096", "warn": True},
            {"t": N_("Usar solo la versión 2 del protocolo."), "c": "ip ssh version 2"},
            {"t": N_("Líneas vty: usuarios locales y solo SSH. Si está conectado por Telnet, esa sesión se cortará: compruebe antes que entra por SSH."),
             "c": "line vty 0 4\nlogin local\ntransport input ssh\nend", "warn": True},
            {"t": N_("Comprobar: debe decir que SSH está activado en la versión 2.0."), "c": "show ip ssh", "check": True},
        ],
    },
    {
        "key": "ros-services", "vendor": "MikroTik", "os": "ros", "os_label": "RouterOS 7", "cat": "seguridad",
        "name": N_("Limitar SSH y Winbox a la red de gestión y apagar Telnet y FTP (RouterOS)"),
        "intro": N_("Los servicios de administración de RouterOS aceptan conexiones desde cualquier dirección. Esta receta los limita a la red de gestión y apaga los que mandan la contraseña sin cifrar."),
        "doc": "https://manual.mikrotik.com/docs/7.25/system-information-and-utilities/services/",
        "vars": [{"n": "red", "l": N_("Red de gestión"), "v": "192.168.88.0/24"}],
        "steps": [
            {"t": N_("Ver los servicios y desde dónde se aceptan."), "c": "/ip service print"},
            {"t": N_("Incluya la dirección desde la que administra el equipo: las conexiones nuevas desde fuera de la lista se rechazan, también la suya si queda fuera."),
             "c": "/ip service set ssh,winbox available-from={{red}}", "warn": True},
            {"t": N_("Apagar Telnet y FTP, que mandan la contraseña sin cifrar."), "c": "/ip service disable telnet,ftp"},
            {"t": N_("Comprobar: Telnet y FTP con la marca X (deshabilitados) y SSH y Winbox con la red de gestión."),
             "c": "/ip service print", "check": True},
        ],
    },
    {
        "key": "ros-vlan", "vendor": "MikroTik", "os": "ros", "os_label": "RouterOS 7", "cat": "interfaces",
        "name": N_("Crear una VLAN con su dirección IP (RouterOS)"),
        "intro": N_("Crea una interfaz VLAN sobre una interfaz física o un bridge y le pone una dirección IP."),
        "doc": "https://manual.mikrotik.com/docs/bridging-and-switching/vlan",
        "vars": [{"n": "nombre", "l": N_("Nombre de la VLAN"), "v": "vlan20"}, {"n": "vlan", "l": N_("Número de VLAN"), "v": "20"},
                 {"n": "interfaz", "l": N_("Interfaz troncal"), "v": "ether2"},
                 {"n": "ip", "l": N_("Dirección IP con máscara"), "v": "192.168.20.1/24"}],
        "steps": [
            {"t": N_("Crear la interfaz VLAN. El número tiene que ser el mismo en todos los equipos de esa VLAN."),
             "c": "/interface vlan add name={{nombre}} vlan-id={{vlan}} interface={{interfaz}}"},
            {"t": N_("Ponerle la dirección IP."), "c": "/ip address add address={{ip}} interface={{nombre}}"},
            {"t": N_("Comprobar la VLAN y su dirección."), "c": "/interface vlan print\n/ip address print", "check": True},
        ],
    },
    {
        "key": "exos-ssh", "vendor": "Extreme Networks", "os": "exos", "os_label": "EXOS / Switch Engine", "cat": "seguridad",
        "name": N_("Configurar SSH en un switch Extreme Networks (EXOS) y apagar Telnet"),
        "intro": N_("Activa el servidor SSH (si no hay clave del equipo, se genera) y apaga Telnet, que viene activado de fábrica."),
        "doc": "https://documentation.extremenetworks.com/ExtremeXOS%20v33.1.1%20Command%20References/GUID-7C156906-2861-4D1D-8A80-BDFF6AD4FE65.shtml",
        "vars": [],
        "steps": [
            {"t": N_("Activar SSH. Si el equipo aún no tiene clave, la genera, y puede tardar varios minutos sin que se pueda cancelar."), "c": "enable ssh2", "warn": True},
            {"t": N_("Comprobar que SSH quedó activado y con una clave válida antes de seguir."), "c": "show management"},
            {"t": N_("Apagar Telnet. Si entró por Telnet, compruebe antes que puede entrar por SSH."), "c": "disable telnet",
             "warn": True},
            {"t": N_("Comprobar: Telnet deshabilitado y SSH activado."), "c": "show management", "check": True},
        ],
    },
    {
        "key": "ios-flap", "vendor": "Cisco", "os": "ios", "os_label": "Cisco IOS / IOS-XE", "cat": "diagnostico",
        "name": N_("Revisar un puerto que sube y baja (Cisco IOS)"),
        "intro": N_("Solo consultas: estado del puerto, sus errores y lo que dice el registro. Los errores CRC suelen venir de un cable o un conector en mal estado."),
        "doc": "", "vars": [{"n": "puerto", "l": N_("Puerto"), "v": "GigabitEthernet0/1"}],
        "steps": [
            {"t": N_("Estado del puerto, última entrada y salida y contadores."), "c": "show interfaces {{puerto}}"},
            {"t": N_("Solo las líneas de errores."), "c": "show interfaces {{puerto}} | include errors|CRC|resets"},
            {"t": N_("Qué registró el equipo de ese puerto."), "c": "show logging | include {{puerto}}"},
            {"t": N_("Comprobar el estado actual."), "c": "show interfaces {{puerto}} | include line protocol", "check": True},
        ],
    },
    {
        "key": "ros-flap", "vendor": "MikroTik", "os": "ros", "os_label": "RouterOS 7", "cat": "diagnostico",
        "name": N_("Revisar un puerto que sube y baja (RouterOS)"),
        "intro": N_("Solo consultas: el registro de interfaces y el tráfico del puerto en este momento."),
        "doc": "", "vars": [{"n": "puerto", "l": N_("Puerto"), "v": "ether4"}],
        "steps": [
            {"t": N_("Qué registró el equipo de las interfaces."), "c": '/log print where topics~"interface"'},
            {"t": N_("Tráfico del puerto ahora."), "c": "/interface monitor-traffic {{puerto}} once"},
            {"t": N_("Comprobar: la R indica que tiene enlace."), "c": "/interface print where name={{puerto}}", "check": True},
        ],
    },
]
from .recipes_more import MORE  # noqa: E402  (las tareas comunes de Cisco, MikroTik, EXOS y VOSS)

from .recipes_ubnt_zte import UBNT_ZTE  # noqa: E402  (Ubiquiti y ZTE)

from .recipes_major import MAJOR  # noqa: E402  (Huawei, Comware, OLT Huawei, Junos, NX-OS, EOS, Linux)

BUILTIN += MORE + UBNT_ZTE + MAJOR
BY_KEY = {r["key"]: r for r in BUILTIN}


def fill(recipe: dict, values: dict) -> list[dict]:
    """Los pasos con las variables ya sustituidas."""
    out = []
    for s in recipe.get("steps", []):
        c = s.get("c", "")
        for v in recipe.get("vars", []):
            c = c.replace("{{" + v["n"] + "}}", str(values.get(v["n"], v.get("v", ""))))
        out.append({**s, "c": c})
    return out
