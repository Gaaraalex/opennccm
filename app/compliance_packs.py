"""Paquetes de cumplimiento listos para usar: PCI DSS 4.0, HIPAA, SOX, buenas prácticas Cisco IOS y seguridad
básica de Extreme VOSS, switches Ubiquiti, MikroTik RouterOS, Juniper Junos y FortiGate.

Las reglas comprueban la configuración de equipos con sintaxis tipo IOS (Cisco, Arista, Dell, Ruckus…)
y Junos. Son controles técnicos de referencia: el cumplimiento normativo completo requiere además
procesos y evidencias que no dependen del equipo.
"""
IOS_LIKE = r"^(cisco_(ios|xe|nxos|s\d00|ioswlc)|arista|dell_(os6|os9|force10|powerconnect)|ruckus|brocade_netiron|allied|adtran|a10)"

NO_TELNET = (r"^\s*transport input .*telnet", "line vty 0 15\n transport input ssh")
EXEC_TIMEOUT = r"^\s*exec-timeout (0 0|0\s*$|(1[6-9]|[2-9]\d|\d{3,})\b)"

PACKS = {
    "PCI DSS 4.0 (IOS)": (IOS_LIKE, "Controles técnicos de PCI DSS 4.0 aplicables a switches y routers", [
        ("Req 2.2.7 · Sin Telnet en líneas VTY", "must_not_contain", NO_TELNET[0], "critical", NO_TELNET[1]),
        ("Req 2.2.7 · SSH versión 2", "must_contain", r"^ip ssh version 2", "major", "ip ssh version 2"),
        ("Req 2.2.4 · Sin servidor HTTP sin cifrar", "must_not_contain", r"^ip http server", "major",
         "no ip http server"),
        ("Req 2.2.2 · Sin comunidades SNMP por defecto", "must_not_contain",
         r"^snmp-server community (public|private)\b", "critical",
         "no snmp-server community public\nno snmp-server community private"),
        ("Req 8.3.2 · Contraseñas cifradas en la configuración", "must_contain", r"^service password-encryption",
         "major", "service password-encryption"),
        ("Req 8.3.2 · Usar enable secret (no enable password)", "must_not_contain", r"^enable password",
         "critical", None),
        ("Req 8.3.6 · Longitud mínima de contraseña ≥ 12", "must_contain",
         r"^security passwords min-length (1[2-9]|[2-9]\d)", "major", "security passwords min-length 12"),
        ("Req 8.3.4 · Bloqueo tras intentos fallidos", "must_contain", r"^login block-for \d+ attempts \d+ within",
         "major", "login block-for 1800 attempts 10 within 300"),
        ("Req 8.2.8 · Cierre de sesión inactiva ≤ 15 min", "must_not_contain", EXEC_TIMEOUT, "major",
         "line vty 0 15\n exec-timeout 15 0\nline con 0\n exec-timeout 15 0"),
        ("Req 8.2 · AAA habilitado", "must_contain", r"^aaa new-model", "major", None),
        ("Req 1.2.1 · ACL de gestión en VTY", "must_contain", r"^\s*access-class \S+ in", "major", None),
        ("Req 10.2 · Envío de logs a syslog", "must_contain", r"^logging (host )?\d", "major", None),
        ("Req 10.2 · Marcas de tiempo en logs", "must_contain", r"^service timestamps log datetime", "minor",
         "service timestamps log datetime msec localtime show-timezone"),
        ("Req 10.6 · Sincronización horaria NTP", "must_contain", r"^ntp server", "major", None),
        ("Req 12.6 · Banner de uso autorizado", "must_contain", r"^banner (motd|login)", "minor", None),
    ]),
    "HIPAA §164.312 (IOS)": (IOS_LIKE, "Salvaguardas técnicas de la Regla de Seguridad HIPAA", [
        ("164.312(a)(1) · Control de acceso con AAA", "must_contain", r"^aaa new-model", "major", None),
        ("164.312(a)(2)(iii) · Cierre automático de sesión", "must_not_contain", EXEC_TIMEOUT, "major",
         "line vty 0 15\n exec-timeout 10 0"),
        ("164.312(b) · Controles de auditoría: syslog", "must_contain", r"^logging (host )?\d", "major", None),
        ("164.312(b) · Registro de comandos de configuración", "must_contain", r"^\s*logging enable", "major",
         "archive\n log config\n  logging enable\n  notify syslog contenttype plaintext"),
        ("164.312(c)(1) · Integridad: hora sincronizada (NTP)", "must_contain", r"^ntp server", "minor", None),
        ("164.312(d) · Autenticación: sin enable password", "must_not_contain", r"^enable password",
         "critical", None),
        ("164.312(e)(1) · Transmisión cifrada: sin Telnet", "must_not_contain", NO_TELNET[0], "critical",
         NO_TELNET[1]),
        ("164.312(e)(1) · Transmisión cifrada: sin HTTP", "must_not_contain", r"^ip http server", "major",
         "no ip http server"),
        ("164.312(e)(2)(ii) · SNMPv3 (sin comunidades v1/v2c)", "must_not_contain", r"^snmp-server community",
         "major", None),
    ]),
    "SOX ITGC (IOS)": (IOS_LIKE, "Controles generales de TI para Sarbanes-Oxley: cambios, acceso y auditoría", [
        ("Gestión de cambios · Archivo de configuración activo", "must_contain", r"^archive", "major",
         "archive\n log config\n  logging enable"),
        ("Gestión de cambios · Registro de comandos", "must_contain", r"^\s*logging enable", "major", None),
        ("Acceso · Autenticación centralizada AAA", "must_contain", r"^aaa authentication login", "major", None),
        ("Acceso · Accounting de comandos", "must_contain", r"^aaa accounting commands", "major",
         "aaa accounting commands 15 default start-stop group tacacs+"),
        ("Acceso · Sin cuentas con enable password", "must_not_contain", r"^enable password", "critical", None),
        ("Auditoría · Syslog central", "must_contain", r"^logging (host )?\d", "major", None),
        ("Auditoría · Marcas de tiempo", "must_contain", r"^service timestamps log datetime", "minor", None),
        ("Auditoría · NTP", "must_contain", r"^ntp server", "minor", None),
    ]),
    "Cisco IOS · Buenas prácticas de hardening": (r"^cisco_(ios|xe)", "Guía de Cisco para robustecer IOS", [
        ("Sin source routing", "must_contain", r"^no ip source-route", "minor", "no ip source-route"),
        ("Sin servicio PAD", "must_contain", r"^no service pad", "minor", "no service pad"),
        ("TCP keepalives entrantes", "must_contain", r"^service tcp-keepalives-in", "minor",
         "service tcp-keepalives-in"),
        ("TCP keepalives salientes", "must_contain", r"^service tcp-keepalives-out", "minor",
         "service tcp-keepalives-out"),
        ("Sin servidor BOOTP", "must_contain", r"^no ip bootp server", "minor", "no ip bootp server"),
        ("Registrar fallos de login", "must_contain", r"^login on-failure log", "minor", "login on-failure log"),
        ("Timeout de SSH ≤ 60 s", "must_contain", r"^ip ssh time-out ([1-5]?\d|60)$", "minor",
         "ip ssh time-out 60"),
        ("Reintentos de SSH ≤ 3", "must_contain", r"^ip ssh authentication-retries [1-3]$", "minor",
         "ip ssh authentication-retries 3"),
        ("Logging en búfer", "must_contain", r"^logging buffered", "minor", "logging buffered 64000"),
        ("Sin HTTP", "must_not_contain", r"^ip http server", "major", "no ip http server"),
    ]),
    # Extreme VOSS / Fabric Engine (VSP 4000/7000/8000, 5000/7000 Fabric Engine): sintaxis ACLI de «show running-config»
    "Extreme VOSS · Seguridad básica": (r"^extreme_vsp", "Controles de seguridad de referencia para switches Extreme "
                                        "VOSS / Fabric Engine (servicios de gestión, SNMP, registros y hora)", [
        ("Sin Telnet", "must_not_contain", r"^boot config flags telnetd", "critical",
         "no boot config flags telnetd"),
        ("SSH habilitado", "must_contain", r"^(ssh\s*$|boot config flags sshd)", "major", "ssh"),
        ("Sin servidor FTP", "must_not_contain", r"^boot config flags ftpd", "major", "no boot config flags ftpd"),
        ("Sin servidor TFTP", "must_not_contain", r"^boot config flags tftpd", "major",
         "no boot config flags tftpd"),
        ("Sin rlogin", "must_not_contain", r"^boot config flags rlogind", "major", "no boot config flags rlogind"),
        ("Interfaz web solo por HTTPS", "must_not_contain", r"^web-server enable(?![\s\S]*^web-server secure-only)",
         "major", "web-server secure-only"),
        ("Sin comunidades SNMP por defecto", "must_not_contain", r'^snmp-server community "?(public|private)"?(\s|$)',
         "critical", None),
        ("Cierre de sesión inactiva ≤ 15 min", "must_not_contain", r"^cli timeout (90[1-9]|9[1-9]\d|[1-9]\d{3,})\b",
         "major", "cli timeout 900"),
        ("Autenticación centralizada (RADIUS o TACACS+)", "must_contain", r"^(radius|tacacs) server host", "major",
         None),
        ("Envío de logs a syslog", "must_contain", r"^syslog host \d+ address \S+", "major",
         None),
        ("Sincronización horaria NTP", "must_contain", r"^ntp server \S+", "major",
         None),
        ("Banner de uso autorizado", "must_contain", r"^banner (custom|motd)", "minor",
         'banner custom\nbanner motd "Acceso solo para personal autorizado"\nbanner displaymotd'),
    ]),
    # Ubiquiti EdgeSwitch y UniFi Switch (CLI Broadcom FastPath)
    "Ubiquiti EdgeSwitch/UniFi · Seguridad básica": (r"^ubiquiti_(edgeswitch|unifiswitch)",
                                                      "Controles de seguridad de referencia para switches Ubiquiti "
                                                      "(cuenta por defecto, servicios de gestión, SNMP, registros y hora)", [
        ("Sin la cuenta por defecto «ubnt»", "must_not_contain", r'^username "?ubnt"?\s', "critical", None),
        ("SSH habilitado", "must_contain", r"^ip ssh server enable", "major", "ip ssh server enable"),
        ("Sin Telnet", "must_not_contain", r"^ip telnet server enable", "critical", "no ip telnet server enable"),
        # sin corrección automática: «ip http secure-server» necesita un certificado y, sin él, se perdería la web
        ("Sin HTTP sin cifrar", "must_not_contain", r"^ip http server\b", "major", None),
        ("Sin comunidades SNMP por defecto", "must_not_contain", r'^snmp-server community "?(public|private)"?(\s|$)',
         "critical", None),
        ("Envío de logs a syslog", "must_contain", r"^logging host \S+", "major", None),
        ("Sincronización horaria (SNTP)", "must_contain", r"^sntp server ", "major",
         None),
    ]),
    # MikroTik RouterOS («/export terse»: solo aparece lo que difiere de fábrica; Telnet, FTP, www y API vienen activos)
    "MikroTik RouterOS · Seguridad básica": (r"^mikrotik_routeros", "Controles de seguridad de referencia para "
                                             "RouterOS: servicios de gestión, acceso por MAC, cortafuegos, registros y hora", [
        ("Sin Telnet", "must_contain", r"^/ip service set telnet .*\bdisabled=yes", "critical",
         "/ip service set telnet disabled=yes"),
        ("Sin FTP", "must_contain", r"^/ip service set ftp .*\bdisabled=yes", "major", "/ip service set ftp disabled=yes"),
        ("Sin web HTTP (www)", "must_contain", r"^/ip service set www .*\bdisabled=yes", "major",
         "/ip service set www disabled=yes"),
        ("Sin API sin cifrar", "must_contain", r"^/ip service set api .*\bdisabled=yes", "major",
         "/ip service set api disabled=yes"),
        ("Winbox limitado a redes de gestión", "must_contain", r"^/ip service set winbox .*\baddress=\S+", "minor",
         None),
        ("Acceso por MAC (MAC-Telnet/MAC-Winbox) limitado", "must_contain",
         r"^/tool mac-server (set .*\ballowed-interface-list=\S+|set .*\bdisabled=yes|mac-winbox set .*\ballowed-interface-list=\S+)",
         "major", "/tool mac-server set allowed-interface-list=none\n/tool mac-server mac-winbox set allowed-interface-list=none"),
        ("Descubrimiento de vecinos limitado", "must_contain", r"^/ip neighbor discovery-settings set .*\bdiscover-interface-list=\S+",
         "minor", "/ip neighbor discovery-settings set discover-interface-list=none"),
        ("Sin servidor de bandwidth-test", "must_contain", r"^/tool bandwidth-server set .*\benabled=no", "minor",
         "/tool bandwidth-server set enabled=no"),
        ("SSH con cifrado fuerte", "must_contain", r"^/ip ssh set .*\bstrong-crypto=yes", "minor",
         "/ip ssh set strong-crypto=yes"),
        ("Cortafuegos: descarta lo no permitido hacia el router", "must_contain",
         r"^/ip firewall filter add .*\bchain=input\b.*\baction=drop|^/ip firewall filter add .*\baction=drop\b.*\bchain=input\b",
         "critical", None),
        ("Sin comunidad SNMP «public»", "must_not_contain", r"^/snmp community (add|set) .*\bname=(public|private)\b",
         "critical", None),
        ("Envío de logs a syslog", "must_contain", r"^/system logging action (add|set) .*\bremote=\d", "major",
         None),
        ("Sincronización horaria NTP", "must_contain", r"^/system ntp client set .*\benabled=yes", "major",
         None),
    ]),
    # Juniper Junos (formato set)
    "Juniper Junos · Seguridad básica": (r"^juniper_junos", "Controles de seguridad de referencia para Junos: "
                                         "servicios de gestión, cuentas, SNMP, registros y hora", [
        ("Sin Telnet", "must_not_contain", r"^set system services telnet", "critical", "delete system services telnet"),
        ("Sin FTP", "must_not_contain", r"^set system services ftp", "major", "delete system services ftp"),
        ("Sin J-Web por HTTP", "must_not_contain", r"^set system services web-management http\b", "major",
         "delete system services web-management http"),
        ("SSH habilitado", "must_contain", r"^set system services ssh", "major", "set system services ssh"),
        ("Root sin acceso por SSH", "must_contain", r"^set system services ssh root-login deny", "major",
         "set system services ssh root-login deny"),
        ("Contraseña de root configurada", "must_contain", r"^set system root-authentication encrypted-password",
         "critical", None),
        ("Bloqueo tras intentos fallidos", "must_contain", r"^set system login retry-options tries-before-disconnect",
         "major", "set system login retry-options tries-before-disconnect 5\nset system login retry-options lockout-period 30"),
        ("Cierre de sesión inactiva", "must_contain", r"^set system login class \S+ idle-timeout", "major", None),
        ("Autenticación centralizada (RADIUS o TACACS+)", "must_contain", r"^set system (radius-server|tacplus-server) ",
         "major", None),
        ("Sin comunidades SNMP por defecto", "must_not_contain", r'^set snmp community "?(public|private)"?(\s|$)',
         "critical", "delete snmp community public\ndelete snmp community private"),
        ("Envío de logs a syslog", "must_contain", r"^set system syslog host \S+", "major",
         None),
        ("Sincronización horaria NTP", "must_contain", r"^set system ntp server \S+", "major",
         None),
        ("Banner de uso autorizado", "must_contain", r"^set system login message ", "minor",
         'set system login message "Acceso solo para personal autorizado"'),
    ]),
    # Fortinet FortiGate («show»: solo aparece lo que difiere de fábrica)
    "FortiGate · Seguridad básica": (r"^fortinet", "Controles de seguridad de referencia para FortiOS: acceso de "
                                     "administración, contraseñas, SNMP, registros y cifrado", [
        ("Sin Telnet en las interfaces", "must_not_contain", r"^\s*set allowaccess .*\btelnet\b", "critical", None),
        ("Sin HTTP en las interfaces", "must_not_contain", r"^\s*set allowaccess .*\bhttp\b", "major", None),
        ("Cierre de sesión de administración ≤ 15 min", "must_not_contain",
         r"^\s*set admintimeout (1[6-9]|[2-9]\d|\d{3,})\b", "major", "config system global\n    set admintimeout 10\nend"),
        ("Administradores con hosts de confianza", "must_contain", r"^\s*set trusthost\d+ ", "major", None),
        ("Política de contraseñas activa", "must_contain",
         r"^config system password-policy\n(?:\s+set (?!status).*\n)*\s+set status enable", "major",
         "config system password-policy\n    set status enable\n    set minimum-length 12\nend"),
        ("Cifrado fuerte", "must_not_contain", r"^\s*set strong-crypto disable", "major",
         "config system global\n    set strong-crypto enable\nend"),
        ("Sin comunidades SNMP por defecto", "must_not_contain",
         r'^config system snmp community\n(?:(?!^end)[\s\S])*?set name "(public|private)"', "critical", None),
        ("Envío de logs a syslog o FortiAnalyzer", "must_contain",
         r"^config log (syslogd\d?|fortianalyzer\d?|fortianalyzer-cloud) setting\n(?:\s+set (?!status).*\n)*\s+set status enable",
         "major", None),
        ("Sincronización horaria NTP activa", "must_not_contain", r"^\s*set ntpsync disable", "major",
         "config system ntp\n    set ntpsync enable\nend"),
        ("Banner antes del inicio de sesión", "must_contain", r"^\s*set pre-login-banner enable", "minor",
         "config system global\n    set pre-login-banner enable\nend"),
        ("Cuenta «admin» por defecto renombrada", "must_not_contain", r'^config system admin\n\s+edit "admin"$', "minor",
         None),
    ]),
    "PCI DSS 4.0 (Junos)": (r"^juniper_junos", "Controles técnicos de PCI DSS 4.0 para Junos (formato set)", [
        ("Req 2.2.7 · Sin Telnet", "must_not_contain", r"^set system services telnet", "critical",
         "delete system services telnet"),
        ("Req 2.2.4 · Sin web-management HTTP", "must_not_contain", r"^set system services web-management http\b",
         "major", "delete system services web-management http"),
        ("Req 8 · Root sin acceso SSH", "must_contain", r"^set system services ssh root-login deny", "major",
         "set system services ssh root-login deny"),
        ("Req 8.3.4 · Bloqueo tras intentos fallidos", "must_contain",
         r"^set system login retry-options tries-before-disconnect", "major",
         "set system login retry-options tries-before-disconnect 6\nset system login retry-options lockout-period 30"),
        ("Req 8.2.8 · Cierre de sesión inactiva", "must_contain", r"^set system login class \S+ idle-timeout",
         "major", None),
        ("Req 10.2 · Syslog remoto", "must_contain", r"^set system syslog host", "major", None),
        ("Req 10.6 · NTP", "must_contain", r"^set system ntp server", "major", None),
    ]),
}


# Paquetes añadidos después de la primera versión: se cargan una sola vez en las instalaciones que se actualizan
# (si después se borran, no vuelven a aparecer).
ADDED_LATER = {"Extreme VOSS · Seguridad básica": "2.2.1", "Ubiquiti EdgeSwitch/UniFi · Seguridad básica": "2.2.1",
               "MikroTik RouterOS · Seguridad básica": "2.3.0", "Juniper Junos · Seguridad básica": "2.3.0",
               "FortiGate · Seguridad básica": "2.3.0"}


def _seeded() -> set:
    import json

    from .db import get_setting
    try:
        return set(json.loads(get_setting("compliance_packs_seeded", "[]") or "[]"))
    except ValueError:
        return set()


def _mark_seeded(names):
    import json

    from .db import set_setting
    set_setting("compliance_packs_seeded", json.dumps(sorted(_seeded() | set(names))))


# Correcciones publicadas en 2.2.1–2.4.0 que no deben aplicarse tal cual: con una dirección de ejemplo (192.0.2.x), un
# servidor que depende de cada red, o que puede dejar sin la web (HTTPS sin certificado en Ubiquiti). Se quitan de las
# instalaciones que las tengan sin modificar.
EXAMPLE_FIXES = {
    ('Ubiquiti EdgeSwitch/UniFi · Seguridad básica', 'Sin HTTP sin cifrar'):
        'ip http secure-server\nno ip http server',
    ('Extreme VOSS · Seguridad básica', 'Envío de logs a syslog'):
        'syslog host 1 address 192.0.2.10\nsyslog host 1 enable',
    ('Extreme VOSS · Seguridad básica', 'Sincronización horaria NTP'):
        'ntp server 192.0.2.1 enable\nntp',
    ('Ubiquiti EdgeSwitch/UniFi · Seguridad básica', 'Envío de logs a syslog'):
        'logging host 192.0.2.10 ipv4 514',
    ('Ubiquiti EdgeSwitch/UniFi · Seguridad básica', 'Sincronización horaria (SNTP)'):
        'sntp client mode unicast\nsntp server "192.0.2.1"',
    ('MikroTik RouterOS · Seguridad básica', 'Winbox limitado a redes de gestión'):
        '/ip service set winbox address=192.0.2.0/24',
    ('MikroTik RouterOS · Seguridad básica', 'Envío de logs a syslog'):
        '/system logging action set [find name=remote] remote=192.0.2.10\n/system logging add action=remote topics=info',
    ('MikroTik RouterOS · Seguridad básica', 'Sincronización horaria NTP'):
        '/system ntp client set enabled=yes servers=pool.ntp.org',
    ('Juniper Junos · Seguridad básica', 'Envío de logs a syslog'):
        'set system syslog host 192.0.2.10 any notice',
    ('Juniper Junos · Seguridad básica', 'Sincronización horaria NTP'):
        'set system ntp server 192.0.2.1',
    ('FortiGate · Seguridad básica', 'Envío de logs a syslog o FortiAnalyzer'):
        'config log syslogd setting\n    set status enable\n    set server "192.0.2.10"\nend',
}


def drop_example_fixes(db) -> int:
    """Quita esas correcciones de las reglas ya cargadas, solo si siguen exactamente como se publicaron."""
    from sqlalchemy import select

    from .db import Policy
    n = 0
    for p in db.scalars(select(Policy).where(Policy.name.in_({k[0] for k in EXAMPLE_FIXES}))):
        for r in p.rules:
            old = EXAMPLE_FIXES.get((p.name, r.name))
            if old and (r.remediation or "").strip() == old.strip():
                r.remediation = None
                n += 1
    if n:
        db.commit()
    return n


def seed_new_packs(db) -> int:
    """Al arrancar: carga los paquetes nuevos de esta versión que la instalación aún no recibió."""
    pending = [n for n in ADDED_LATER if n not in _seeded()]
    if not pending:
        return 0
    n = seed_packs(db, only=pending)
    _mark_seeded(pending)
    return n


def seed_packs(db, only=None):
    from sqlalchemy import select

    from .db import Policy, Rule
    created = 0
    for name, (flt, desc, rules) in PACKS.items():
        if only is not None and name not in only:
            continue
        if db.scalars(select(Policy).where(Policy.name == name)).first():
            continue
        p = Policy(name=name, device_type_filter=flt, description=desc, enabled=True)
        db.add(p)
        db.flush()
        for rname, kind, pattern, sev, rem in rules:
            db.add(Rule(policy_id=p.id, name=rname, kind=kind, pattern=pattern, severity=sev, remediation=rem))
        created += 1
    db.commit()
    if only is None:
        _mark_seeded(PACKS)
    return created
