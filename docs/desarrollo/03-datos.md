# 3. Datos

Todo está en `app/db.py`: el motor, los modelos (SQLAlchemy 2, estilo `Mapped[...]`), la migración y los ajustes.

## Motor y sesiones

- **SQLite** por omisión (`DATA_DIR/opennccm.db`), con `journal_mode=WAL` y `foreign_keys=ON` en cada conexión. El
  grupo de conexiones se dimensiona con `NCM_WORKERS + 8` para que los respaldos en paralelo nunca se queden sin una.
- **PostgreSQL** con `NCM_DATABASE_URL` (el instalador lo ofrece). El código no usa nada propio de un motor salvo en
  lugares marcados (`engine.dialect.name`): agrupar por fecha en `reports._hour_key` y la copia de recuperación.
- Cada uso abre una sesión corta: `with SessionLocal() as db: ...`. `expire_on_commit=False`: los objetos siguen
  leíbles después del commit. **Las relaciones se cargan dentro de la sesión** (después da `DetachedInstanceError`);
  por eso hay líneas como `v.device.name  # carga la relación antes de cerrar la sesión`, o mejor, `selectinload`.

## Tablas

| Tabla | Modelo | Qué guarda |
|---|---|---|
| `devices` | `Device` | cada equipo: dirección, tipo (`device_type` = clave del perfil), credencial, grupo, nodo, inventario (modelo, serie, versión), estado del último respaldo, vecinos y puertos (JSON), retiro |
| `config_versions` | `ConfigVersion` | cada copia de la configuración (`running` o `startup`), **cifrada** (`content_enc`), su sha256, líneas +/−, origen (`manual`, `scheduled`, `syslog`, `push`), firmware de esa copia (`fw_version`) |
| `credentials` | `Credential` | usuario y contraseña/secreto cifrados, reutilizables entre equipos |
| `device_groups`, `net_nodes` | `DeviceGroup`, `NetNode` | grupos (permisos, programaciones) y nodos (sitios del mapa) |
| `users`, `user_groups`, `api_tokens` | `User`, `UserGroup`, `ApiToken` | cuentas locales o de directorio, a qué grupos se limitan y sus tokens de API (solo el hash) |
| `change_requests` | `ChangeRequest` | solicitudes de cambio: cuerpo (plantilla Jinja2), variables, equipos, estado, resultado, hora programada |
| `policies`, `rules`, `compliance_results` | `Policy`, `Rule`, `ComplianceResult` | políticas de cumplimiento, sus reglas (regex) y el último resultado de cada regla en cada equipo |
| `autofix_runs` | `AutoFixRun` | historial de la corrección automática (`app/autofix.py`): equipo, política, reglas, resultado, motivo, salida y versiones de antes y después. La política guarda si está activada, su ventana y quién la activó (`Policy.autofix*`); cada regla elegida, la huella de lo aprobado (`Rule.autofix_sig`); cada equipo, si se permite (`Device.autofix_allowed`) |
| `configlets`, `recipes` | `Configlet`, `Recipe` | plantillas de comandos y recetas propias de los usuarios (las incluidas están en el código) |
| `schedules` | `Schedule` | programaciones (respaldo, auditoría, CVE, configlet) con su cron |
| `events` | `Event` | alarmas y eventos (gravedad, categoría, equipo, reconocida) |
| `audit_log` | `AuditLog` | quién hizo qué y desde dónde |
| `vulnerabilities` | `Vulnerability` | CVE de NVD por equipo |
| `eol_entries` | `EolEntry` | fin de venta / soporte por modelo |
| `firmware_images`, `firmware_updates` | `FirmwareImage`, `FirmwareUpdate` | biblioteca de firmware y última comprobación de versiones nuevas por equipo |
| `console_sessions`, `console_access_requests`, `console_favorites` | … | sesiones de la consola web (con grabación en disco), pedidos de acceso y comandos favoritos |
| `kb_*` | `KbCase`, `KbVersion`, … | base de conocimiento: casos, versiones, propuestas, comentarios, votos, «falta documentar» |
| `mac_seen`, `neighbor_states` | `MacSeen`, `NeighborState` | historial MAC→puerto / IP→MAC y estado de cada vecino LLDP/CDP para avisos |
| `fxi_images` | `FxiImage` | imágenes .fxi de WatchGuard |
| `ai_usage`, `ai_chats` | `AiUsage`, `AiChat` | límites y conversaciones del asistente IA |
| `settings` | `Setting` | ajustes clave → valor (ver abajo) |

## Migración al actualizar

`init_db()` corre en cada arranque y **solo agrega**:

1. `Base.metadata.create_all(engine)` crea las tablas nuevas.
2. `_add_missing_columns()` compara cada modelo con la tabla real y hace `ALTER TABLE … ADD COLUMN` de lo que falte,
   con su valor por omisión si es simple (booleano, número o texto).
3. `_add_missing_indexes()` crea los índices declarados en `__table_args__` que no existan (`CREATE INDEX` con
   `checkfirst`). `create_all` solo crea índices junto con su tabla, por eso hace falta este paso.

Para **agregar una columna**: declárela en el modelo con `default=` (si lo necesita) y listo. Para **agregar un
índice**: póngalo en `__table_args__` del modelo con un nombre propio (`ix_<tabla>_<qué>`). Para renombrar o
transformar datos no hay mecanismo automático: escriba la migración (una función idempotente llamada desde
`init_db`, como `_migrate_themes`) y su prueba en `tests/update-test.sh`.

Nunca borre una columna del modelo en la misma versión que deja de usarla: los servidores que vuelvan a la versión
anterior (`rollback-update.sh`) la necesitan.

## Ajustes (`settings`)

- `get_setting(clave, por_omisión)` / `set_setting(clave, valor)`. Las claves de `SECRET_SETTINGS` (contraseñas de
  SMTP, LDAP, tokens de Git, claves de IA y de NVD…) se guardan cifradas.
- Los ajustes que se leen en cada petición usan `auth.cached_setting` (caché de 30 s; `clear_settings_cache()` al
  guardar la página de Ajustes).
- La página **Administración › Ajustes** (`templates/settings.html`) guarda las claves de `SETTING_KEYS` y
  `CHECKBOX_KEYS` de `app/main.py`, con sus valores de fábrica en `SETTING_DEFAULTS`. Para un ajuste nuevo:
  agréguelo ahí, al formulario y, si se lee en cada petición, use `cached_setting`.

## Cifrado (`app/crypto.py`)

- `encrypt()` / `decrypt()`: AES-256-GCM con un nonce aleatorio de 12 bytes por valor; se guarda en base64
  (nonce + texto cifrado). La clave es `secret.key` (32 bytes; se crea sola la primera vez).
- `decrypt()` lanza `KeyMismatch` si el dato se cifró con otra clave (por ejemplo, una base restaurada sin su
  clave): la interfaz lo explica en lugar de fallar. `try_decrypt()` devuelve `None` en ese caso.
- Las contraseñas de usuario son `pbkdf2_sha256` con sal (`hash_password`, `needs_rehash` las actualiza al entrar).
- La cookie de sesión se firma con un secreto derivado de la clave (`session_secret`).

**Si se pierde `secret.key` se pierde todo lo cifrado** (configuraciones y credenciales). La copia de recuperación
(`app/sysbackup.py`, `python -m app.cli system-backup --with-key`) puede incluirla.

## Retención y tamaño

- `retention_versions` (Ajustes): cuántas copias guardar por equipo y tipo; la purga corre a las 03:30 y nunca borra
  las copias con etiqueta (`label`, p. ej. la línea base).
- Un respaldo igual al anterior no crea copia nueva (se compara el texto normalizado, `services.same_config`): solo
  actualiza la fecha del equipo y, si faltaba, el firmware de la última copia.
