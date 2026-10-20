# Historial de versiones

## 2.0.1
- **Consola SSH en un diálogo** sobre la página, como en el prototipo: Reconectar, Pantalla completa, cerrar y estado de la
  conexión. Un botón la abre en una ventana aparte (redimensionable) y Ctrl+clic, en una pestaña nueva. La ventana propia tiene la
  misma cabecera.
- **Activar la verificación en dos pasos**: el código QR y la clave ya no se solapan; el código se escribe en seis casillas (se puede
  pegar).
- **Ficha del equipo**: el menú ⋯ muestra siempre las opciones de línea base (sin línea base, permite marcar la última versión) y
  «Últimas versiones» de un equipo sin respaldos muestra bien su estado vacío.
- **Actualizador**: `opennccm update` ya no falla de vez en cuando con el código 141 (SIGPIPE) al leer las novedades del CHANGELOG
  ni al comprobar la configuración de git.
- Ctrl K: «Administración › Grupos» lleva a la pestaña Grupos.
- Manual de funciones, guía de instalación y capturas del README actualizados al diseño «Señal 2».

## 2.0.0
- **Interfaz rehecha pantalla por pantalla según el prototipo de diseño «Señal 2»**, con los datos reales de cada instalación:
  armazón, barra superior (búsqueda, tema y acento, notificaciones, ayuda), paneles laterales, diálogos y avisos con «Deshacer».
- **Ingreso, Panel, Dispositivos y ficha del equipo** con el nuevo diseño: línea de tiempo de versiones, comparación lado a lado
  en la propia ficha, menú ⋯ y paneles para agregar, importar y editar equipos.
- **Cambios, Configlets y Tareas**: Cambios se abre en «Por aprobar»; los configlets se ejecutan en un panel con vista previa en
  vivo y campos por variable; las tareas muestran la próxima ejecución en palabras («hoy 06:00», «lun 07:00»).
- **Cumplimiento**: mapa equipo × política, políticas con barra por gravedad y «Corregir» en cada regla, y un editor de
  políticas que guarda todas las reglas a la vez y prueba cada una en vivo.
- **Vulnerabilidades agrupadas por CVE**, con indicadores y la marca **«explotado activamente»** del catálogo KEV de CISA
  (se descarga como mucho una vez al día al escanear; sin conexión no se marca).
- **Firmware y ciclo de vida** en una página con pestañas: imágenes, actualizar equipos y fin de vida, con una barra del soporte
  restante de cada equipo y la base de datos filtrable por fabricante.
- **Alarmas** con chips por severidad y «Sin reconocer / Todas»; **Análisis** con mapa de calor, origen de los cambios y barras;
  **Informes** en tarjetas.
- **Administración** en pestañas con recuentos: usuarios con tarjetas de rol, credenciales con su estado real según los equipos,
  **Grupos** como pestaña propia, plataformas, auditoría, **Sistema** (gráficas de 24 h, espacio, actualizaciones, copias, fecha y
  hora) y **Ajustes** por secciones con barra de cambios sin guardar.
- **Mi cuenta** con apariencia, densidad de tablas, idioma, verificación en dos pasos y reglas de contraseña en vivo; Acerca de y
  Certificado con el nuevo diseño.
- Interfaz en inglés completa y accesibilidad mantenida (tema de alto contraste, teclado, barras con `role="meter"`).

## 1.9.0
- **Nuevo diseño «Señal»** en toda la interfaz. Barra lateral agrupada por tarea (Operar, Proteger, Observar, Administrar) con
  contadores de lo pendiente y opción de plegarla; Administración y Firmware + EOL en pestañas; buscador **Ctrl K** para ir a
  cualquier página, abrir un equipo o buscar en las configuraciones; avisos flotantes y paneles laterales para altas y ediciones.
  Fuentes e iconos propios servidos desde el propio servidor (sin conexiones externas) y marca nueva.
- **Temas**: Claro, Sala de control (oscuro) y Alto contraste, más un **color de acento** a elegir entre siete. Quien usaba
  uno de los temas de color anteriores conserva su color como acento.
- **Panel**: puntuación de **salud de la red** (0 a 100) con lo que la está bajando y una bandeja **«Qué hacer ahora»** con un
  botón para cada asunto. El rendimiento del servidor pasa a Administración → Sistema.
- **Dispositivos**: filtros por estado con contadores, acciones en cada fila, **acciones masivas** (respaldar, auditar y mover a
  un grupo) e **importación CSV en un panel lateral con vista previa** de las filas válidas y con error.
- **Ficha del equipo** en pestañas, menú ⋯ con las acciones menos frecuentes y confirmación escribiendo el nombre para eliminar.
  Las versiones se ven en una **línea de tiempo**: se pulsan dos para compararlas.
- **Comparación**: resumen **«Qué cambió»** en palabras, teclas **n/p** para saltar entre cambios y una minibarra con su posición.
- **Cumplimiento** agrupado por política con **«Corregir N»** (una solicitud de cambio por equipo con todas sus correcciones) y
  **prueba de reglas en vivo** contra la configuración de un equipo antes de guardarlas. Las severidades se muestran con forma y
  texto traducido.
- **Deshacer** al reconocer alarmas, barra **«Tienes cambios sin guardar»** en Ajustes y estados vacíos que explican el primer paso.
- Manual, guía de instalación y capturas del README actualizados.

## 1.8.0
- **Interfaz en español e inglés**. Cada usuario elige su idioma en «Mi cuenta → Idioma» o en la pantalla de ingreso, y queda
  guardado en su usuario. Sin elección se usa el idioma del navegador y, si no está disponible, el predeterminado que fija el
  administrador en «Ajustes». Se traducen menús, páginas, avisos, gráficas, programación de tareas y la consola SSH. Los datos
  (nombres de políticas, eventos ya registrados, auditoría, CVE) y los PDF siguen en su idioma original; los CSV mantienen los
  encabezados en español. Añadir otro idioma es completar un archivo de traducciones (`scripts/i18n_missing.py`).
- **Análisis renovado**: indicadores con tendencia y comparación con el período anterior, cumplimiento global con anillo de
  estado, actividad de cambios apilada por origen, mapa de calor «cuándo cambia la red» (día × hora), anillo con el origen de los
  cambios, cumplimiento por política con color e icono (✓ ⚠ ✕) y ciclo de vida en una barra al 100 %. Al pasar el cursor sobre
  cualquier barra, celda o segmento se ve su valor exacto.
- **Nueva pantalla de ingreso**: panel de presentación con las funciones principales, botón para ver la contraseña escrita, aviso
  de Bloq Mayús y selector de idioma. La verificación en dos pasos usa el mismo diseño. En el teléfono se muestra solo el formulario.

## 1.7.0
- **Extreme Networks EXOS: modelo y número de serie**. El número de serie no se leía cuando la línea «Switch : …» de
  `show version` trae varios espacios (como en la máquina virtual de GNS3), y el modelo no aparece en `show version`: ahora el
  inventario ejecuta también `show switch` y toma el modelo de «System Type». La serie de los equipos ya respaldados se corrige
  sola al reiniciar; el modelo aparece en el próximo respaldo.
- **pfSense y OPNsense**: perfiles propios. OpenNCCM entra por SSH, atraviesa el menú de consola (opción «8) Shell») y respalda
  el `config.xml` completo, con historial, comparación, alertas y cumplimiento como cualquier otro equipo. El inventario trae
  versión, modelo y número de serie. Use el usuario `admin`/`root`.
- **Alpine Linux**: perfil propio que respalda red, DNS, paquetes instalados (`apk world`), servicios, SSH y cortafuegos
  (iptables / nftables), con versión, modelo y serie en el inventario.
- **¿El equipo tiene interfaz web?**: al crear o editar un equipo se elige si tiene página de administración. Con «No» se oculta
  el botón 🌐 (en la lista y en la ficha); con «Sí» se puede indicar su dirección si no es `https://IP/`. Los equipos existentes
  siguen mostrando el botón.
- Los comandos de inventario de un perfil pueden ser varios (se muestran como «show version + show switch»).

## 1.6.2
- **Corrección: actualizar desde la web**. El botón «Actualizar» fallaba con «fatal: $HOME not set» (el servicio de
  systemd que ejecuta la actualización no tenía definida la variable HOME que necesita git). Ahora el servicio define HOME y
  PATH, y el comando `opennccm` también lo hace por su cuenta. La página de progreso muestra claramente si la actualización
  falló o si ya estaba al día, en lugar de quedarse en «Preparando la actualización».
  **Importante:** esta corrección se instala con la propia actualización, así que para pasar a la 1.6.2 hay que usar una vez más
  `sudo opennccm update` en la consola; desde la 1.6.2 el botón funciona.
- **Extreme VOSS**: ya no se registra un cambio en cada respaldo por las líneas «Command Execution Time: …» y «# <fecha y hora>»
  que el equipo agrega al mostrar la configuración (el primer respaldo tras actualizar guarda una versión sin esas líneas).
- **Cinco temas nuevos**: Arena, Océano, Atardecer, Lavanda y Grafito.
- **Legibilidad en temas oscuros**: los números de colores del panel (respaldos OK, errores, alarmas…) y los conteos +/− de
  líneas usan tonos claros en los temas oscuros. Los 10 temas cumplen el contraste WCAG 2.1 AA (prueba automática).

## 1.6.1
- **Consola en su propia ventana**: se abre en una ventana aparte del navegador (1000 × 640, centrada) que el usuario puede
  agrandar o achicar; el terminal ocupa toda la ventana y se adapta al tamaño.
- **Interfaz web del equipo**: icono 🌐 junto al de la consola y botón «🌐 Interfaz web» en cada equipo; abre su página de
  administración en una ventana aparte. Por omisión `https://IP/`; se puede indicar otra URL al editar el equipo.
- **Panel**: más espacio entre los indicadores y las gráficas de CPU y memoria; «Últimos cambios» y «Eventos recientes»
  muestran 10 elementos, 6 a la vista y el resto con barra de desplazamiento.
- **Eventos del equipo**: 3 a la vista y el resto con barra de desplazamiento, en lugar de una lista muy larga.
- **Sistema → Actualizaciones**: ventana «Novedades de cada versión» (historial de cambios y novedades de la versión nueva) y
  enlace a todas las versiones en GitHub; el aviso de actualización también enlaza a las novedades en GitHub.
- **Extreme Networks VOSS (VSP) y ERS**: el inventario usa `show sys-info` (VOSS no tiene `show version`), así que ahora se
  completan el modelo, el número de serie y la versión de firmware.

## 1.6.0
- **Consola SSH web**: icono ⌨ en la lista de equipos (después de «Estado») y botón «⌨ Consola» en cada equipo; abre un
  terminal en el navegador (xterm.js, incluido en el proyecto) conectado al equipo. Pide usuario y contraseña, o inicia sesión
  automáticamente con la credencial del equipo si el administrador lo permite en Ajustes (desactivado por omisión). Por omisión
  solo para administradores; cierre por inactividad (15 min); cada sesión queda en Auditoría.
- **Rendimiento del servidor en el Panel** (administradores): CPU, carga, memoria, swap, disco usado/libre/total, espacio de
  los datos, tiempo encendido y gráficas de CPU y memoria de las últimas 24 h.
- **Actualizar desde la web**: tarjeta «Actualizaciones» en Sistema (buscar ahora / actualizar con progreso en pantalla) y aviso
  emergente a los administradores cuando hay una versión nueva. Usa el mismo «opennccm update» (copia previa y vuelta atrás
  automática) a través de un servicio de systemd; la aplicación sigue sin privilegios de root.
- **Editar tareas programadas**: botón «Editar» que abre el asistente con los valores de la tarea.
- **Guardar la contraseña en el navegador**: el instalador crea una autoridad certificadora propia, restringida a este servidor,
  y firma con ella el certificado HTTPS. Instalándola una vez en el PC (página «Certificado» enlazada desde el login) desaparece
  el aviso «No es seguro» y el navegador ofrece guardar usuario y contraseña. Las instalaciones existentes migran al actualizar.
- **Seguridad**:
  - verificación en dos pasos (TOTP con app de autenticación), opcional por usuario y exigible a los administradores;
    `sudo opennccm reset-2fa USUARIO` para quien pierda el teléfono;
  - bloqueo de la cuenta 15 min tras 5 contraseñas incorrectas (además del bloqueo por IP);
  - cierre de sesión por inactividad (30 min, configurable) y al cambiar la contraseña en las demás sesiones;
  - contraseñas: mínimo 10 caracteres con letras y números, sin el nombre de usuario; PBKDF2-SHA256 con 600 000
    iteraciones (las existentes se reprotegen al ingresar);
  - cookie de sesión solo por HTTPS; cabeceras CSP, X-Frame-Options, nosniff, Referrer-Policy y sin caché en las páginas;
  - copias de recuperación que incluyen la clave de cifrado: el archivo se cifra con una contraseña (AES-256-GCM);
  - permisos estrictos de la clave, la base de datos y el archivo de configuración.
- **Privacidad, cookies y licencias**: página pública con los datos que guarda OpenNCCM, conexiones externas, la única cookie
  (técnica, sin necesidad de consentimiento), condiciones de uso, licencias de terceros y datos de la organización
  responsable (nuevos campos en Ajustes).
- **Accesibilidad**: etiquetas asociadas a los campos, nombres accesibles en botones de icono, enlace «Saltar al contenido»,
  foco visible con teclado, gráficas con descripción, y colores corregidos para cumplir el contraste WCAG 2.1 AA en los 6
  temas (prueba automática).
- **nginx**: soporte de WebSocket (consola) y conservación del certificado de Let's Encrypt al actualizar.
- **Corrección**: un segundo aviso de cambio por syslog del mismo equipo dentro del mismo minuto se descartaba, y ese segundo
  cambio podía quedar sin respaldar hasta la siguiente tarea programada. Ahora se agrupa en un respaldo adicional.

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
