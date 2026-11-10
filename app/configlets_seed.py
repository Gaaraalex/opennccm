"""Configlets y scripts incluidos desde 2.34: diez para cada una de las plataformas probadas a fondo (MikroTik RouterOS,
Extreme EXOS, Extreme VOSS / Fabric Engine, Ubiquiti EdgeSwitch y WatchGuard Fireware).

Los de configuración usan los mismos comandos que las recetas verificadas de esas plataformas. Llegan a las instalaciones
nuevas (opennccm seed) y a las que ya existen al arrancar, una sola vez: si alguien borra uno, no vuelve a aparecer.
Todavía no tienen el sello «Verificada»: se pone cuando se prueben en un equipo (app.verified)."""
from __future__ import annotations

import json

from sqlalchemy import select

ROS, EXOS, VOSS, FP, WG = "mikrotik_routeros", "extreme_exos", "extreme_vsp", "ubiquiti_edgeswitch", "watchguard_fireware"

# (nombre, tipo, filtro de tipo de equipo, descripción, cuerpo con variables {{ variable }})
CONFIGLETS_234 = [
    # ------------------------------------------------------------------ MikroTik RouterOS
    ("Cerrar los servicios de gestión inseguros (RouterOS)", "config", ROS,
     "Apaga Telnet, FTP, la web sin cifrar y la API, y deja SSH y Winbox solo para la red de gestión.",
     "/ip service disable telnet,ftp,www,api\n/ip service set ssh,winbox address={{ red_gestion }}"),
    ("Enviar el syslog a OpenNCCM (RouterOS)", "config", ROS,
     "Manda los registros del sistema a OpenNCCM para detectar cambios al momento.",
     "/system logging action add name=opennccm target=remote remote={{ servidor_ncm }} remote-port=514\n"
     "/system logging add topics=system action=opennccm"),
    ("Configurar NTP (RouterOS 7)", "config", ROS,
     "Hora correcta en los registros y los respaldos (en RouterOS 6 el parámetro es primary-ntp).",
     "/system ntp client set enabled=yes servers={{ ntp_server }}"),
    ("Configurar los servidores DNS (RouterOS)", "config", ROS,
     "DNS del equipo, sin responder consultas de otros equipos.",
     "/ip dns set servers={{ dns }} allow-remote-requests=no"),
    ("Crear un usuario administrador (RouterOS)", "config", ROS,
     "Usuario nuevo con permisos completos.",
     "/user add name={{ usuario }} group=full password={{ clave }}"),
    ("Crear una VLAN con su dirección IP (RouterOS)", "config", ROS,
     "Interfaz VLAN sobre un puerto y su dirección IP (por ejemplo 192.168.20.1/24).",
     "/interface vlan add name={{ nombre }} vlan-id={{ vlan_id }} interface={{ interfaz }}\n"
     "/ip address add address={{ ip }} interface={{ nombre }}"),
    ("Agregar una ruta estática (RouterOS)", "config", ROS,
     "Ruta hacia una red (por ejemplo 172.16.0.0/16) por una puerta de enlace.",
     "/ip route add dst-address={{ red }} gateway={{ gateway }}"),
    ("Habilitar SNMP de solo lectura (RouterOS)", "config", ROS,
     "Comunidad SNMP limitada a la red de gestión.",
     "/snmp community add name={{ comunidad }} addresses={{ red_gestion }}\n/snmp set enabled=yes"),
    ("Estado general del equipo (RouterOS)", "exec", ROS,
     "Recursos, nombre, interfaces, direcciones y rutas.",
     "/system resource print\n/system identity print\n/interface print\n/ip address print\n/ip route print"),
    ("Ver vecinos y tabla ARP (RouterOS)", "exec", ROS,
     "Equipos vecinos (MNDP, CDP, LLDP) y direcciones IP y MAC aprendidas.",
     "/ip neighbor print\n/ip arp print"),
    # ------------------------------------------------------------------ Extreme EXOS
    ("Activar SSH y apagar Telnet y HTTP (EXOS)", "config", EXOS,
     "Gestión solo por SSH: apaga Telnet y la web sin cifrar.",
     "enable ssh2\ndisable telnet\ndisable web http"),
    ("Nombre, ubicación y contacto del switch (EXOS)", "config", EXOS,
     "Lo que se ve en SNMP, en el prompt y en el inventario.",
     "configure snmp sysName {{ nombre }}\nconfigure snmp sysLocation \"{{ ubicacion }}\"\nconfigure snmp sysContact \"{{ contacto }}\""),
    ("Enviar el syslog a OpenNCCM (EXOS)", "config", EXOS,
     "Registros hacia OpenNCCM por el router virtual de gestión (normalmente VR-Mgmt).",
     "configure syslog add {{ servidor_ncm }}:514 vr {{ vr }} local0\n"
     "enable log target syslog {{ servidor_ncm }}:514 vr {{ vr }} local0\n"
     "configure log target syslog {{ servidor_ncm }}:514 vr {{ vr }} local0 filter DefaultFilter severity notice"),
    ("Sincronizar la hora por SNTP (EXOS)", "config", EXOS,
     "Servidor SNTP por el router virtual de gestión (normalmente VR-Mgmt).",
     "configure sntp-client primary {{ ntp_server }} vr {{ vr }}\nenable sntp-client"),
    ("Habilitar SNMP v2c de solo lectura (EXOS)", "config", EXOS,
     "Comunidad de solo lectura y acceso SNMP encendido.",
     "configure snmp add community readonly {{ comunidad }}\nenable snmp access"),
    ("Crear una VLAN con puertos y dirección IP (EXOS)", "config", EXOS,
     "VLAN con etiqueta, sus puertos (tagged o untagged) y su dirección IP (por ejemplo 192.168.20.2/24).",
     "create vlan {{ nombre }} tag {{ vlan_id }}\nconfigure vlan {{ nombre }} add ports {{ puertos }} {{ modo }}\n"
     "configure vlan {{ nombre }} ipaddress {{ ip }}"),
    ("Crear una cuenta de administrador (EXOS)", "config", EXOS,
     "Cuenta nueva con nivel de administrador.",
     "create account admin {{ usuario }} {{ clave }}"),
    ("Agregar una ruta estática (EXOS)", "config", EXOS,
     "Ruta hacia una red (por ejemplo 172.16.0.0/16) en un router virtual (normalmente VR-Default).",
     "configure iproute add {{ red }} {{ gateway }} vr {{ vr }}"),
    ("Estado general del switch (EXOS)", "exec", EXOS,
     "Datos del switch, versión, puertos y VLAN.",
     "show switch\nshow version\nshow ports no-refresh\nshow vlan"),
    ("Errores de puertos y tabla MAC (EXOS)", "exec", EXOS,
     "Errores de recepción y envío de todos los puertos y las MAC aprendidas.",
     "show ports rxerrors no-refresh\nshow ports txerrors no-refresh\nshow fdb"),
    # ------------------------------------------------------------------ Extreme VOSS / Fabric Engine
    ("Nombre, ubicación y contacto del switch (VOSS / Fabric Engine)", "config", VOSS,
     "Lo que se ve en el prompt, en SNMP y en el inventario.",
     "enable\nconfigure terminal\nsys name {{ nombre }}\nsnmp-server location \"{{ ubicacion }}\"\nsnmp-server contact \"{{ contacto }}\""),
    ("Enviar el syslog a OpenNCCM (VOSS / Fabric Engine)", "config", VOSS,
     "Registros hacia OpenNCCM (número de host libre del 1 al 10).",
     "enable\nconfigure terminal\nsyslog enable\nsyslog host {{ n }}\nsyslog host {{ n }} address {{ servidor_ncm }}\nsyslog host {{ n }} enable"),
    ("Configurar NTP (VOSS / Fabric Engine)", "config", VOSS,
     "Servidor NTP y sincronización encendida.",
     "enable\nconfigure terminal\nntp server {{ ntp_server }}\nntp server {{ ntp_server }} enable\nntp"),
    ("Crear una VLAN y agregarle puertos (VOSS / Fabric Engine)", "config", VOSS,
     "VLAN basada en puertos (por ejemplo puertos 1/7-1/8).",
     "enable\nconfigure terminal\nvlan create {{ vlan_id }} name {{ nombre }} type port-mstprstp 0\nvlan members add {{ vlan_id }} {{ puertos }}"),
    ("Dar una dirección IP a una VLAN (VOSS / Fabric Engine)", "config", VOSS,
     "Interfaz de la VLAN con su dirección (por ejemplo 192.168.20.2/24).",
     "enable\nconfigure terminal\ninterface vlan {{ vlan_id }}\nip address {{ ip }}\nexit"),
    ("Agregar una ruta estática (VOSS / Fabric Engine)", "config", VOSS,
     "Ruta hacia una red con su máscara y la puerta de enlace.",
     "enable\nconfigure terminal\nip route {{ red }} {{ mascara }} {{ gateway }} weight 1"),
    ("Entrar con RADIUS (VOSS / Fabric Engine)", "config", VOSS,
     "Servidor RADIUS para la CLI. Antes de usarlo, confirme que el servidor responde: si no, la CLI tarda en cada orden.",
     "enable\nconfigure terminal\nradius server host {{ servidor }} key {{ clave }} used-by cli\nradius enable"),
    ("Poner un aviso al entrar (VOSS / Fabric Engine)", "config", VOSS,
     "Texto que se muestra antes de iniciar sesión.",
     "enable\nconfigure terminal\nbanner custom\nbanner \"{{ texto }}\""),
    ("Estado general del switch (VOSS / Fabric Engine)", "exec", VOSS,
     "Datos del sistema, puertos, VLAN y rutas.",
     "show sys-info\nshow interfaces gigabitEthernet\nshow vlan basic\nshow ip route"),
    ("Revisar la fábrica SPB (VOSS / Fabric Engine)", "exec", VOSS,
     "Vecinos IS-IS y los servicios de la fábrica.",
     "show isis adjacencies\nshow isis spbm\nshow i-sid"),
    # ------------------------------------------------------------------ Ubiquiti EdgeSwitch
    ("Activar SSH y HTTPS, y apagar Telnet y HTTP (EdgeSwitch)", "config", FP,
     "Gestión solo por canales cifrados, guardada en la configuración de arranque.",
     "enable\nip ssh server enable\nip http secure-server\nno ip telnet server enable\nno ip http server\nwrite memory"),
    ("Enviar el syslog a OpenNCCM (EdgeSwitch)", "config", FP,
     "Registros y comandos de la CLI hacia OpenNCCM.",
     "enable\nconfigure\nlogging host {{ servidor_ncm }} ipv4 514 info\nlogging cli-command\nlogging syslog\nexit\nwrite memory"),
    ("Configurar la hora por SNTP (EdgeSwitch)", "config", FP,
     "Cliente SNTP en modo unicast con su servidor.",
     "enable\nconfigure\nsntp client mode unicast\nsntp server {{ ntp_server }}\nexit\nwrite memory"),
    ("Habilitar SNMP v2c de solo lectura (EdgeSwitch)", "config", FP,
     "Comunidad de solo lectura, ubicación y contacto.",
     "enable\nconfigure\nsnmp-server community {{ comunidad }} ro\nsnmp-server location \"{{ ubicacion }}\"\n"
     "snmp-server contact \"{{ contacto }}\"\nexit\nwrite memory"),
    ("Crear una VLAN con nombre (EdgeSwitch)", "config", FP,
     "VLAN nueva en la base de datos de VLAN.",
     "enable\nvlan database\nvlan {{ vlan_id }}\nvlan name {{ vlan_id }} \"{{ nombre }}\"\nexit\nwrite memory"),
    ("Poner un puerto de acceso en una VLAN (EdgeSwitch)", "config", FP,
     "El puerto queda solo en esa VLAN, sin etiqueta (por ejemplo 0/5).",
     "enable\nconfigure\ninterface {{ puerto }}\nvlan pvid {{ vlan_id }}\nvlan participation include {{ vlan_id }}\n"
     "vlan participation exclude 1\nexit\nexit\nwrite memory"),
    ("Pasar una VLAN etiquetada por un puerto (EdgeSwitch)", "config", FP,
     "Agrega la VLAN con etiqueta a un puerto troncal.",
     "enable\nconfigure\ninterface {{ puerto }}\nvlan participation include {{ vlan_id }}\nvlan tagging {{ vlan_id }}\nexit\nexit\nwrite memory"),
    ("Crear un usuario administrador (EdgeSwitch)", "config", FP,
     "Usuario con nivel 15.",
     "enable\nconfigure\nusername {{ usuario }} password {{ clave }} level 15\nexit\nwrite memory"),
    ("Estado general del switch (EdgeSwitch)", "exec", FP,
     "Datos del sistema, versión y estado de los puertos.",
     "show sysinfo\nshow version\nshow port all"),
    ("Tabla MAC y vecinos LLDP (EdgeSwitch)", "exec", FP,
     "MAC aprendidas en cada puerto y equipos vecinos por LLDP.",
     "show mac-addr-table\nshow lldp remote-device all"),
    # ------------------------------------------------------------------ WatchGuard Fireware
    ("Cambiar el nombre del firewall (WatchGuard Fireware)", "config", WG,
     "Nombre del equipo (sin espacios). Fireware guarda el cambio al momento.",
     "configure\nsystem name {{ nombre }}\nexit"),
    ("Agregar un servidor NTP (WatchGuard Fireware)", "config", WG,
     "Fireware admite hasta 3 servidores NTP: si ya tiene 3, quite uno antes.",
     "configure\nntp enable\nntp server ip {{ ntp_server }}\nexit"),
    ("Estado general del firewall (WatchGuard Fireware)", "exec", WG,
     "Datos del sistema, interfaces, rutas y tabla ARP.",
     "show sysinfo\nshow interface\nshow route\nshow arp"),
    ("Licencias y servicios activos (WatchGuard Fireware)", "exec", WG,
     "Feature key y servicios de seguridad con licencia.",
     "show feature-key\nshow features"),
    ("Conexiones y administradores conectados (WatchGuard Fireware)", "exec", WG,
     "Número de conexiones, administradores con sesión abierta y cuentas.",
     "show connection count\nshow login-user\nshow users"),
    ("Estado de los enlaces WAN (WatchGuard Fireware)", "exec", WG,
     "Multi-WAN y el monitor de enlaces.",
     "show multi-wan\nshow link-monitor"),
    ("Ver el registro reciente (WatchGuard Fireware)", "exec", WG,
     "Las últimas entradas del registro del equipo (por ejemplo 100).",
     "show log-cache tail {{ lineas }}"),
    ("Almacenamiento e historial de actualizaciones (WatchGuard Fireware)", "exec", WG,
     "Espacio en el equipo y actualizaciones de firmas y del sistema.",
     "show sys-storage\nshow update-history"),
    ("Probar la conectividad hacia una dirección (WatchGuard Fireware)", "exec", WG,
     "Ping de 4 paquetes y la ruta hasta el destino.",
     "ping -c 4 {{ destino }}\ntraceroute {{ destino }}"),
    ("Copias de seguridad guardadas en el equipo (WatchGuard Fireware)", "exec", WG,
     "Imágenes de respaldo que guarda el propio Firebox.",
     "show backup-list"),
]


def seed_new_configlets(db) -> int:
    """Agrega los configlets de esta lista que todavía no se ofrecieron en esta instalación (por nombre)."""
    from .db import Configlet, get_setting, set_setting
    try:
        offered = set(json.loads(get_setting("configlets_offered", "[]") or "[]"))
    except ValueError:
        offered = set()
    have = set(db.scalars(select(Configlet.name)))
    n = 0
    for name, kind, flt, desc, body in CONFIGLETS_234:
        if name in offered:
            continue
        if name not in have:
            db.add(Configlet(name=name, kind=kind, device_type_filter=flt, description=desc, body=body))
            n += 1
        offered.add(name)
    db.commit()
    set_setting("configlets_offered", json.dumps(sorted(offered), ensure_ascii=False))
    return n
