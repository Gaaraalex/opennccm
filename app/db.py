from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine,
                        event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATA_DIR, DATABASE_URL

if DATABASE_URL.startswith("sqlite"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=20, max_overflow=20)

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | operator | viewer
    email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    theme: Mapped[str] = mapped_column(String(16), default="auto")  # auto | claro | oscuro | contraste
    accent: Mapped[str | None] = mapped_column(String(16))  # color de acento (vacío: cobalto)
    density: Mapped[str | None] = mapped_column(String(8))  # densidad de las tablas: comfy | compact (vacío: cómoda)
    lang: Mapped[str | None] = mapped_column(String(8))  # idioma de la interfaz (vacío: el del navegador)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # seguridad
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)        # verificación en dos pasos (cifrado)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)   # impide reutilizar un mismo código
    session_version: Mapped[int] = mapped_column(Integer, default=0)  # cambia al cambiar la clave: cierra sesiones
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_login_ip: Mapped[str | None] = mapped_column(String(64))


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str] = mapped_column(String(128))
    password_enc: Mapped[str | None] = mapped_column(Text)
    secret_enc: Mapped[str | None] = mapped_column(Text)  # enable / privilegiado


class DeviceGroup(Base):
    __tablename__ = "device_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    protocol: Mapped[str] = mapped_column(String(8), default="ssh")
    device_type: Mapped[str] = mapped_column(String(64))
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("device_groups.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
    location: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    # inventario
    model: Mapped[str | None] = mapped_column(String(128))
    serial: Mapped[str | None] = mapped_column(String(128))
    os_version: Mapped[str | None] = mapped_column(String(128))
    inventory_raw_enc: Mapped[str | None] = mapped_column(Text)
    # estado
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str] = mapped_column(String(16), default="pending")  # ok | error | pending
    last_error: Mapped[str | None] = mapped_column(Text)
    startup_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    maintenance_until: Mapped[datetime | None] = mapped_column(DateTime)
    web_url: Mapped[str | None] = mapped_column(String(255))   # interfaz web del equipo (por omisión https://host)
    web_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # mostrar el botón 🌐 (hay equipos sin web)
    custom_backup_cmd: Mapped[str | None] = mapped_column(Text)   # sustituye al comando del perfil
    baseline_version_id: Mapped[int | None] = mapped_column(Integer)  # versión "línea base"
    baseline_drift: Mapped[bool] = mapped_column(Boolean, default=False)

    credential = relationship("Credential")
    group = relationship("DeviceGroup")
    versions = relationship("ConfigVersion", back_populates="device", cascade="all, delete-orphan",
                            passive_deletes=True)


class ConfigVersion(Base):
    __tablename__ = "config_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="running")  # running | startup
    content_enc: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual|scheduled|syslog|push
    username: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(128))  # p.ej. "baseline"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    device = relationship("Device", back_populates="versions")


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    device_type_filter: Mapped[str | None] = mapped_column(String(255))  # regex sobre device_type
    group_id: Mapped[int | None] = mapped_column(ForeignKey("device_groups.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rules = relationship("Rule", cascade="all, delete-orphan", passive_deletes=True)


class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # must_contain | must_not_contain
    pattern: Mapped[str] = mapped_column(Text)      # regex multilínea
    severity: Mapped[str] = mapped_column(String(16), default="major")  # critical|major|minor
    remediation: Mapped[str | None] = mapped_column(Text)  # comandos de corrección


class ComplianceResult(Base):
    __tablename__ = "compliance_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"))
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="CASCADE"))
    passed: Mapped[bool] = mapped_column(Boolean)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    rule = relationship("Rule")
    policy = relationship("Policy")
    device = relationship("Device")


class Configlet(Base):
    __tablename__ = "configlets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="config")  # config | exec
    body: Mapped[str] = mapped_column(Text)  # plantilla Jinja2
    device_type_filter: Mapped[str | None] = mapped_column(String(255))


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)            # plantilla Jinja2 de comandos
    variables: Mapped[str | None] = mapped_column(Text)  # JSON
    device_ids: Mapped[str] = mapped_column(Text)        # JSON
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | approved | running | done | partial | failed | rejected
    requested_by: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    review_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[str | None] = mapped_column(Text)
    is_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), default="config")  # config | firmware
    firmware_id: Mapped[int | None] = mapped_column(Integer)
    reload: Mapped[bool] = mapped_column(Boolean, default=False)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))  # backup | compliance | cve | configlet
    cron: Mapped[str] = mapped_column(String(64))    # crontab de 5 campos
    group_id: Mapped[int | None] = mapped_column(ForeignKey("device_groups.id", ondelete="SET NULL"))
    configlet_id: Mapped[int | None] = mapped_column(ForeignKey("configlets.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime)
    group = relationship("DeviceGroup")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="info")  # info|warning|major|critical
    category: Mapped[str] = mapped_column(String(32))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    message: Mapped[str] = mapped_column(Text)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    device = relationship("Device")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))


class Vulnerability(Base):
    __tablename__ = "vulnerabilities"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    cve_id: Mapped[str] = mapped_column(String(32))
    score: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    found_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    device = relationship("Device")


class UserGroup(Base):
    """Restringe a un usuario (no admin) a los equipos de ciertos grupos. Sin filas = todos."""
    __tablename__ = "user_groups"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("device_groups.id", ondelete="CASCADE"), primary_key=True)


class EolEntry(Base):
    """Fechas de fin de venta / fin de soporte por modelo (manual, CSV o API Cisco EoX)."""
    __tablename__ = "eol_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str | None] = mapped_column(String(64))
    model_pattern: Mapped[str] = mapped_column(String(255))  # regex sobre el modelo del inventario
    end_of_sale: Mapped[datetime | None] = mapped_column(DateTime)
    end_of_sw_maintenance: Mapped[datetime | None] = mapped_column(DateTime)
    end_of_support: Mapped[datetime | None] = mapped_column(DateTime)  # último día de soporte
    replacement: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16), default="manual")


class FirmwareImage(Base):
    __tablename__ = "firmware_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    device_type_filter: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    md5: Mapped[str] = mapped_column(String(32))
    uploaded_by: Mapped[str | None] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


SECRET_SETTINGS = {"smtp_password", "cisco_api_secret", "dr_remote_password"}


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _migrate_themes()


# Rediseño «Señal» (1.9): los 10 temas pasan a 4 temas + un color de acento; nadie pierde «su» color.
OLD_THEMES = {"noche": ("oscuro", "cobalto"), "bosque": ("claro", "bosque"), "arena": ("claro", "arena"),
              "lavanda": ("claro", "lavanda"), "oceano": ("oscuro", "oceano"), "atardecer": ("oscuro", "atardecer"),
              "grafito": ("oscuro", "grafito")}


def _migrate_themes():
    with SessionLocal() as db:
        changed = False
        for u in db.scalars(select(User).where(User.theme.in_(list(OLD_THEMES)))):
            u.theme, u.accent = OLD_THEMES[u.theme]
            changed = True
        if changed:
            db.commit()


def _add_missing_columns():
    """Migración mínima al actualizar: agrega columnas nuevas a tablas existentes."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                default = getattr(col.default, "arg", None)
                if isinstance(default, bool):
                    ddl += f" DEFAULT {'1' if default else '0'}" if engine.dialect.name == "sqlite" \
                        else f" DEFAULT {str(default).upper()}"
                elif isinstance(default, (int, str)):
                    ddl += f" DEFAULT '{default}'" if isinstance(default, str) else f" DEFAULT {default}"
                conn.execute(text(ddl))


def get_setting(key: str, default: str = "") -> str:
    from .crypto import decrypt
    with SessionLocal() as db:
        s = db.get(Setting, key)
        if not s or s.value is None:
            return default
        return decrypt(s.value) if key in SECRET_SETTINGS else s.value


def set_setting(key: str, value: str):
    from .crypto import encrypt
    with SessionLocal() as db:
        s = db.get(Setting, key) or Setting(key=key)
        s.value = encrypt(value) if key in SECRET_SETTINGS else value
        db.merge(s)
        db.commit()


def audit(username: str, action: str, detail: str = "", ip: str | None = None):
    with SessionLocal() as db:
        db.add(AuditLog(username=username, action=action, detail=detail[:4000], ip=ip))
        db.commit()


__all__ = ["select"]
