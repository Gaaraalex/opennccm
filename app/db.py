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


class ApiToken(Base):
    """Token personal para la API (/api/…): actúa con los permisos y grupos de su dueño. Solo se guarda su hash."""
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16))  # primeros caracteres, para reconocerlo en la lista
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    client: Mapped[str | None] = mapped_column(String(16))  # «android»: token de la sesión de un teléfono (API móvil)


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
    auth_source: Mapped[str] = mapped_column(String(8), default="local")  # local | ldap (Active Directory) | oidc (Azure)


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
    neighbors: Mapped[str | None] = mapped_column(Text)  # JSON: vecinos LLDP/CDP del último respaldo (mapa de la red)
    # JSON {"at", "status", "ports"}: puertos físicos y su estado leídos en el último respaldo (app/ports.py)
    ports: Mapped[str | None] = mapped_column(Text)
    # licencia del equipo (Profile.license_cmd, p. ej. la feature key de WatchGuard), cifrada; se lee una sola vez
    license_enc: Mapped[str | None] = mapped_column(Text)
    license_at: Mapped[datetime | None] = mapped_column(DateTime)
    # imagen completa .fxi de WatchGuard (app/fxi.py): credencial de administrador, clave de cifrado, frecuencia, cuántas
    # guardar y el receptor FTP temporal (dirección por la que llega el Firebox, puerto de control y pasivos)
    fxi_cred_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"))
    fxi_key_enc: Mapped[str | None] = mapped_column(Text)
    fxi_freq: Mapped[str | None] = mapped_column(String(16))
    fxi_keep: Mapped[int | None] = mapped_column(Integer)
    fxi_addr: Mapped[str | None] = mapped_column(String(64))
    fxi_port: Mapped[int | None] = mapped_column(Integer)
    fxi_pasv: Mapped[str | None] = mapped_column(String(16))
    fxi_fails: Mapped[int] = mapped_column(Integer, default=0)
    fxi_last_at: Mapped[datetime | None] = mapped_column(DateTime)

    credential = relationship("Credential", foreign_keys=[credential_id])
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


class FxiImage(Base):
    """Imagen .fxi de un Firebox (app/fxi.py): el archivo está en DATA_DIR/fxi/<equipo>/; su clave, cifrada aquí."""
    __tablename__ = "fxi_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled
    username: Mapped[str | None] = mapped_column(String(64))
    key_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


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
    notes: Mapped[str | None] = mapped_column(Text)  # avisos de la reversión: lo que hay que revisar a mano


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


class FirmwareUpdate(Base):
    """Última comprobación de actualizaciones de firmware de un equipo (ver fwupdates.py)."""
    __tablename__ = "firmware_updates"
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # update | current | unknown | error
    source: Mapped[str | None] = mapped_column(String(16))   # mikrotik | ubiquiti | nvd
    current: Mapped[str | None] = mapped_column(String(128))
    target: Mapped[str | None] = mapped_column(String(64))   # versión recomendada (la más nueva de su rama)
    latest: Mapped[str | None] = mapped_column(String(64))   # la más nueva conocida (puede ser otra rama)
    notes_url: Mapped[str | None] = mapped_column(String(512))
    changelog: Mapped[str | None] = mapped_column(Text)
    download_url: Mapped[str | None] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int | None] = mapped_column(Integer)
    fixed_cves: Mapped[str | None] = mapped_column(Text)     # JSON [[id, severidad], …]: CVE que la nueva ya no tiene
    fixed_for: Mapped[str | None] = mapped_column(String(64))
    open_cves: Mapped[int | None] = mapped_column(Integer)   # CVE que siguen afectando a la versión nueva
    detail: Mapped[str | None] = mapped_column(Text)         # motivo cuando no hay fuente o hubo un error
    notified: Mapped[str | None] = mapped_column(String(64))  # versión ya avisada en Alarmas
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)


class ConsoleSession(Base):
    """Una sesión de la consola web (Operar › Consola): quién, en qué equipo, cuánto duró y su grabación en disco."""
    __tablename__ = "console_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    device_name: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    commands: Mapped[int] = mapped_column(Integer, default=0)
    danger: Mapped[bool] = mapped_column(Boolean, default=False)   # hubo un comando que puede cortar el servicio
    recorded: Mapped[bool] = mapped_column(Boolean, default=False)  # hay grabación (DATA_DIR/console/rec/<id>.jsonl)


class ConsoleAccessRequest(Base):
    """Pedido de consola en un equipo cuyo grupo exige aprobación (Operar › Consola › Acceso)."""
    __tablename__ = "console_access_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(Text)
    hours: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    until: Mapped[datetime | None] = mapped_column(DateTime)
    device = relationship("Device")


class Recipe(Base):
    """Receta propia de un usuario (las incluidas en OpenNCCM están en app/recipes.py)."""
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    data: Mapped[str] = mapped_column(Text)  # JSON: name, vendor, os, cat, intro, vars, steps
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AiUsage(Base):
    """Una pregunta al asistente IA: para el límite diario y el uso del mes (los textos van a Auditoría, no aquí)."""
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)


class AiChat(Base):
    """Conversación del chat del asistente (barra superior), por usuario: la última se retoma; «+» empieza otra."""
    __tablename__ = "ai_chats"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    messages: Mapped[str] = mapped_column(Text, default="[]")  # JSON: preguntas y respuestas (con sus acciones)


class KbCase(Base):
    """Caso de la base de conocimiento (Equipo › Base de conocimiento): cómo se resolvió algo en la red."""
    __tablename__ = "kb_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(32), default="")  # respaldos | conectividad | cambios | …
    vendor: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft | review | published | archived
    symptom: Mapped[str] = mapped_column(Text, default="")
    cause: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{"t": texto, "c": comando, "hid": se ocultó una clave}]
    verify: Mapped[str] = mapped_column(Text, default="")
    prevent: Mapped[str] = mapped_column(Text, default="")
    ai_marks: Mapped[str | None] = mapped_column(Text)  # JSON: campos propuestos por el asistente que faltan revisar
    author: Mapped[str] = mapped_column(String(64), index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    review_comment: Mapped[str | None] = mapped_column(Text)  # «Pedir cambios»
    source_kind: Mapped[str | None] = mapped_column(String(16))  # event | change | console
    source_id: Mapped[int | None] = mapped_column(Integer)
    console_id: Mapped[int | None] = mapped_column(Integer)  # grabación de consola enlazada
    archived_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)


class KbCaseDevice(Base):
    __tablename__ = "kb_case_devices"
    case_id: Mapped[int] = mapped_column(ForeignKey("kb_cases.id", ondelete="CASCADE"), primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)


class KbVersion(Base):
    """Cada versión publicada de un caso (v1, v2…): el contenido completo en JSON."""
    __tablename__ = "kb_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("kb_cases.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    data: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(255), default="")
    author: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbProposal(Base):
    """«Proponer cambio» sobre un caso publicado: espera la revisión de un admin (el caso sigue como estaba)."""
    __tablename__ = "kb_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("kb_cases.id", ondelete="CASCADE"), index=True)
    data: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="review")  # review | approved | returned
    review_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbComment(Base):
    __tablename__ = "kb_comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("kb_cases.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbVote(Base):
    """«Me sirvió»: un voto por persona y caso."""
    __tablename__ = "kb_votes"
    case_id: Mapped[int] = mapped_column(ForeignKey("kb_cases.id", ondelete="CASCADE"), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class KbPending(Base):
    """«Falta documentar»: algo que se cerró sin caso (alarma crítica, cambio revertido, sesión larga de consola)."""
    __tablename__ = "kb_pending"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # event | change | console
    source_id: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    assignee: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    state: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | documented | linked | dismissed
    case_id: Mapped[int | None] = mapped_column(ForeignKey("kb_cases.id", ondelete="SET NULL"))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    overdue_sent: Mapped[bool] = mapped_column(Boolean, default=False)  # ya se avisó al admin de los 5 días


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


SECRET_SETTINGS = {"git_token", "smtp_password", "cisco_api_secret", "dr_remote_password", "ldap_bind_password", "oidc_client_secret",
                   "nvd_api_key", "ai_key_anthropic", "ai_key_openai", "ai_key_gemini", "ai_key_mistral", "ai_key_compat"}


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
