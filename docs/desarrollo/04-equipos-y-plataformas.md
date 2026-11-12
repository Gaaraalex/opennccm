# 4. Equipos y plataformas

## El perfil de cada tipo de equipo (`app/drivers.py`)

Cada tipo de equipo es un `Profile` en la lista `PROFILES`. `get_profile(device_type)` lo devuelve
(`PROFILE_MAP`). Los campos que más se usan:

| Campo | Para qué |
|---|---|
| `key` | el `device_type` de Netmiko (o uno propio registrado en `shell_drivers.py`) |
| `label`, `vendor` | cómo se muestra |
| `backup` | comando que da la configuración en ejecución (`show running-config`, `/export`, `show configuration` de Junos en formato «set»…) |
| `startup` | configuración de arranque (si existe): se compara para avisar de cambios sin guardar |
| `inventory` | comandos de inventario (uno por línea); `version_re`, `model_re`, `serial_re` sacan versión, modelo y serie |
| `family` | sintaxis para la reversión: `IOS`, `HUAWEI`, `SET` (Junos, VyOS), `FASTPATH`, `MERGE` (sin reversión por comandos) |
| `commit`, `save` | si los cambios necesitan `commit` (Junos, PAN-OS, VyOS) o guardar (`write memory`) |
| `ignore` | regex de líneas volátiles que no cuentan como cambio (fechas, contadores) |
| `cpe`, `cpe_version`, `cpe_model` | cómo buscar sus CVE en NVD |
| `firmware` | cómo actualizar el firmware por SCP (sistema de archivos, arranque, recarga) |
| `license_cmd` | licencia que se lee una vez (WatchGuard) |
| `backup_timeout` | segundos para leer la configuración (OLT grandes) |

## El respaldo, paso a paso (`services.backup_device`)

1. Si el equipo está desactivado, no se conecta. Se toma un candado por equipo (`device_lock`): nunca hay dos
   respaldos del mismo equipo a la vez.
2. `connect(dev)` abre la sesión de Netmiko con su credencial (con los algoritmos SSH antiguos de
   `legacy_ssh.py` si hace falta). Equipos sin SSH (SwitchOS) van por HTTP (`swos.py`).
3. En **la misma conexión** se lee: la configuración (`backup`), la de arranque (`startup`), el inventario, los
   vecinos LLDP/CDP (`topology`), los puertos (`ports`/`porttable`), las tablas MAC/ARP (`macfind`) y, si falta, la
   licencia. Leer todo en una conexión es una regla: cada conexión cuesta segundos y algunos equipos limitan sesiones.
4. `clean_config()` quita lo volátil (`ignore`); se detecta la salida cortada (paginado, falta el `end` final).
5. Si es igual a la última copia (`same_config`), no se crea versión nueva. Si cambió, se guarda cifrada con su
   diferencia (+/−), su origen y el firmware leído (`fwtag`).
6. Se actualizan inventario, estado, vecinos (y avisos de vecino nuevo/perdido, `nbwatch`), puertos, conflicto con
   la de arranque y desvío de la línea base. (La diferencia con la configuración de referencia, `refconfig`, se
   calcula al mirarla.)
7. Después: exportación a Git (`gitexport`), búsqueda de firmware nuevo si el equipo es nuevo (`fwupdates`),
   auditoría de cumplimiento del equipo y, si fue un cambio no autorizado con la reversión automática encendida,
   `_auto_rollback`.
8. Si falló, `_check_and_retry` mira si el puerto responde y reintenta una vez; el motivo corto y traducido para la
   interfaz sale de `errors.short_error`.

## Aplicar cambios (`services.execute_change_request` → `apply_config`)

Una solicitud de cambio guarda el cuerpo como **plantilla Jinja2** (variables del usuario y `device.*`). Al
ejecutarse, por cada equipo:

1. `render_commands()` arma la lista de comandos (sandbox de Jinja2).
2. `push_commands()` abre una ventana de mantenimiento de 5 minutos (para que el syslog de este cambio no dispare la
   reversión automática) y llama a `apply_config()`.
3. Red de seguridad del equipo: **MikroTik** en modo seguro (Ctrl+X), **Junos** con `commit confirmed 5` (se vuelve a
   conectar y se confirma; si el cambio cortó el acceso, el equipo vuelve solo).
4. `_push_stepwise()` manda los comandos **de uno en uno**, lee la respuesta hasta el prompt y se detiene en el
   primero que el equipo rechace (`DEVICE_ERROR`, una regex con los mensajes de error de cada fabricante) o que pida
   confirmación (`CONFIRM`). No guarda ni hace commit si algo falló; en plataformas con commit, descarta lo pendiente.
5. Si el comando cambió el nombre del equipo, `_refresh_prompt` actualiza el prompt esperado.
6. Guarda (`save_config`) o hace commit, y al final se respalda el equipo (origen «push»).

Las solicitudes que vienen de una receta, de Multi-equipo o del asistente pasan antes por
`consolecenter.strip_wrappers()`, que quita `configure terminal`, `end`, `commit`… porque OpenNCCM ya entra al modo
configuración y guarda. **Excepción:** el `end` que cierra un bloque `config …` de FortiOS se conserva, porque en
FortiOS es lo que aplica el bloque.

## Consultas y cambios en la consola (`app/consolecore.py`)

Multi-equipo, la consola en modo solo lectura y las comprobaciones de las recetas necesitan saber si un comando solo
consulta. `os_of(device_type)` da la familia (`ios`, `junos`, `ros`, `fortios`…) y `is_change(os, cmd)` decide con:

- `_QUERY[familia]`: regex de lo que **consulta**. Lo que no coincide **cuenta como cambio**.
- `_DANGER[familia]`: lo que corta el servicio (reinicio, borrar la configuración) y pide confirmación aparte.
- El catálogo de comandos del tipo de equipo (`cmdref.py`): sus consultas conocidas cuentan como consulta.

`PAGING_OFF` (apagar el paginado) y `CONFIG_PROMPT` (reconocer el modo configuración) también van por familia.

## Volver a una versión (`app/rollback.py`, `app/rollback_vendor.py`)

OpenNCCM **no** reaplica la configuración entera: calcula la diferencia entre la actual y la de destino y escribe
los comandos para deshacerla, que el usuario revisa en una solicitud de cambio.

- `IOS` / `HUAWEI`: bloques por indentación; lo que sobra se niega con `no …` / `undo …`.
- `SET` (Junos, VyOS): `delete …` de lo que sobra y `set …` de lo que falta.
- `FASTPATH`: como IOS con sus modos (`vlan database`).
- Propias (`rollback_vendor.py`): MikroTik RouterOS (`/… remove`, `set`), Extreme EXOS (`unconfigure`, `delete`) y
  VOSS (`vlan delete`, `no …`).
- `MERGE` (FortiOS, PAN-OS…) y las de `MANUAL`: **sin reversión por comandos**; la interfaz explica cómo restaurar
  con la herramienta del fabricante. La reversión automática no se puede activar en esos equipos.

La IP de gestión del equipo nunca se toca al revertir (`mgmt`).

## Recetas (`app/recipes*.py`)

Guías paso a paso de Operar › Consola › Recetas. Formato (ver el docstring de `app/recipes.py`):

```python
{"key": "ios-ntp", "vendor": "Cisco", "os": "ios", "os_label": "Cisco IOS / IOS-XE", "cat": "servicios",
 "name": N_("Configurar NTP (Cisco IOS)"), "intro": N_("…"), "doc": "https://… (documentación oficial)",
 "vars": [{"n": "ntp", "l": N_("Servidor NTP"), "v": "10.0.0.1"}],
 "steps": [{"t": N_("Servidor NTP."), "c": "configure terminal\nntp server {{ntp}}\nend"},
           {"t": N_("Comprobar."), "c": "show ntp associations", "check": True}]}
```

- Los pasos que cambian algo se aplican con una solicitud de cambio; los de consulta y el de comprobar
  (`"check": True`) se ejecutan en el momento por Multi-equipo. Por eso **el comando de comprobación tiene que ser una
  consulta** para `consolecore` (si no, la comprobación no se puede ejecutar).
- `"warn": True` marca un paso que puede cortar el acceso o pedir confirmación.
- Cada receta se revisa contra la documentación oficial (`doc`).
- **Sello «Verificada»** (`recipes.VERIFIED`): versión en la que se probaron todos sus pasos en un equipo real o en
  el laboratorio, con 3 rondas seguidas sin fallos (capítulo 7). Un administrador puede poner o quitar el sello desde
  la interfaz (ajuste `recipe_verified`, queda en Auditoría).

## Configlets y scripts

Plantillas Jinja2 de comandos guardadas en la tabla `configlets` (filtro de tipo de equipo por regex). Los incluidos
se cargan con `configlets_seed.py` (`seed_new_configlets` al arrancar agrega los nuevos de cada versión). Su sello
«Verificada» está en `app/verified.py`.

## Cumplimiento (`app/compliance_packs.py`, `services.run_compliance`)

Una política tiene reglas `must_contain` / `must_not_contain` (regex multilínea sobre la configuración guardada), una
gravedad y, opcionalmente, los comandos de corrección. La auditoría corre después de cada respaldo y con las
programaciones. Los paquetes incluidos (PCI DSS, HIPAA, SOX, Cisco, MikroTik, Junos, FortiGate…) se cargan al
arrancar si faltan.

## Cómo agregar una plataforma nueva

Ejemplo: un switch que se maneja como Cisco pero con otro comando de configuración.

1. **Perfil** en `app/drivers.py` (`Profile(...)` en `PROFILES`): `key` de Netmiko, comandos de respaldo e
   inventario, regex de versión/modelo/serie, familia de reversión, `ignore`, `cpe`. Si Netmiko no lo trae y es un
   shell Unix con menú, mire `shell_drivers.py`.
2. **Logo** (opcional): la clave de su marca en `app/vendor_logos.json` y el SVG en `app/static/vendor-logos/`;
   regenere el sprite con `python scripts/build_vendor_logos.py`.
3. **Familia de la consola** en `consolecore.os_of` y, si no es una ya conocida, su `_QUERY`, `_DANGER`,
   `PAGING_OFF` y `CONFIG_PROMPT`. Lo que no se defina usa `generic` (seguro: casi todo cuenta como cambio).
4. **Comandos de la consola y del asistente** en `cmdref.py` (autocompletado, frecuentes, consultas conocidas).
5. Si muestra vecinos LLDP/CDP: su comando y su lector en `topology.py`; puertos en `porttable.py`; tablas MAC/ARP
   en `macfind.py`; IP y VLAN en `ipam.py`. Cada uno es opcional: lo que no se reconoce no cuenta.
6. **Reversión**: si su sintaxis es de una familia conocida, basta la `family`. Si no, una función en
   `rollback_vendor.py` o `MERGE` (sin reversión por comandos, se explica cómo restaurar).
7. **Recetas** (`recipes_*.py`) y **paquete de cumplimiento** (`compliance_packs.py`), revisados contra la
   documentación oficial.
8. **Pruebas**: un caso en `tests/unit_vendors.py` (perfil, inventario), lo que agregue en topología/puertos/IPAM
   en sus `unit_*.py`, y si puede, el laboratorio (capítulo 7) para sellar recetas.
9. **Documentación y registro**: el manual de funciones (`docs/src/manual-funciones.html`) si cambia lo que ve el
   usuario, `CHANGELOG.md` y las traducciones de los textos nuevos en `app/locales/en.json`.
