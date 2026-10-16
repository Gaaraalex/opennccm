"""Utilidades de línea de comandos.

    python -m app.cli version
    python -m app.cli list-users
    python -m app.cli create-admin <usuario> [contraseña]
    python -m app.cli reset-password <usuario> <contraseña>
    python -m app.cli seed          # políticas y configlets de ejemplo
    python -m app.cli backup-all    # respaldo inmediato de todos los equipos
    python -m app.cli system-backup [--with-key]   # copia de recuperación de OpenNCCM
    python -m app.cli restore <archivo.tar.gz>      # restaurar (con el servicio detenido)
"""
import getpass
import sys

from sqlalchemy import select

from .crypto import hash_password
from .db import Configlet, Policy, Rule, SessionLocal, User, init_db

SEED_POLICIES = {
    "Hardening básico Cisco/IOS-like": ("cisco|arista|dell|ruckus|allied|tplink|ruijie|zte", [
        ("Cifrado de contraseñas", "must_contain", r"^service password-encryption", "major",
         "service password-encryption"),
        ("Sin Telnet en VTY", "must_not_contain", r"^\s*transport input .*telnet", "critical",
         "line vty 0 15\n transport input ssh"),
        ("SSH versión 2", "must_contain", r"^ip ssh version 2", "major", "ip ssh version 2"),
        ("Sin servidor HTTP", "must_not_contain", r"^ip http server", "major", "no ip http server"),
        ("Enable secret (no enable password)", "must_not_contain", r"^enable password", "critical", None),
        ("SNMP sin comunidad 'public'", "must_not_contain", r"^snmp-server community public", "critical",
         "no snmp-server community public"),
        ("NTP configurado", "must_contain", r"^ntp server", "minor", None),
        ("Syslog configurado", "must_contain", r"^logging (host )?\d", "minor", None),
        ("Banner de acceso", "must_contain", r"^banner (motd|login)", "minor", None),
    ]),
    "Hardening Huawei/H3C": ("huawei|hp_comware", [
        ("Sin Telnet", "must_not_contain", r"^\s*telnet server enable", "critical", "undo telnet server enable"),
        ("Stelnet habilitado", "must_contain", r"^\s*stelnet server enable", "major", "stelnet server enable"),
        ("NTP configurado", "must_contain", r"^\s*ntp(-service)? unicast-server", "minor", None),
        ("Syslog configurado", "must_contain", r"^\s*info-center loghost", "minor", None),
    ]),
    "Hardening Juniper": ("juniper", [
        ("Sin Telnet", "must_not_contain", r"^set system services telnet", "critical",
         "delete system services telnet"),
        ("Root sin SSH", "must_contain", r"^set system services ssh root-login deny", "major",
         "set system services ssh root-login deny"),
        ("NTP configurado", "must_contain", r"^set system ntp server", "minor", None),
    ]),
    "Hardening FortiGate": ("fortinet", [
        ("Admin sin HTTP", "must_not_contain", r"set allowaccess .*\bhttp\b", "major", None),
        ("Admin sin Telnet", "must_not_contain", r"set allowaccess .*\btelnet\b", "critical", None),
        ("Timeout de admin", "must_contain", r"set admintimeout \d+", "minor", None),
    ]),
}

SEED_CONFIGLETS = [
    ("Cambiar contraseña de usuario local (IOS)", "config", "cisco|arista|dell|ruckus",
     "Cambia la contraseña de un usuario local",
     "username {{ usuario }} privilege 15 secret {{ clave }}"),
    ("Agregar VLAN (IOS-like)", "config", "cisco|arista|dell|ruckus|aruba",
     "Crea una VLAN con nombre", "vlan {{ vlan_id }}\n name {{ vlan_nombre }}"),
    ("Agregar VLAN (Huawei)", "config", "huawei|hp_comware",
     "Crea una VLAN con descripción", "vlan {{ vlan_id }}\n description {{ vlan_nombre }}"),
    ("Configurar NTP (IOS)", "config", "cisco|arista",
     "Configura servidor NTP", "ntp server {{ ntp_server }}"),
    ("Configurar syslog hacia OpenNCCM (IOS)", "config", "cisco",
     "Envía logs de configuración al servidor OpenNCCM para detección en tiempo real",
     "logging host {{ servidor_ncm }}\nlogging trap notifications\narchive\n log config\n  logging enable\n  notify syslog contenttype plaintext"),
    ("Configurar syslog hacia OpenNCCM (Junos)", "config", "juniper",
     "Envía eventos de commit al servidor OpenNCCM",
     "set system syslog host {{ servidor_ncm }} interactive-commands notice\nset system syslog host {{ servidor_ncm }} change-log info"),
    ("Configurar syslog hacia OpenNCCM (Huawei)", "config", "huawei",
     "Envía logs al servidor OpenNCCM", "info-center loghost {{ servidor_ncm }}"),
    ("Estado de interfaces (IOS)", "exec", "cisco|arista", "Muestra resumen de interfaces",
     "show ip interface brief"),
    ("Estado de interfaces (Huawei)", "exec", "huawei|hp_comware", "Muestra resumen de interfaces",
     "display interface brief"),
    ("Tabla ARP y MAC (IOS)", "exec", "cisco", "Diagnóstico de nivel 2/3",
     "show arp\nshow mac address-table"),
    ("Verificar flash para actualización de firmware (IOS)", "exec", "cisco",
     "Paso previo a actualizar firmware", "show version\ndir flash:\nshow boot"),
]


def create_admin(username: str, password: str | None):
    password = password or getpass.getpass("Contraseña: ")
    if len(password) < 8:
        sys.exit("La contraseña debe tener al menos 8 caracteres")
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.username == username)).first()
        if u:
            u.password_hash, u.role, u.active = hash_password(password), "admin", True
        else:
            db.add(User(username=username, password_hash=hash_password(password), role="admin"))
        db.commit()
    print(f"Administrador '{username}' listo.")


def list_users():
    from .db import DeviceGroup, UserGroup
    roles = {"admin": "administrador", "operator": "operador", "viewer": "lector"}
    with SessionLocal() as db:
        groups = {g.id: g.name for g in db.scalars(select(DeviceGroup))}
        rows = []
        for u in db.scalars(select(User).order_by(User.username)):
            gids = list(db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == u.id)))
            scope = "todos" if u.role == "admin" or not gids else ", ".join(groups.get(g, "?") for g in gids)
            rows.append((u.username, roles.get(u.role, u.role), "activo" if u.active else "BLOQUEADO", scope))
    head = ("USUARIO", "ROL", "ESTADO", "EQUIPOS")
    widths = [max(len(str(r[i])) for r in rows + [head]) for i in range(4)]
    for r in [head] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())
    print(f"\n{len(rows)} usuario(s). Cambiar una contraseña:  sudo opennccm reset-password USUARIO 'CLAVE'")


def seed():
    with SessionLocal() as db:
        for name, (flt, rules) in SEED_POLICIES.items():
            if db.scalars(select(Policy).where(Policy.name == name)).first():
                continue
            p = Policy(name=name, device_type_filter=flt, description="Política de ejemplo")
            db.add(p)
            db.flush()
            for rname, kind, pattern, sev, rem in rules:
                db.add(Rule(policy_id=p.id, name=rname, kind=kind, pattern=pattern, severity=sev, remediation=rem))
        for name, kind, flt, desc, body in SEED_CONFIGLETS:
            if not db.scalars(select(Configlet).where(Configlet.name == name)).first():
                db.add(Configlet(name=name, kind=kind, device_type_filter=flt, description=desc, body=body))
        db.commit()
        from .compliance_packs import seed_packs
        seed_packs(db)
    print("Políticas (incl. PCI DSS, HIPAA, SOX, Cisco IOS) y configlets de ejemplo cargados.")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    cmd = args[0]
    if cmd == "version":
        from . import __version__
        print(__version__)
        return
    if cmd != "restore":  # restaurar no debe crear una base vacía antes de reemplazarla
        init_db()
    if cmd in ("create-admin", "reset-password") and len(args) >= 2:
        create_admin(args[1], args[2] if len(args) > 2 else None)
    elif cmd == "seed":
        seed()
    elif cmd == "list-users":
        list_users()
    elif cmd == "system-backup":
        from .sysbackup import create_backup
        print(create_backup(include_key="--with-key" in args))
    elif cmd == "restore" and len(args) >= 2:
        from pathlib import Path

        from .sysbackup import restore_backup
        print(restore_backup(Path(args[1])))
        print("Reinicie el servicio: sudo systemctl start opennccm")
    elif cmd == "backup-all":
        from . import services
        print(services.backup_many(services.device_ids(), "manual", "cli"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
