# 2. Mapa del código

Qué hace cada archivo de `app/`. La descripción es la primera frase del docstring del módulo: si cambia lo que hace un archivo, actualice su docstring y vuelva a generar esta página con `python docs/src/mapa_codigo.py`.

Otras carpetas: `app/templates/` (páginas Jinja2), `app/static/` (CSS, JS, iconos, logos de marca), `app/locales/` (traducciones), `tests/` (pruebas, capítulo 7), `scripts/` (utilidades de desarrollo), `tools/`, `docs/` (manuales y esta guía) e `install.sh` (instalador y actualizador, capítulo 8).

## Núcleo de la aplicación

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/main.py` | 3703 | Aplicación web OpenNCCM (FastAPI). |
| `app/web.py` | 489 | Utilidades compartidas por las rutas web. |
| `app/auth.py` | 173 | Sesión, roles y alcance: |
| `app/config.py` | 31 | Configuración del proceso por variables de entorno (NCM_*): |
| `app/db.py` | 701 | Base de datos: |
| `app/crypto.py` | 118 | Cifrado AES-256-GCM de credenciales y configuraciones almacenadas. |
| `app/i18n.py` | 300 | Idiomas de la interfaz. |
| `app/scheduler.py` | 255 | Tareas programadas (cron) y receptor syslog para detección de cambios en tiempo real. |
| `app/cli.py` | 226 | Utilidades de línea de comandos. |
| `app/errors.py` | 36 | Motivo corto y traducido de un error de conexión o de respaldo (Panel «Necesita tu atención», chat del asistente). |
| `app/metrics.py` | 73 | Rendimiento del servidor para el panel: |
| `app/security.py` | 142 | Verificación en dos pasos (TOTP), certificado propio del sitio y páginas públicas de privacidad y licencias. |
| `app/systime.py` | 93 | Fecha, hora y zona horaria del servidor (timedatectl). |
| `app/updates.py` | 145 | Aviso de versiones nuevas y actualización desde la web. |
| `app/sysbackup.py` | 215 | Recuperación ante desastres: |
| `app/__init__.py` | 2 | OpenNCCM: |

## Equipos: conexión, respaldo e inventario

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/services.py` | 1951 | Lógica de negocio: |
| `app/drivers.py` | 716 | Perfiles de fabricantes. |
| `app/shell_drivers.py` | 136 | Drivers de Netmiko propios para sistemas basados en un shell Unix que Netmiko no trae. |
| `app/legacy_ssh.py` | 83 | Compatibilidad SSH con equipos de red antiguos. |
| `app/fwtag.py` | 85 | Versión de firmware de cada copia de seguridad (ConfigVersion.fw_version). |
| `app/chassis.py` | 213 | Parte de atrás del equipo: |
| `app/ports.py` | 714 | Puertos físicos del equipo y su estado (ficha del equipo: |
| `app/porttable.py` | 378 | Lectores de la tabla de puertos de cada fabricante (app/ports.py los usa al respaldar). |
| `app/swos.py` | 75 | MikroTik SwitchOS (SwOS y SwOS Lite): |
| `app/fireware_web.py` | 196 | Feature key de WatchGuard (Fireware) en el formato que se pega para restaurarla. |
| `app/fxi.py` | 772 | WatchGuard Fireware: |
| `app/licenses.py` | 127 | Vencimiento de los servicios de la licencia guardada de cada equipo (Profile.license_cmd: |
| `app/bulkbackup.py` | 223 | «Respaldar todos» (y un grupo o los marcados) con su progreso: |
| `app/retire.py` | 253 | Equipos retirados (Dispositivos › Retirados): |
| `app/restore.py` | 222 | Restaurar una copia de seguridad (versión de configuración) en otro equipo del mismo modelo. |

## Cambios, reversión y consola

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/rollback.py` | 566 | Generación de comandos de reversión (current -> target) según la familia de sintaxis. |
| `app/rollback_vendor.py` | 506 | Reversión propia de plataformas que no siguen la sintaxis Cisco/Huawei/set: |
| `app/console.py` | 725 | Consola SSH web: |
| `app/consolecenter.py` | 838 | Operar › Consola: |
| `app/consolecore.py` | 363 | Lo que la consola web sabe de cada sistema: |
| `app/cmdref.py` | 423 | Comandos de cada equipo para la consola (autocompletado y frecuentes) y para el asistente IA, según su tipo, su modelo y su versión de firmware. |
| `app/nettools.py` | 266 | Herramientas de Operar › Consola con parámetros: |
| `app/favorites.py` | 250 | Comandos favoritos de la consola (Dispositivos y Operar › Consola). |
| `app/credview.py` | 169 | Ver el usuario y la contraseña de la credencial de un equipo. |
| `app/impact.py` | 448 | Análisis de impacto: |

## Recetas, configlets y cumplimiento

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/recipes.py` | 207 | Recetas incluidas en OpenNCCM (Operar › Consola › Recetas): |
| `app/recipes_more.py` | 266 | Más recetas incluidas: |
| `app/recipes_major.py` | 371 | Recetas de las familias principales: |
| `app/recipes_fw.py` | 315 | Recetas de firewalls y routers: |
| `app/recipes_gen.py` | 325 | Recetas estándar generadas por familia de CLI: |
| `app/recipes_rest.py` | 351 | Recetas de las plataformas que no tenían (firewalls, SD-WAN, transporte, OLT, routers japoneses y otros). |
| `app/recipes_ubnt_zte.py` | 287 | Recetas de Ubiquiti y ZTE: |
| `app/configlets_seed.py` | 237 | Configlets y scripts incluidos desde 2.34: |
| `app/configlets_ui.py` | 79 | Datos de la página Configlets y scripts en tres columnas (como Recetas): |
| `app/compliance_packs.py` | 414 | Paquetes de cumplimiento listos para usar: |
| `app/verified.py` | 63 | Sello «Verificada» de los configlets y de las correcciones de cumplimiento: |
| `app/refconfig.py` | 587 | Configuración de referencia por modelo (Dispositivos › Configuración de referencia). |

## Red: mapa, vecinos, MAC/IP e informes

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/topology.py` | 482 | Mapa de la red: |
| `app/topolayout.py` | 200 | Distribución del Mapa de la red: |
| `app/nodes.py` | 95 | Nodos de la red (Administración › Nodos): |
| `app/nbwatch.py` | 459 | Aviso de vecino nuevo o perdido (Ajustes › Mapa de la red, Alarmas y el mapa). |
| `app/macfind.py` | 451 | Buscar una MAC o una IP: |
| `app/ipam.py` | 157 | Inventario de IP y VLAN sacado de las configuraciones guardadas (no se ejecuta nada en los equipos). |
| `app/mapreport.py` | 163 | Informes › Equipos del mapa: |
| `app/netreports.py` | 383 | Informes › Puertos sin uso y Informes › Documento de la red. |
| `app/netdoc.py` | 624 | Documento de la red (Informes › Documento de la red): |
| `app/reports.py` | 218 | Informes tabulares (CSV en pantalla, descarga y envío programado por correo) y analítica. |
| `app/charts.py` | 248 | Gráficas SVG generadas en el servidor (sin librerías externas). |
| `app/health.py` | 121 | Panel: |

## Firmware, CVE y ciclo de vida

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/firmware.py` | 288 | Actualización remota de firmware: |
| `app/fwupdates.py` | 748 | Actualizaciones de firmware de los equipos: |
| `app/fwverify.py` | 157 | Versión instalada leída del propio equipo, sin respaldar, y comprobación después de actualizar el firmware. |
| `app/eol.py` | 147 | Fin de venta / fin de soporte (EOL/EOS): |

## Integraciones

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/extauth.py` | 279 | Inicio de sesión con directorios externos: |
| `app/zabbix.py` | 189 | Importar equipos desde Zabbix (API JSON-RPC, versiones 6.0 a 7.x). |
| `app/gitexport.py` | 145 | Copia de los respaldos en un repositorio Git (GitHub, GitLab, Gitea…): |
| `app/vendorlogos.py` | 70 | Logos de marca de los equipos (diseño de Diseño UX, app/vendor_logos.json y static/vendor-logos/*.svg). |

## Asistente IA y base de conocimiento

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/assistant.py` | 615 | Asistente IA (Administración › Ajustes › Asistente IA): |
| `app/aichat.py` | 763 | Chat del asistente en todo el sistema (botón «Asistente» de la barra superior). |
| `app/kb.py` | 1169 | Equipo › Base de conocimiento: |

## Rutas agrupadas y API

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/routes_ops.py` | 552 | Rutas: |
| `app/mobile.py` | 347 | API para apps móviles (/api/mobile/…): |
| `app/mobile_app.py` | 714 | La app móvil con servidor: |

## Utilidades

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/cronutil.py` | 164 | Programación de tareas: |

## Otros

| Archivo | Líneas | Qué hace |
|---|---:|---|
| `app/perms.py` | 165 | Permisos por usuario: |

## Dónde buscar

| Quiero cambiar… | Mire en |
|---|---|
| cómo se respalda un equipo | `services.backup_device` y el `Profile` de su tipo en `drivers.py` |
| cómo se aplica una solicitud de cambio | `services.execute_change_request` → `apply_config` → `_push_stepwise` |
| qué comando cuenta como consulta | `consolecore.is_change` (y `_QUERY` / `_DANGER` por familia) |
| los comandos de «Volver a esta versión» | `rollback.py` (familias) y `rollback_vendor.py` (RouterOS, EXOS, VOSS) |
| una receta | `recipes*.py` (formato en `recipes.py`) y su sello en `recipes.VERIFIED` |
| una regla de cumplimiento incluida | `compliance_packs.py` |
| el panel principal | `main.dashboard`, `reports.analytics`, `health.summary`, `templates/dashboard.html` |
| la lista de equipos | `main.devices`, `templates/devices.html` |
| la barra lateral y la superior | `templates/base.html`, `web.render` y `web._nav_counts` |
| un texto en inglés | `locales/en.json` |
| lo que ve la app móvil | `mobile_app.py` (pantallas) y `mobile.py` (sesión y acciones) |
