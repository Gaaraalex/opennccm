"""Paquetes de cumplimiento listos para usar: PCI DSS 4.0, HIPAA, SOX y buenas prácticas Cisco IOS.

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


def seed_packs(db):
    from sqlalchemy import select

    from .db import Policy, Rule
    created = 0
    for name, (flt, desc, rules) in PACKS.items():
        if db.scalars(select(Policy).where(Policy.name == name)).first():
            continue
        p = Policy(name=name, device_type_filter=flt, description=desc, enabled=True)
        db.add(p)
        db.flush()
        for rname, kind, pattern, sev, rem in rules:
            db.add(Rule(policy_id=p.id, name=rname, kind=kind, pattern=pattern, severity=sev, remediation=rem))
        created += 1
    db.commit()
    return created
