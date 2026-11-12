# 7. Pruebas y laboratorio

Hay tres niveles de prueba. Los dos primeros corren solos en cada cambio; el tercero se hace a mano cuando se quiere
dar por **verificada** una receta o una plataforma.

## 1. Pruebas automáticas (`tests/`)

- `unit_*.py`: sin red ni servidor. Prueban una función o un módulo (lectores de inventario, puertos, topología,
  reversión, clasificación de comandos…). Se ejecutan con `python tests/unit_x.py`.
- `e2e_*.py`: contra un OpenNCCM real en `127.0.0.1:8000` y el **Cisco simulado** (`tests/fakedev.py`, SSH en el
  puerto 2222; el 2223 con `--legacy` ofrece solo ssh-rsa y kex SHA-1). Recorren la web como un usuario (con
  `requests`) y revisan el HTML, la base de datos o la API.
- Todas siguen el mismo molde: `check(nombre, condición, detalle)` imprime `OK` o `FAIL`, y al final el script sale
  con código 1 si algo falló.

```bash
bash tests/run-tests.sh .venv/bin/python   # crea un directorio de datos temporal, el admin, arranca el
                                           # servidor y los dos equipos simulados, y corre la lista completa
```

Para correr una sola prueba e2e, arranque el servidor como hace `run-tests.sh` (variables `NCM_DATA_DIR`,
`NCM_KEY_FILE`, `NCM_SYSLOG_PORT=5514`, `NCM_FW_CHECK_NEW=0`; admin `admin`/`Admin12345`; `python -m app.cli seed`)
y luego `python tests/e2e_x.py`. Algunas e2e dependen de otras anteriores (por ejemplo `e2e_legacy` usa la
credencial que crea `e2e`): respete el orden de la lista.

**Una prueba nueva** se agrega al final de la lista de `tests/run-tests.sh`. Cada arreglo de un error lleva una
prueba que fallaba antes del arreglo.

`tests/fakedev.py` simula varios sistemas con opciones (`--edgeswitch`, `--exos`, `--shell=pfsense|opnsense|…`,
`--enable-pass=X`): mire su docstring antes de escribir un simulador nuevo.

## 2. Integración continua (`.github/workflows/ci.yml`)

En cada *push* y *pull request*, GitHub Actions corre (15 trabajos):

| Trabajo | Qué prueba |
|---|---|
| Revisión de código | `shellcheck` de los scripts y `pyflakes` de todo el Python |
| Aplicación (Ubuntu 22.04 y 24.04) | `tests/run-tests.sh`, OpenSSH antiguo real y restaurar una copia con PostgreSQL |
| Directorios reales | Samba AD (LDAP) y Keycloak (OpenID Connect) |
| Servidores Linux reales | respaldar Ubuntu 26.04/24.04/22.04 y Debian 13 por SSH (contenedores oficiales) |
| MikroTik real | RouterOS CHR en QEMU/KVM: cambio no autorizado, reversión en modo seguro, fallo a mitad |
| Zabbix real | importar equipos desde Zabbix 6 y 7 |
| Instalación y actualización | `install.sh` desatendido, certificado propio, nginx, zona horaria, actualización desde la versión anterior y vuelta atrás |

Una versión no se publica si algún trabajo falla. Hay dos pruebas que a veces fallan por tiempos en las máquinas de
GitHub (`unit_db_pool` en Ubuntu 22.04 y `e2e_console2`): si fallan solas, vuelva a correr solo los trabajos
fallidos antes de buscar un error.

## 3. Laboratorio con equipos virtuales (GNS3) y el sello «Verificada»

Para afirmar que una receta, un configlet o una función funciona **en un sistema de verdad**, se prueba en el
laboratorio: máquinas virtuales de los fabricantes en GNS3, manejadas por un OpenNCCM de prueba como lo haría un
usuario (por la web y la API), sin tocar equipos de producción.

### Cómo está armado

- Un proyecto de GNS3 con una **nube** conectada a la red del laboratorio, un switch de gestión y, por plataforma,
  **dos equipos** (`X-1` principal y `X-2`) unidos por dos puertos de datos (para «restaurar en otro», el mapa por
  LLDP y el aviso de vecino perdido). La gestión de cada equipo sale por DHCP de la red del laboratorio.
- Un **OpenNCCM de prueba** en otro puerto (con su propio directorio de datos) que recibe el syslog de los equipos.
- Un script de **primer arranque** por plataforma que entra por la consola de GNS3 (telnet), pone la contraseña del
  laboratorio, el nombre, la gestión por DHCP y SSH, y muestra la IP.

### Las etapas

Cada ronda corre las mismas etapas en cada plataforma, y anota cada comprobación (OK/FALLA con su detalle):

| Etapa | Qué se comprueba |
|---|---|
| 01 Alta, respaldo e inventario | respaldo completo, versión, modelo y serie; un segundo respaldo igual no crea versión |
| 02 Firmware | «Comprobar ahora» lee la versión del propio equipo |
| 04 Consola web | abrir la consola SSH en el navegador, ejecutar un comando y que quede grabada |
| 05 Multi-equipo | una consulta en los dos a la vez; un cambio no se ejecuta directo (pide solicitud) |
| 06 Configlet con variables | solicitud con variables, aprobación, aplicación y el respaldo de después |
| 07 Cambiar el nombre | el prompt cambia y la solicitud termina bien |
| 08 Configuración grande | decenas de comandos en una solicitud y el respaldo los tiene todos |
| 09 Comando inválido | la solicitud queda como fallida (no «bien») |
| 10 Syslog | un cambio hecho por fuera dispara un respaldo de origen «syslog» |
| 11 Reversión automática | un cambio no autorizado se quita solo |
| 12 Volver a una versión | la comparación lo muestra y la reversión lo quita |
| 13 Borrar y restaurar | borrar el equipo, volverlo gestionable y restaurar la copia en texto |
| 14 Restaurar en otro | clonar la copia de X-1 en X-2 (mismo modelo) |
| 15 Configuración de referencia | encontrar diferencias y prepararlas para igualar |
| 16 CVE | buscar vulnerabilidades de esa versión |
| 17-18 Mapa y vecino perdido | el enlace entre los dos por LLDP/CDP; apagar los puertos da la alarma y al volver se cierra |
| 20 Buscar MAC o IP | dónde se ve una MAC/IP |
| 21 Puertos | la franja de puertos de la ficha |
| 22 Cumplimiento | el paquete de la plataforma se aplica |
| 23 Recetas | **cada receta de la plataforma**: todos sus pasos por solicitud y su comprobación por Multi-equipo; después se deshace lo que hizo |

Lo que una plataforma no admite por diseño se anota como «no aplica» con su motivo, y se comprueba que OpenNCCM lo
explique (por ejemplo, FortiOS no tiene reversión por comandos: se verifica que «Volver a esta versión» lo diga y
no arme comandos).

### El sello

Una receta (o configlet, o corrección de cumplimiento) recibe el sello **«Verificada · <sistema> <versión>»**
cuando pasa **3 rondas seguidas sin fallos** en esa versión. Se escribe en `recipes.VERIFIED` (o `verified.py`) con
un comentario de dónde y cuándo se probó:

```python
# Juniper Junos 14.1R1.10 (vMX en GNS3), 3 rondas seguidas sin fallos, 2026-11-09
"junos-desc": "Junos 14.1R1.10", ...
```

Una falla de la prueba (no del producto) no cuenta: se arregla la prueba y se vuelven a correr 3 rondas. Una falla
del producto se arregla en OpenNCCM, con su prueba automática (`tests/unit_*.py`) para que no vuelva, y también se
repiten las 3 rondas.

### Lo que el laboratorio ya encontró (y quedó como prueba automática)

- Junos: la serie sale de `show chassis hardware`; un vMX sin serie no toma «VMX» como serie (`unit_lab_multi`).
- OPNsense: el aviso de entrada llega en trozos y no se confunde con el prompt (`unit_lab_multi`).
- RouterOS 6: la pantalla de avance de `/certificate sign` no es el prompt (`unit_ros_progress`).
- FortiOS: el `end` de los bloques `config …` no se quita de las solicitudes; `diagnose sys ntp status` y
  `diagnose sniffer packet` son consultas; la configuración de referencia no arma comandos al estilo Cisco
  (`unit_wrappers`).
- Junos (vQFX): `family inet dhcp vendor-id …` se borra desde `dhcp` al revertir; y un commit que falla descarta la
  configuración candidata con `rollback 0`, porque en Junos es compartida y el cambio siguiente la arrastraba
  (`unit_junos_commit`).

### Reglas del laboratorio

- Solo equipos virtuales o de prueba del laboratorio, nunca de un cliente.
- La contraseña del laboratorio vive en un archivo fuera del repositorio; los scripts no la imprimen.
- Lo que cada prueba cambia se deshace al terminar (la siguiente ronda empieza igual).
- Algunas imágenes necesitan funciones del hipervisor: vJunos-switch exige KVM anidado («Nested KVM is not enabled,
  shutting down»); el vQFX necesita su RE (máquina clásica, adaptadores e1000) y su PFE unidos por `em1`.
