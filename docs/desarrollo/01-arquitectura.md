# 1. Arquitectura

OpenNCCM es **una sola aplicación web en Python** (FastAPI) que respalda, compara, audita y cambia la configuración
de equipos de red por SSH (Netmiko/Paramiko), con una base de datos (SQLite por omisión, PostgreSQL opcional) y
páginas HTML generadas en el servidor (Jinja2) con un poco de JavaScript propio, sin frameworks del lado del navegador.

```
Navegador ──HTTPS──> nginx ──HTTP──> uvicorn (1 proceso) ──> FastAPI app.main:app
                                         │                     ├─ rutas web (HTML)      app/main.py, app/routes_ops.py, …
App móvil ──HTTPS────────────────────────┤                     ├─ API REST y móvil      /api/…, app/mobile*.py
                                         │                     ├─ consola web (WebSocket) app/console.py
Equipos ──syslog UDP 514────────────────>│ receptor syslog     app/scheduler.py
                                         ├─ APScheduler (tareas programadas)           app/scheduler.py
                                         ├─ grupo de hilos (respaldos, cambios)        app/services.py «pool»
                                         └─ SQLAlchemy ──> SQLite (WAL) o PostgreSQL   app/db.py
                                                     └──> SSH/SCP/HTTP a los equipos   app/services.py, app/drivers.py
```

## Un solo proceso

El servicio `opennccm.service` arranca **un** proceso de uvicorn (`--workers 1`). Es a propósito: el planificador
de tareas, el receptor de syslog, la caché de ajustes, los trabajos de «Respaldar todos» y las sesiones de consola
viven en memoria de ese proceso. Si algún día se necesitan varios procesos, esas piezas tendrían que salir a un
proceso aparte (y la caché de ajustes, invalidarse entre procesos).

Dentro del proceso hay tres tipos de trabajo:

| Trabajo | Dónde corre | Ejemplos |
|---|---|---|
| Peticiones web | hilos del servidor (las rutas son funciones normales; FastAPI las corre en su grupo de hilos) | páginas, formularios, API |
| Trabajo largo con equipos | `services.pool` (`ThreadPoolExecutor`, tamaño `NCM_WORKERS`, 16 por omisión) | respaldos, solicitudes de cambio, auditorías |
| Tareas programadas | APScheduler (`app/scheduler.py`) | respaldos programados, purga de versiones, avisos, firmware |

Una ruta web **nunca** espera a un equipo: encola el trabajo en `services.pool` y vuelve enseguida; la página
consulta el avance (por ejemplo «Respaldar todos» en `app/bulkbackup.py` y `static/bulkbk.js`).

## Cómo viaja una petición

`app/main.py` crea `app = FastAPI(...)` y le pone, de fuera hacia dentro:

1. `_Gzip` — comprime las respuestas (menos las que llegan en vivo línea a línea, como ping o traceroute).
2. `SessionMiddleware` — cookie de sesión firmada (`opennccm`), 30 días como máximo.
3. `LangMiddleware` (`app/i18n.py`) — elige el idioma de la petición.
4. `MobileBridge` (`app/mobile_app.py`) — deja que la app móvil use las rutas web con su token (capítulo 6).
5. `security_headers` — cabeceras de seguridad (CSP, X-Frame-Options…), `no-store` en las páginas y caché larga en
   los estáticos con `?v=`.

Después, la ruta. Casi todas siguen el mismo patrón:

```python
@app.get("/devices")
def devices(request: Request, q: str = ""):
    user = require(request)                 # sin sesión → al login; sin la función (app/perms.py) → 403
    scope = device_scope(user)              # None = todos; si no, el conjunto de equipos que puede ver
    with SessionLocal() as db:              # una sesión corta de base de datos
        ...                                 # consultas (ver capítulo 9 para que sean rápidas)
    return render(request, "devices.html", user, devices=devs)   # app/web.py
```

`render()` (`app/web.py`) agrega a la plantilla lo común: el usuario, los contadores de la barra lateral
(`_nav_counts`), los avisos (`flash`), el tema, el idioma y las funciones globales (`icon`, `vlogo`, `_`, `_f`…).

## Arranque

`lifespan()` en `app/main.py`:

1. `init_db()` crea las tablas que faltan, agrega columnas nuevas e índices nuevos (`app/db.py`).
2. Carga los paquetes de cumplimiento y los configlets nuevos de la versión (`compliance_packs`, `configlets_seed`).
3. `scheduler.start()` arranca APScheduler y el receptor de syslog.

## Tareas programadas (`app/scheduler.py`)

| Tarea | Cuándo |
|---|---|
| Programaciones del usuario (respaldo, auditoría, CVE, configlet) | su cron (id `sched-<id>`) |
| Purga de versiones viejas | 03:30 |
| Limpieza de grabaciones de consola | 03:40 |
| Imágenes .fxi de WatchGuard | 03:00 |
| Base de conocimiento: casos vencidos / correo semanal | 08:00 / lunes 08:05 |
| Licencias por vencer | 08:10 |
| Métricas del servidor (CPU, memoria, disco) | cada 60 s |
| Versión nueva de OpenNCCM / de firmware | cada 24 h |
| Solicitudes de cambio con hora (ventana de mantenimiento) | cada minuto |
| Corrección automática de cumplimiento (`autofix.sweep`; también después de cada auditoría con fallos) | cada 5 minutos |

El cron de las programaciones pasa por `app/cronutil.py`: APScheduler numera los días de la semana distinto que
cron (0 = lunes) y ahí se corrige.

## Syslog: detección de cambios en tiempo real

El receptor UDP (puerto `NCM_SYSLOG_PORT`, 514) recibe los mensajes de los equipos. Si el mensaje dice que alguien
cambió la configuración, busca el equipo por la IP de origen y programa un respaldo con un pequeño retraso
(`_delayed_backup`), para que el cambio termine antes de leerlo. Ese respaldo queda con origen «syslog». Si el
equipo tiene la reversión automática activada y el respaldo (de syslog o programado) encuentra un cambio fuera de
una ventana de mantenimiento (es decir, que no vino de una solicitud aprobada), `services._auto_rollback` lo
revierte con los mismos comandos de «Volver a esta versión».

## Corrección automática de cumplimiento (`app/autofix.py`)

Apagada por defecto. Una política con `autofix` activado aplica sola, dentro de su ventana, las correcciones de las
reglas elegidas: solo reglas con el sello «Verificada» (`app/verified.py`) cuya huella (`Rule.autofix_sig`: tipo,
patrón y comandos) sigue siendo la aprobada. Va regla por regla y equipo por equipo: respaldo de antes, las mismas
comprobaciones que una solicitud sin aprobación (`services.example_addresses`, `services.lockout_risks`,
`impact.for_change`), `services.push_commands`, respaldo de después y, si el equipo rechazó algo o la regla sigue sin
cumplirse, `services.rollback_commands` a la versión de antes. Cada intento queda en `AutoFixRun` y bloquea esa regla
en ese equipo 24 h (`hold`, que «Reintentar» quita). El ajuste `autofix_paused` la detiene en todo el sistema. Las
rutas `/compliance/autofix/…` piden la función crítica `autofix` (`app/perms.py`). Cada activación queda a nombre de
quien la guarda (`Policy.autofix_by`) y `autofix.owner` lo comprueba en cada vuelta: solo corrige los equipos de
`auth.device_scope` de ese usuario y nada si ya no existe, está desactivado o perdió la función. Un equipo con
`Device.autofix_allowed` apagado (Editar equipo) nunca se corrige solo.

## Firmware en muchos equipos (`app/firmware.py`)

Una solicitud de firmware aprobada (`execute_firmware_request`) reparte los equipos en tandas (`plan_batches`): con
reinicio, primero el orden de `impact.waves`; cada grupo se parte en tandas de `variables["batch"]` equipos (0 = todos).
Cada tanda corre en su propio `ThreadPoolExecutor` (no en `services.pool`, que podría quedar ocupado esperando
reinicios) y, si tiene más tandas detrás y la solicitud reinicia, `upgrade_device(wait=True)` espera a
`fwverify.verify_after_upgrade` antes de devolver. Con `variables["stop"]` (por defecto), un fallo marca las tandas
siguientes como `stopped`. En cada equipo: respaldo, `free_space` (`dir` de Netmiko, `free-hdd-space`, `df -k`) contra
`need_bytes`, copia con avance, suma en el equipo (`file_transfer` de Netmiko con MD5; `device_hash` con
`sha256sum`/`md5sum` en airOS y EdgeOS) y arranque. Cada paso queda en `FirmwareJob` mediante `Progress` (la copia
escribe como mucho cada 1,5 s), y `GET /changes/{id}/firmware-progress` lo da a la página. `check_request`
(«Comprobar los equipos», `POST /changes/{id}/firmware-check`, función `firmware_install`) hace conexión, versión y
espacio sin copiar nada, en filas `kind="check"`.

## Directorio de datos y archivos del servidor instalado

| Ruta | Qué hay |
|---|---|
| `/opt/opennccm` | el código (un clon de Git) y `.venv` |
| `/etc/opennccm/secret.key` | la clave de cifrado (AES-256). **Sin ella no se puede leer nada de lo guardado** |
| `/etc/opennccm/opennccm.env` | variables de entorno del servicio |
| `/var/lib/opennccm` | base SQLite, grabaciones de consola, imágenes de firmware, exportación Git, documentos |
| `/etc/nginx/sites-available/opennccm` | proxy HTTPS (lo genera el instalador) |

Servicios de systemd: `opennccm.service` (la aplicación, sin privilegios, usuario `opennccm`),
`opennccm-webupdate.path/.service` (actualizar desde la web: corre como root cuando aparece `update.request`) y
`opennccm-fxi-firewall.path/.service` (abre el puerto FTP temporal de las imágenes .fxi).

## Variables de entorno (`app/config.py`)

| Variable | Por omisión | Para qué |
|---|---|---|
| `NCM_DATA_DIR` | `/var/lib/opennccm` | directorio de datos |
| `NCM_KEY_FILE` | `/etc/opennccm/secret.key` | clave de cifrado |
| `NCM_DATABASE_URL` | SQLite en el directorio de datos | p. ej. `postgresql+psycopg2://usuario:clave@host/opennccm` (instale `psycopg2-binary`) |
| `NCM_SYSLOG_PORT` / `NCM_SYSLOG_ENABLED` | `514` / `1` | receptor syslog |
| `NCM_WORKERS` | `16` | respaldos y cambios a la vez (y tamaño del grupo de conexiones a la base) |
| `NCM_NVD_API_KEY` | vacío | clave de la API de NVD (CVE) |
| `NCM_FW_CHECK_NEW` | `1` | buscar firmware nuevo al agregar un equipo (las pruebas lo apagan) |
| `NCM_SECURE_COOKIE` | `0` | cookie solo por HTTPS |
| `NCM_TLS_CA` | `/etc/ssl/opennccm/ca.crt` | CA propia del instalador (se descarga desde el login) |
| `NCM_UPDATE_REPO` | `Gaaraalex/opennccm` | de dónde se buscan las versiones nuevas |
| `NCM_BACKUP_PASSPHRASE` | vacío | frase para cifrar la copia de recuperación |
| `NCM_FORUM_URL`, `NCM_ISSUES_URL` | enlaces del proyecto | foro y reportes de recetas (los de soporte y donación son fijos) |

## Dependencias principales (`requirements.txt`)

FastAPI + uvicorn[standard] (web y WebSocket de la consola), SQLAlchemy (base de datos), Jinja2 (plantillas),
Netmiko (equipos por SSH/Telnet; trae Paramiko y scp), APScheduler 3 (tareas), cryptography (cifrado), requests
(NVD, GitHub, fabricantes), pyotp + segno (verificación en dos pasos y su QR), psutil (rendimiento del servidor),
ldap3 y pyjwt (Active Directory y Entra ID) y reportlab (documento de la red en PDF; el Word se arma sin librería).
No se agregan dependencias sin una buena razón: tienen que instalarse también en Python 3.10 (Ubuntu 22.04).
