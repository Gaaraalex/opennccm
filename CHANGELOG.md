# Historial de versiones

## 1.5.0
Lo aprendido con el Ubiquiti EdgeSwitch, revisado y aplicado a todas las marcas:
- **Errores de todos los fabricantes**: se reconocen los mensajes de rechazo de Cisco/Arista/Dell/Allied/Extreme,
  HP Comware/Mellanox/A10, Huawei, Juniper, MikroTik, FortiGate, Palo Alto, Nokia, IOS-XR, VyOS/EdgeRouter,
  Check Point y Broadcom FastPath. Antes algunos (p. ej. MikroTik o FortiGate) podían quedar como «OK».
- **Un comando a la vez en todas las plataformas**: al primer comando rechazado se detiene, no guarda
  (ni hace commit) y dice qué comando falló y cuáles se aplicaron. En plataformas con commit (Junos, IOS-XR, VyOS,
  EdgeRouter, Huawei VRP8, Nokia SRL, PAN-OS) descarta los cambios pendientes. Si un comando pide confirmación
  («[confirm]», «(y/n)»), no se responde: se detiene y lo informa.
- **Sin falsos cambios**: se quitan en todas las plataformas las líneas con fecha/hora o contadores (MikroTik,
  Junos, Huawei, Nokia, Check Point, Arista, ASA, Mellanox, Dell/Netgear...). En FortiGate, las contraseñas que el
  equipo vuelve a cifrar en cada lectura («ENC ...») ya no generan una versión nueva (se guardan tal cual).
- **Reversión más segura en Cisco, Huawei y similares**: los ajustes de un solo valor (descripción, IP, VLAN de
  acceso o permitidas, hostname, usuario) se reescriben sin negarlos antes (un «no ip address» en la interfaz de
  gestión cortaba la conexión); las VLAN nuevas se crean primero y las que sobran se borran al final; en Huawei,
  `vlan batch` se compara como conjunto.
- **Estilo FastPath detectado automáticamente**: Dell N-series (OS6/PowerConnect), Netgear, Broadcom ICOS, Quanta y
  UniFi Switch usan bloques cerrados con `exit` sin sangría; su reversión ahora usa las mismas reglas que el EdgeSwitch.
- **Respaldos incompletos**: además de «--More--», se rechaza una configuración estilo FastPath sin su `exit` final y
  una de Cisco IOS/XE, Arista o ASA sin su `end` final.
- **Modo usuario sin enable**: si un equipo (Cisco y similares) entra en modo «>» y la credencial no tiene contraseña
  de enable, se prueba la de ingreso; si no sirve, el error dice en qué credencial escribirla.

## 1.4.2
- **Errores del EdgeSwitch detectados**: las respuestas «Failed to set VLAN participation…», «An invalid…» y similares
  ahora marcan la solicitud como fallida. Antes se mostraba «OK» y se ejecutaba `write memory` igualmente.
- **EdgeSwitch: un comando a la vez**: los cambios se envían de uno en uno; al primer comando rechazado se detiene,
  no guarda la configuración y el informe indica qué comando falló y cuáles ya se aplicaron.
- **Respaldo tras un cambio fallido**: si un cambio falla a mitad, se respalda el equipo para registrar su estado real.
- **Reversión FastPath en el orden correcto**: primero crea las VLAN que faltan, luego ajusta los puertos y al final
  borra las VLAN que sobran. Ya no intenta sacar los puertos de una VLAN que se va a borrar (desaparecen con ella).
  Antes borraba la VLAN primero y luego fallaba «vlan participation auto» en cada puerto.
- **Versiones incompletas bloqueadas**: revertir a una versión con menos de la mitad del tamaño de la actual (un
  respaldo cortado) ya no se ofrece con el botón normal; la página explica el riesgo y pide confirmación explícita.

## 1.4.1
- **Inventario al actualizar**: al iniciar, OpenNCCM relee la última salida de inventario guardada de cada equipo con el
  lector actual. Así el modelo, el número de serie y el firmware de equipos ya respaldados (p. ej. un Ubiquiti
  EdgeSwitch respaldado antes de la 1.4.0, con la serie como «......») se corrigen solos, sin esperar al próximo respaldo.

## 1.4.0
- **Tareas programadas sin cron**: se elige la frecuencia (todos los días, algunos días de la semana, una vez al mes,
  cada hora, cada pocos minutos) con hora y días en desplegables, y se ve en palabras («Cada lunes y jueves a las
  02:30») antes de crearla y en la tabla. El cron sigue disponible en «Avanzado».
- **Corrección**: en cron, el día de la semana `1` se ejecutaba el martes en lugar del lunes (y `0` el lunes en lugar
  del domingo). Ahora se usa el estándar de cron: 0 o 7 = domingo, 1 = lunes. Revise sus tareas con días numéricos.
- **Aprobación propia**: por defecto un administrador puede aprobar sus propias solicitudes (se puede desactivar en Ajustes).
- **Ubiquiti EdgeSwitch**:
  - inventario: modelo, número de serie y firmware se leen bien (antes el número de serie aparecía como «......»);
    lo mismo para otros equipos con formato «Campo....... valor»;
  - los respaldos ya no marcan «+2 −2» en cada lectura (se ignoran el tiempo encendido y la hora SNTP);
  - reversión nueva para su sintaxis FastPath: `vlan database` y modo privilegiado fuera de `configure`, bloques con
    `exit`, ajustes de un solo valor reescritos en lugar de borrados, `vlan participation auto` y sin tocar la IP de
    gestión. Antes la reversión fallaba en estos switches.
- **Respaldos incompletos**: una configuración cortada o paginada («--More--») ya no se guarda como versión; y al
  pedir una reversión a una versión mucho más pequeña que la actual se muestra una advertencia.
- **Grupos más fáciles de encontrar**: botón «Grupos» en Dispositivos, enlace «asignar grupo» en cada equipo y campo
  «…o crear un grupo nuevo» al editar un equipo.

## 1.3.2
- **Modo privilegiado (enable)**: corrige «Failed to enter enable mode. Please ensure you pass the 'secret'
  argument to ConnectHandler» en equipos cuyo driver entra a enable al conectar (p. ej. Ubiquiti EdgeSwitch).
  Si la credencial no tiene contraseña de enable, OpenNCCM prueba automáticamente con la contraseña de ingreso
  (muy habitual en estos equipos). Si tampoco sirve, el error ahora explica en español qué falta y en qué
  credencial escribirlo; también avisa cuando la contraseña de enable configurada es incorrecta.

## 1.3.1
- **Equipos antiguos por SSH**: corrige el error «no matching host key type found. Their offer: ssh-rsa» /
  «Incompatible ssh peer» al respaldar equipos como Ubiquiti EdgeSwitch, Cisco IOS 12.x o HP ProCurve antiguos.
  La librería SSH (Paramiko 5) dejó de incluir los algoritmos con SHA-1; OpenNCCM vuelve a aceptar `ssh-rsa`,
  `diffie-hellman-group14-sha1`, `diffie-hellman-group-exchange-sha1` y `diffie-hellman-group1-sha1`, siempre
  como **última opción**: los equipos modernos siguen negociando algoritmos modernos.
- Pruebas automáticas nuevas: respaldo de un equipo simulado que solo ofrece esos algoritmos y conexión a un
  OpenSSH real configurado igual.

## 1.3.0
- **Usuarios**: crear y editar ahora son acciones separadas (botón «Editar» en cada fila). Antes, si el desplegable
  «Modificar existente» tenía un usuario elegido, se modificaba ese usuario en lugar de crear uno nuevo.
  Mensajes claros de lo que se hizo, confirmación de contraseña, nombres duplicados rechazados y protección
  para no quitarse a sí mismo el rol de administrador.
- **Cambiar mi contraseña**: botón 🔑 junto al nombre de usuario en la barra superior.
- **Temas**: Automático, Claro, Oscuro, Azul noche, Bosque y Alto contraste; cada usuario elige el suyo en «Mi cuenta».
- **Icono** de OpenNCCM en la pestaña del navegador y en la barra superior (también al guardar como acceso directo en el teléfono).
- **Fecha y hora** del servidor desde Sistema: zona horaria, sincronización NTP y fecha/hora manual. El servicio sigue
  sin privilegios de root: el instalador agrega una regla de polkit que permite solo esas tres acciones.
- **Plataformas**: enlace en el menú, buscador y filtro por fabricante, comandos de arranque e inventario, y columna
  «Comprobado en su red» que se marca sola cuando un equipo de esa plataforma hace su primer respaldo correcto.
- Nuevo comando `sudo opennccm users` para listar usuarios desde la consola.

## 1.2.1
- Corrección: con `curl … | sudo bash` el asistente con menús no recibía el teclado (Enter no respondía).
  Ahora el instalador lo detecta, no modifica nada y muestra el comando correcto.
- La instalación guiada se hace descargando primero el instalador:
  `curl -fsSL …/install.sh -o install.sh && sudo bash install.sh`.
  La forma `curl … | sudo bash -s -- --unattended` sigue siendo válida para instalar sin preguntas.
- La prueba automática del asistente se ejecuta ahora como un usuario normal con sudo, igual que un administrador.
- Corrección: si el respaldo posterior a un cambio aprobado no podía ejecutarse (equipo ocupado), la ventana de
  mantenimiento quedaba abierta y los avisos syslog se retrasaban hasta que expiraba.
- El registro del servicio (`opennccm logs`) anota cada respaldo y cada decisión de reversión automática.

## 1.2.0
- Instalación con un solo comando desde GitHub (`curl … | sudo bash`), con asistente de menús o desatendida.
- El instalador comprueba el servidor (versión de Ubuntu, internet, disco, memoria, puertos) y actualiza
  Ubuntu e instala las dependencias que falten.
- Nuevo comando `opennccm`: `update`, `rollback`, `status`, `backup`, `restore`, `logs`, `uninstall`…
- `opennccm update` instala la última versión publicada, guarda una copia previa y vuelve sola a la versión
  anterior si la nueva no arranca.
- Opciones del asistente: dirección del servidor, zona horaria, HTTPS (autofirmado, Let's Encrypt o certificado
  propio), SQLite o PostgreSQL, cortafuegos, contraseña del administrador y correo.
- `/healthz` informa la versión y comprueba la base de datos.
- Pruebas automáticas en Ubuntu 22.04 y 24.04 en cada cambio: aplicación, instalación, asistente,
  actualización y vuelta atrás.
- Corrección: la reversión automática podía omitirse si un respaldo en curso había leído la configuración
  del equipo antes de activarla; ahora se vuelve a leer al decidir.

## 1.1.0
- 122 plataformas de unos 70 fabricantes (antes ~40) y perfil genérico con comando de respaldo personalizado.
- Fin de venta / fin de soporte (EOL/EOS): base por modelo, CSV y API Cisco EoX.
- Actualización remota de firmware (SCP + MD5 + arranque + reinicio) con aprobación.
- Paquetes de cumplimiento PCI DSS 4.0, HIPAA, SOX y Cisco IOS.
- Recuperación ante desastres: copias de la base, retención, SFTP y restauración.
- Línea base por equipo con detección de desviación.
- Comparación lado a lado.
- Página de Análisis con gráficas, informes en pantalla y envío programado por correo.
- Restricción de usuarios por grupos de dispositivos.
- Actualizaciones: copia previa automática y `rollback-update.sh`; versión visible en la interfaz.
- Correcciones: avisos syslog durante ventanas de mantenimiento se difieren en lugar de descartarse;
  la restauración ya no crea una base vacía antes de restaurar.

## 1.0.0
- Primera versión: respaldos, comparación, configlets, aprobación, reversión, cumplimiento, CVE, syslog,
  tareas, alarmas, auditoría, informes CSV.
