# Historial de versiones

## 2.43.0
- [seguridad] **Consola: las contraseñas ya no quedan en las grabaciones:** Lo que se escribe cuando el equipo pide una
  contraseña («Password:» de enable, «Contraseña:»…) va al equipo pero no a la grabación: queda `********` al reproducirla,
  al exportarla y al buscar. Las grabaciones anteriores, que la guardaban tal cual, también se muestran ocultas. En la consola
  ese texto se escribe oculto y no entra en el historial de comandos.
- [nuevo] **Eliminar varios equipos a la vez:** En Dispositivos, al marcar uno o varios equipos, la barra de abajo tiene
  «Eliminar…». Pide escribir ELIMINAR y borra los equipos con sus versiones, resultados y eventos.
- [nuevo] **Ping desde la lista de equipos:** Al pasar el puntero sobre un equipo, junto a Consola, Interfaz web y Respaldar
  ahora, está Ping: el servidor envía tres paquetes y dice si responde, con la pérdida y el tiempo medio. Aparece también
  cuando el equipo tiene error de respaldo.
- [mejora] **Copiar todo en la versión de una copia:** Un botón copia la configuración entera al portapapeles, sin los
  números de línea.
- [mejora] **Las copias muy grandes se abren al instante:** La versión de una OLT C300 (40.000 líneas) tardaba unos
  segundos en verse; ahora la página solo dibuja lo que está a la vista y pesa menos de la mitad.
- [mejora] **Herramientas de la consola con botones de parámetros:** Debajo de la casilla, un botón por cada parámetro de
  Ping, Traceroute, Puerto TCP y DNS: se escribe la IP o el nombre y se tocan los que se quieran (otra vez, se quitan).
- [mejora] **Soporte profesional y Apoya OpenNCCM siempre visibles:** Los dos botones de la barra superior se ven para todos
  los usuarios y ya no se pueden ocultar (Ajustes › Organización ya no tiene la casilla).

## 2.42.0
- [nuevo] **OLT ZTE C300 y C320: el chasis entero en la ficha:** En lugar de la franja de puertos con barra de desplazamiento,
  la cabecera muestra el chasis completo: la C300 con las tarjetas de pie (ranuras 2 a 20) y la C320 acostada, con la fuente
  abajo. Las controladoras dicen si están activas o en espera, las ranuras vacías salen punteadas y el número de una ranura va
  en rojo si más del 10 % de sus ONU están sin señal. Al tocar una ranura se ven sus puertos con las ONU en línea de cada uno;
  «Ver todos los puertos» abre Puertos filtrada por esa ranura y «Ver las N ONU» abre el puerto con sus ONU del respaldo.
- [mejora] **La ficha de una OLT grande abre al instante:** En una OLT C300 con miles de ONU (configuración de 40.000 líneas)
  la ficha tardaba casi un minuto en abrir, porque comparaba las copias grandes línea por línea varias veces. Ahora abre en
  menos de un segundo, con la misma comparación. Respaldar esos equipos también es más rápido.
- [arreglo] **Firmware de cada copia:** En la ventana de una versión, la marca «de aquí sale el firmware» va en la misma línea
  de la que sale la versión, a la derecha. En el historial, una copia programada sin usuario dice «Programado» (antes
  «scheduled»).

## 2.41.0
- [nuevo] **Firmware en muchos equipos a la vez:** Las solicitudes de firmware con varios equipos van en tandas («Equipos
  a la vez»: de a 1, 2, 5, 10, 20 o todos). Con reinicio, cada tanda espera a que los equipos de la anterior vuelvan con
  la versión nueva, y si uno falla las siguientes no empiezan (se puede desmarcar). Antes de copiar se mira el espacio
  libre donde va la imagen (`dir flash:`, `free-hdd-space`, `df -k`) y, si no alcanza, ese equipo no se toca. Después de
  copiar, el propio equipo calcula la suma del archivo (MD5 en Cisco, Arista y Junos; SHA-256 o MD5 en airOS y
  EdgeRouter; en RouterOS, que no calcula sumas, se compara el tamaño) y si no coincide no se instala. Los equipos que ya
  tienen esa versión no se tocan. La solicitud muestra al vivo el avance de cada equipo: su tanda, el paso (respaldando,
  copiando con su porcentaje, comprobando la suma, instalando, reiniciando, comprobando la versión) y cómo terminó.
- [nuevo] **Comprobar los equipos antes de aprobar:** En una solicitud de firmware pendiente, «Comprobar los equipos» se
  conecta a cada uno sin copiar ni cambiar nada y dice si responde, qué versión tiene y si hay espacio para la imagen.
- [mejora] **Suma del fabricante al cargar una imagen:** Al cargar una imagen de firmware se puede pegar el MD5 o el
  SHA-256 que publica el fabricante: si el archivo no coincide no se guarda (ni reemplaza la imagen que hubiera con ese
  nombre). Además de su MD5, ahora se calcula su SHA-256.
- [mejora] **OLT ZTE: estado de cada puerto:** El frente de las OLT ZXA10 (C300, C320…) ahora muestra qué puertos están
  activos. Un PON está activo si tiene al menos una ONU en línea, y dice «12 de 14 ONU en línea» y cuántas están en LOS,
  DyingGasp u OffLine (`show gpon onu state`). Los uplinks muestran su enlace y su velocidad (`show interface`). Se lee en
  el respaldo: «Respaldar ahora» en cada OLT para verlo. Solo se guarda la cuenta por PON, no los datos de los clientes.
- [seguridad] **Corrección automática a nombre de quien la activa:** Cada política con corrección automática queda a
  nombre de quien la activó y solo corrige los equipos que esa persona puede ver (se comprueba en cada vuelta). Si esa
  persona se elimina, se desactiva o pierde la función «Corrección automática», la política deja de corregir y la página
  lo avisa. Las políticas activadas en 2.40.0 no tienen dueño: dejan de corregir hasta que alguien las vuelva a guardar.
- [nuevo] **«Permitir corrección automática» por equipo:** En Editar equipo, un interruptor (encendido por defecto) deja
  fuera de la corrección automática a un equipo concreto, sin tocar las políticas.
- [arreglo] **Inglés:** La corrección automática nombraba «Remediate» al botón que en la interfaz se llama «Fix».

## 2.40.0
- [nuevo] **Corrección automática de cumplimiento (opcional):** En Cumplimiento › Corrección automática, cada política
  puede corregir sola los equipos que incumplen las reglas elegidas, dentro de una ventana de días y horas. Solo entran
  reglas con el sello «Verificada», y si alguien cambia su patrón o sus comandos dejan de aplicarse hasta volver a
  elegirlas. En cada equipo y regla respalda antes, hace las mismas comprobaciones que una solicitud de cambio
  (direcciones de ejemplo, quedarse sin acceso, reinicios y puertos apagados: si hay algo, no aplica nada y queda
  «Bloqueada»), aplica, vuelve a respaldar y audita; si el equipo rechaza un comando o la regla sigue sin cumplirse,
  vuelve a la versión de antes. Historial con el resultado, el motivo, la salida y las versiones de antes y después,
  además de Eventos y Auditoría; no insiste más de una vez en 24 h por regla y equipo («Reintentar» lo libera), y
  «Pausar todo» la detiene al instante. Viene apagada en todas las políticas, y activarla es la nueva función crítica
  «Corrección automática» (de los administradores; el maestro la da o la quita).
- [mejora] **OLT ZTE: frente con sus puertos:** La ficha de las OLT ZXA10 (C300, C320…) ahora dibuja el frente por
  ranuras desde la configuración, cada tarjeta con su número y su tipo («Ranura 2 · GTGH»): los puertos PON con cuántas
  ONU tienen y los uplinks de 1 G y 10 G. Antes no mostraba ningún puerto. No se guardan datos de los clientes (nombres
  ni descripciones de las ONU).
- [arreglo] **OLT ZTE: número de serie:** La serie no está en «show system-group» ni en «show card»: ahora se busca la
  tarjeta de control (SMXA, SCXN…) en «show card» y se lee su «Serial-Number» con «show card slotno N». Sirve para la C320
  (ranura 3 o 4) y la C300 (ranura 10 u 11). Para verla, «Respaldar ahora» en cada OLT. Visto en una C320 con V2.1.0.
- [mejora] **OLT ZTE: vulnerabilidades:** NVD casi no registra las OLT ZTE (de la C320, ninguna), así que el «0» de la
  pestaña Vulnerabilidades no significa que no tenga: ahora lo avisa y enlaza a los boletines de seguridad de ZTE.
- [mejora] **Firmware sin fuente pública:** Cuando el fabricante no publica sus versiones (como ZTE), el aviso dice además
  que las fechas de fin de venta y de soporte se pueden cargar a mano en Firmware y ciclo de vida.

## 2.39.0
- [seguridad] **Permisos por usuario:** Al crear o editar un usuario, el rol (Lector, Operador, Administrador) es una
  plantilla y debajo está la lista de funciones del sistema por secciones (operación, cambios en los equipos,
  administración): se quitan o se agregan a gusto, por ejemplo un operador que no envía configuraciones o un lector que
  respalda. Cada ruta y cada botón obedecen a la función, no al rol. Al actualizar nada cambia: cada usuario sigue con
  las funciones de su rol.
- [seguridad] **Administrador maestro:** El primer administrador (al actualizar, el administrador activo más antiguo) es
  el maestro: nadie lo puede eliminar, bloquear, editar ni quitarle la verificación en dos pasos, y es el único que crea
  administradores y da o quita las funciones críticas (enviar configuraciones, revertir, restaurar, consola con cambios,
  firmware, aprobar, credenciales, usuarios y ajustes). Puede pasar el rol a otro usuario; si se pierde el acceso, en el
  servidor: «opennccm set-master USUARIO».
- [nuevo] **Interruptor «Enviar configuraciones»:** En la lista de usuarios, el maestro da o corta al instante a cada
  usuario el envío de configuraciones a los equipos (solicitudes, recetas, configlets, multi-equipo y correcciones).
- [arreglo] **Junos: respaldo sin permiso de lectura:** Si la clase de login del usuario no tiene «view-configuration», el
  equipo responde «/* ACCESS-DENIED */» en cada sección; antes se guardaba como una versión (que no se podía restaurar).
  Ahora el respaldo falla y dice qué permiso darle. Visto en un QFX5100 con 13.2X51-D25.3.
- [arreglo] **Junos: equipos que tardan en abrir la CLI:** Algunos muestran «--- JUNOS … built …» un rato antes del
  prompt y el respaldo fallaba con «Pattern not detected» aunque el usuario y la contraseña eran correctos. Ahora espera
  el prompt hasta 90 s. Visto en un switch con Junos 21.4R3-S5.4.
- [arreglo] **Alarma de respaldo fallido:** Un respaldo que fallaba por algo que no se reintenta (contraseña rechazada,
  sin permiso…) quedaba como «Respaldo reintentado» informativo en lugar de la alarma «Respaldo fallido».
- [mejora] **Frente de los switches Juniper QFX:** Los puertos salen en el orden del equipo (antes los de 1 G iban
  primero, como cobre), en dos filas como el frente real (pares arriba, impares abajo, en bloques de 12), con los
  QSFP+ en su bloque y los puertos sin módulo «sin módulo» (antes no aparecían: del 19 se pasaba al 43). Para verlo,
  «Respaldar ahora» en cada QFX.

## 2.38.0
- [arreglo] **Cisco IOS: restaurar con SNMP:** La reversión ya no escribe «snmp-server chassis-id» sin valor (IOS lo muestra en
  cuanto hay otra línea de SNMP y no se puede escribir así): la restauración se detenía en «% Incomplete command». Visto en
  un IOSv 15.6(2)T.
- [arreglo] **Cisco IOS: guardar tras cambiar de imagen:** El primer «write memory» después de cargar otra versión pregunta
  «Overwrite the previous NVRAM configuration?[confirm]»; ahora se contesta, en lugar de esperar hasta fallar (cambios,
  recetas y actualización de firmware quedaban sin guardar). Visto en un 7200 y un 3745.
- [arreglo] **Cisco ASA: restaurar una copia:** No se mandan los comentarios «: Serial Number…», «: Hardware…» ni
  «ASA Version …», y el «no snmp-server location» de fábrica no se niega cuando la copia trae la línea con valor. Visto en
  un ASAv 9.9.2.
- [arreglo] **Cisco ASA: configuración de referencia:** Los bloques («interface», «object network») van primero, cada uno
  con su «exit», y después las líneas sueltas que dependen de ellos («mtu puesto …», «route puesto …»); lo que sobra se quita
  antes de poner lo nuevo (el ASA no acepta un segundo «inspect dns …» sin quitar el que tiene) y la marca
  «call-home reporting anonymous prompt N» queda como propia del equipo.
- [arreglo] **Bloques de tres niveles:** Restaurar e igualar a la referencia escriben las líneas de tercer nivel entrando en
  su sub-bloque («policy-map › class › inspect», «router bgp › address-family › network»); antes se escribían en el bloque
  de arriba y el equipo las rechazaba.
- [arreglo] **Cisco ASA en la consola:** «packet-tracer» cuenta como consulta, y «write erase», «reload», «format» y
  «configure factory-default» se marcan como corte del servicio.
- [arreglo] **Receta «Activar SSH» en IOS:** Usa una clave RSA de 2048 bits («general-keys modulus 2048»), que existe en
  todas las versiones; la de 4096 no existe en IOS 12.x y la receta fallaba.
- [mejora] **Recetas verificadas en Cisco IOS / IOS-XE:** Las 10 recetas de IOS quedan con el sello «Verificada» y las
  versiones en las que pasaron 3 rondas seguidas sin fallos: IOSv 15.6(2)T, Catalyst 8000v 17.6.1a, 7200 15.3(3)XB12,
  3745 12.4(25d) (todas menos RADIUS, cuya sintaxis «radius server» no existe en 12.4) y 3620 12.2(26c) (las que no
  necesitan SSH, HTTPS ni «secret»).
- [mejora] **Recetas verificadas en Cisco ASA:** Las 11 recetas del ASA quedan con el sello «Verificada · ASA 9.9.2»
  (3 rondas seguidas sin fallos en un ASAv 9.9.2).

## 2.37.0
- [mejora] **Más rápido con muchos equipos:** Con 2000 equipos, el panel pasa de 1,4 s a 0,4 s, IP y VLAN de 1,7 s a 0,07 s,
  Analítica de 1 s a 0,08 s, Cumplimiento de 1,1 s a 0,35 s y la búsqueda en las configuraciones de 0,8 s a 0,2 s. Los
  indicadores se cuentan en la base de datos, se leen solo los datos que cada página usa y hay índices nuevos (se crean
  solos al actualizar).
- [mejora] **Páginas más livianas:** Las respuestas van comprimidas (la lista de 2000 equipos pasa de 4 MB a 200 KB) y el
  navegador guarda los estilos y los scripts hasta la versión siguiente, en lugar de volver a pedirlos en cada página.
- [mejora] **Firmware en un respaldo sin cambios:** Si la configuración es la misma y la última copia es de antes de registrar
  el firmware, esa copia recibe la versión leída en la misma conexión (las copias más viejas siguen «sin registrar»).
- [arreglo] **FortiGate: los cambios no se aplicaban:** Las recetas, Multi-equipo y el asistente quitaban el «end» de los
  bloques «config … end», que en FortiOS es lo que aplica el bloque: el cambio no quedaba hecho aunque la solicitud terminaba
  «bien» (por ejemplo, cambiar el nombre). Visto en un FortiGate-VM 7.4.2.
- [arreglo] **FortiGate: comprobación de las recetas:** «diagnose sys ntp status», «diagnose sniffer packet» y los demás
  diagnose y execute que solo consultan ya cuentan como consulta (la comprobación de «Configurar NTP» y «Ver el tráfico de una
  IP» no se podía ejecutar). «execute reboot», «execute factory-reset» y similares se marcan como corte del servicio.
- [arreglo] **FortiGate: configuración de referencia:** Ya no prepara comandos al estilo Cisco («no set …», bloques sin «end»)
  que el equipo rechazaba a mitad. La comparación se muestra igual; las diferencias se igualan a mano.
- [arreglo] **Junos: un commit fallido dejaba cambios pendientes:** Si el equipo rechazaba el commit de una solicitud, lo
  aplicado quedaba en la configuración candidata (que en Junos es la misma para todas las sesiones) y el siguiente cambio,
  de cualquiera, lo arrastraba y fallaba igual. Ahora se descarta («rollback 0») y la solicitud lo dice.
- [arreglo] **Junos: volver a una versión con DHCP en un puerto:** «family inet dhcp vendor-id …» (de fábrica en los puertos
  del QFX) se borra entero; antes se quitaba solo «vendor-id» y restaurar una copia con IP fija fallaba en el commit
  («Incompatible with interface assigned with address»). Visto en un vQFX 19.4R1.10.
- [mejora] **Recetas verificadas en FortiGate:** Las 12 recetas de FortiOS quedan con el sello «Verificada · FortiOS 7.4.2»
  (3 rondas seguidas sin fallos en un FortiGate-VM 7.4.2: todos sus pasos y su comprobación).
- [mejora] **Recetas verificadas en switches Juniper:** Las 10 recetas de Junos pasaron 3 rondas seguidas sin fallos en un
  vQFX 19.4R1.10 (las 9 que ya estaban verificadas en Junos 14.1 suman esa versión; «Crear una VLAN y poner un puerto de
  acceso» queda verificada por primera vez).
- [arreglo] **Consola: el aviso «cambio detectado» en su lugar:** Si un cambio se escribía justo después de un comando
  bloqueado (modo solo lectura), el aviso verde podía salir antes del comando, en el prompt anterior. Ahora espera a que el
  equipo repita el comando.
- [nuevo] **Guía del desarrollador:** docs/desarrollo (y en PDF, docs/OpenNCCM-Guia-del-desarrollador.pdf): arquitectura,
  mapa del código, datos y migraciones, cómo agregar una plataforma, interfaz, API, pruebas, laboratorio, publicación y
  rendimiento, para continuar el proyecto.

## 2.36.0
- [nuevo] **Firmware de cada copia de seguridad:** Cada copia guarda la versión de software que tenía el equipo cuando se hizo,
  leída en el mismo respaldo. Se ve en la línea de tiempo (azul la de hoy, ámbar otra, «sin registrar» en las copias viejas),
  con un aviso «Firmware actualizado» entre dos copias de firmware distinto, en el inventario, en la versión abierta (con la
  línea de la que sale y un aviso antes de revertir a una versión de otro firmware) y al comparar, donde las diferencias que
  trajo la actualización salen en ámbar. El archivo descargado lleva la versión en el nombre.
- [nuevo] **Logos abreviados en tamaño chico (opcional):** Administración › Ajustes › Organización. Las 10 marcas cuyo logo es
  su nombre escrito (Cisco, Juniper, Nokia, NEC, Netgear, Hitachi, Siemens, Supermicro, Dell y Zyxel) usan su símbolo o sus
  iniciales donde el logo se ve muy pequeño. Apagado por omisión: el logo normal en todos los tamaños.
- [mejora] **Menú «Marca» de Dispositivos:** Con buscador y barra para bajar; ya no se alarga hasta el final de la página.
- [arreglo] **Menús desplegables:** Junto al borde derecho de la pantalla se cortaban («Todas las mar…»); ahora se abren hacia
  el lado en que entran.
- [arreglo] **Plurales:** «13 equipos en 1 grupo», «1 regla cumple», «1 línea distinta»… en lugar de «1 grupos».

## 2.35.0
- [nuevo] **Equipos retirados:** Un equipo dañado, reemplazado o dado de baja se retira en lugar de borrarlo (ficha › ⋯ ›
  Retirar o reemplazar). Se renombra «NOMBRE (n)», queda sin IP, aislado y fuera de todo lo activo, con todo su historial para
  auditarlo, y se busca por su nombre e IP originales. En el mismo paso se puede dar de alta el reemplazo con sus datos y
  comparar sus respaldos. Se ven en Dispositivos › Retirados y se pueden reincorporar.
- [mejora] **Equipo desactivado = aislado:** OpenNCCM ya no se conecta con un equipo desactivado por ninguna vía (respaldos,
  consola, SFTP, Multi-equipo, recetas, solicitudes, firmware, imagen .fxi, API). La ficha lo dice y ofrece «Activar».
- [nuevo] **Activar y desactivar en lote:** En Dispositivos, al marcar varios equipos (solo administradores).
- [arreglo] **Credenciales:** «Guardar» al editar una credencial no hacía nada; tampoco al editar un grupo, una política o
  una ficha de ciclo de vida en el panel lateral. Había que borrarla y crearla de nuevo.
- [mejora] **ArubaOS (S2500 y controladoras):** Sin contraseña de enable prueba la de ingreso y, si no sirve, lo explica en
  lugar de «Pattern not detected». Lee el modelo, la serie y todos los puertos (en el S2500-48P, 48 de cobre y 4 SFP+) y
  muestra su parte trasera.
- [nuevo] **ArubaOS: configlets y cumplimiento:** 10 configlets y scripts y el paquete «Aruba ArubaOS · Seguridad básica».

## 2.34.0
- [mejora] **pfSense y OPNsense por separado:** Cada uno es su propia plataforma en la consola, en Recetas y en Configlets,
  con sus recetas y sus rutas (config.xml, versión y registro). Los favoritos de consola que ya tenía se copian a los dos.
- [arreglo] **Consola de pfSense y OPNsense:** `ifconfig`, `sockstat`, `tail`, `ls`, `arp -an` y otros comandos de consulta se
  bloqueaban como si cambiaran la configuración (en Multi-equipo y en solo lectura). Ahora son consultas; reiniciar, apagar
  pf o borrar archivos se avisan como cortes de servicio.
- [arreglo] **Recetas de WatchGuard Fireware:** Probadas en un Firebox T40 real. El nombre del equipo usaba un submodo que no
  existe, «show log» y «show connection» daban error, «show system usage» no existe y el ping no terminaba nunca. La receta de
  NTP avisa del máximo de 3 servidores.
- [mejora] **Recetas verificadas:** 23 recetas más con el sello «Verificada» tras 3 rondas seguidas sin fallos: OPNsense 26.7
  (11) y WatchGuard Fireware 12.8.1 en un equipo físico (12).
- [nuevo] **50 configlets y scripts nuevos:** Diez para cada una de estas plataformas: MikroTik RouterOS, Extreme EXOS, Extreme
  VOSS / Fabric Engine, Ubiquiti EdgeSwitch y WatchGuard Fireware. Llegan también a las instalaciones que ya existen, una sola
  vez: si borra uno, no vuelve. Salen «Sin verificar» hasta que se prueben.

## 2.33.0
- [mejora] **«Ejecutar» espera la casilla del aviso:** En Configlets, Recetas y el asistente, los botones que ejecutan o aplican
  comandos quedan desactivados hasta marcar «Leí el aviso…». Con marcarla una vez basta para toda la página.
- [arreglo] **Configlets:** El panel de la derecha muestra los comandos completos; antes quedaban en una franja de una línea con
  su propia barra y no se veía qué se iba a ejecutar.
- [arreglo] **WatchGuard: imagen .fxi:** Crear la imagen y «Probar» fallaban siempre («SimpleNamespace is not mapped»). Ahora
  la imagen se crea, se recibe cifrada, se guarda con su clave y se borra del Firebox.
- [arreglo] **WatchGuard: sesiones de administración:** Los respaldos, las solicitudes y la consola web cierran su sesión de
  Fireware. Antes se acumulaban en el Firebox y una solicitud que fallaba dejaba el modo de edición tomado: el Firebox
  rechazaba la imagen .fxi y los cambios de otros administradores.
- [arreglo] **Ubiquiti EdgeSwitch (FastPath):** Las solicitudes crean VLAN («vlan database»); el nombre, el SSH y la web van en
  el modo privilegiado; «Volver a esta versión» quita el syslog, la comunidad SNMP y devuelve los puertos a la VLAN 1; el
  syslog del switch avisa de cada cambio; y las recetas de nombre, SSH/HTTPS, SNMP, SNTP y syslog funcionan en 1.9.3.
- [arreglo] **Cumplimiento de Ubiquiti EdgeSwitch:** «SSH habilitado», «Sin HTTP sin cifrar» y «SNTP» daban el resultado al
  revés (el switch no muestra lo que viene de fábrica). Se corrigen solas en las instalaciones que ya las tienen.
- [arreglo] **Solicitudes de cambio:** Un rechazo del equipo que llegaba tarde ya no se pierde (la solicitud terminaba «bien»
  sin aplicarse); un fallo pasajero del saludo SSH se reintenta.
- [arreglo] **Extreme VOSS, Juniper, Cisco y OPNsense:** VOSS restaura el RADIUS y el banner que sobran; Junos lee la serie de
  «show chassis hardware», ignora el aviso de «commit confirmed» en LLDP y nunca toca fxp0 al restaurar; Cisco no copia el
  certificado, la licencia ni el client-id de otro equipo y lee los puertos de los routers IOS-XE; OPNsense 26.7 conecta bien;
  y un equipo que se ve a sí mismo por un lazo ya no cuenta como vecino.
- [mejora] **Recetas verificadas en el laboratorio:** 26 recetas más llevan el sello «Verificada» tras 3 rondas seguidas sin
  fallos: Extreme VOSS 9.3, Juniper Junos 14.1 (vMX) y Ubiquiti EdgeSwitch 1.9.3 (equipo físico).

## 2.32.0
- [arreglo] **Errores falsos de SSH:** Equipos en línea quedaban con error («QueuePool limit of size 5 overflow 10 reached») y
  había que respaldarlos a mano: con muchos respaldos a la vez OpenNCCM se quedaba sin conexiones a su base. Ahora cada respaldo
  la suelta mientras habla con el equipo, y la página de Dispositivos ya no se queda en negro durante «Respaldar todos».
- [nuevo] **Respaldar todos con su progreso:** Un panel con el porcentaje, la barra, los correctos, con error, en curso y en
  cola, y cada fila de la lista que se actualiza sola con su paso. Se puede salir de la página, cancelar y, al terminar,
  reintentar solo los que fallaron.
- [mejora] **Los respaldos que fallan se comprueban solos:** Si la falla fue pasajera (sesión cortada con el puerto respondiendo,
  base ocupada) se reintenta; si el equipo no responde o rechaza la contraseña, se avisa con lo que se comprobó.
- [nuevo] **Informe «Equipos del mapa»:** Los equipos del mapa de la red cruzados con Dispositivos: cuáles ya están agregados y
  respaldados y cuáles no, sin IP ni MAC, con dónde escribir la IP de los que faltan (en la página o en un PDF para llenar).
- [mejora] **El aviso completo en todos lados:** Recetas, Configlets, Cumplimiento, Multi-equipo, la ficha y el asistente
  muestran el aviso entero con el Foro, GitHub y la casilla, y cada aceptación queda en Auditoría.
- [arreglo] **Configlets:** Las plataformas sin equipos ya no se ven apagadas: se ven igual que en Recetas.

## 2.31.0
- [arreglo] **Respaldar todos sin «Internal Server Error»:** Con varios respaldos a la vez, uno podía terminar en error
  («database is locked») y su versión no se guardaba. Ahora cada respaldo espera su turno para escribir.
- [nuevo] **Buscar dentro de una versión:** Al ver una copia, «Buscar en esta versión» resalta las líneas que tienen el texto o
  la IP y salta entre ellas con Intro.
- [arreglo] **Puertos de los MikroTik con muchos SFP:** El frente del CRS326-24S+2Q+ (y de los CRS con muchos SFP) muestra los
  SFP+ en dos filas y cada QSFP+ dividido en sus 4 interfaces («1-1» a «2-4») en su propio grupo.
- [arreglo] **Número de serie de los MikroTik:** Se lee del RouterBOARD («/system routerboard print») o de la cabecera del
  /export; antes salía vacío.
- [arreglo] **Comprobar ahora, aunque otro equipo se esté comprobando:** La comprobación de firmware de un equipo ya no se
  rechaza con «Ya hay una comprobación en curso».
- [mejora] **Reintento al entrar por SSH:** Un fallo pasajero de un equipo ocupado («No existing session») se reintenta; una
  contraseña mala no.
- [arreglo] **MikroTik RouterOS:** «Volver a esta versión» restaura los elementos que el /export de RouterOS 7 parte en dos
  líneas; restaurar en otro equipo e igualar a la referencia ya no fallan por el «keymat-provider» que RouterOS 7 crea solo; en
  RouterOS 6, un comando que muestra su avance («/certificate sign») ya no hace perder el siguiente.
- [arreglo] **Recetas de RouterOS:** «Aplicar como solicitud» ya no quita pasos que cambian algo, «Limitar SSH y Winbox» sirve
  en RouterOS 6 y 7, y hay una receta de NTP para RouterOS 6.
- [arreglo] **Extreme EXOS:** El syslog detecta los cambios hechos por fuera; restaurar una copia funciona (las líneas de punto
  de partida de EXOS y las cuentas que ya existen); igualar a la referencia ya no copia el nombre ni la contraseña del switch;
  buscar una MAC encuentra los puertos de EXOS; y las recetas de contraseña y SNMP usan la sintaxis de EXOS 33.
- [mejora] **Recetas y correcciones verificadas en el laboratorio:** 25 recetas (MikroTik RouterOS 6.49.23 y 7.24.5, Extreme
  EXOS 33.6.1.14) y 8 correcciones de cumplimiento de MikroTik llevan el sello «Verificada», tras 3 rondas seguidas sin fallos.

## 2.30.0
- [nuevo] **Recetas para todos los equipos:** 758 recetas, con al menos 10 para cada tipo de equipo con línea de comandos (131 de
  133), entre ellos Sophos, Cisco SD-WAN, Brocade FC, Ciena, Calix, Ericsson, Nokia ISAM, D-Link, Hirschmann, Yamaha, Eltex,
  Teldat, AudioCodes, ScreenOS, Hillstone, WatchGuard y Prisma SD-WAN.
- [mejora] **Recetas por equipo y por tarea:** La pestaña Recetas tiene tres columnas: tus plataformas y todas las marcas, sus
  recetas agrupadas por categoría y el detalle. «Por tarea» muestra en qué plataformas hay receta para lo que quieres hacer, y la
  búsqueda agrupa por plataforma y dice dónde está la palabra.
- [mejora] **Configlets y scripts en columnas:** Primero lo que puedes ejecutar en tus equipos, con la plataforma en palabras,
  el sello «Verificada», el aviso con el foro y GitHub y la casilla obligatoria antes de crear la solicitud.
- [arreglo] **Vista previa de «Ejecutar» un configlet:** Mostraba trozos de código en lugar de los comandos; ahora muestra los
  valores escritos y no deja enviar con una variable vacía.
- [mejora] **Aviso al aplicar comandos:** Las correcciones de Cumplimiento, Multi-equipo y el asistente IA llevan el aviso
  «Revisa los comandos antes de usarlos en producción», y las correcciones y Multi-equipo piden marcar la casilla antes de crear
  la solicitud. «Corregir N» deja elegir qué correcciones aplicar.
- [mejora] **Comprobar ahora lee la versión del equipo:** En la pestaña Firmware, «Comprobar ahora» le pregunta al equipo su
  versión instalada antes de buscar la nueva, sin tener que respaldarlo.
- [nuevo] **Comprobación después de actualizar el firmware:** Cuando el equipo vuelve, OpenNCCM lee su versión y avisa si quedó
  en la nueva; si no, guarda en Eventos y en la solicitud el final del registro del equipo con el motivo.
- [arreglo] **Serie de los Ubiquiti:** Los airMAX, airFiber y AP UniFi muestran su número de serie (la MAC, como hace Ubiquiti), y
  se reconoce la serie escrita como «S/N».

## 2.29.0
- [nuevo] **Equipos ZTE:** OLT ZXA10 C300, C320, C350 y C220, OLT TITAN C600, C610, C620, C650 y C680, y switches y routers
  ZXR10 (ZXROS), con respaldo, versión, modelo y serie.
- [nuevo] **Recetas para casi todos los equipos:** 422 recetas, con al menos 10 para 95 de los 133 tipos de equipo: Ubiquiti,
  ZTE, Huawei, H3C/HPE Comware, Juniper, Arista, Cisco NX-OS, IOS-XR y ASA, FortiGate, PAN-OS, Aruba, Dell, Nokia, F5, Citrix,
  pfSense, OPNsense, Linux y los equipos con CLI tipo Cisco, entre otros.
- [nuevo] **Sello «Verificada» en las recetas:** Las recetas probadas completas llevan el sello con la versión del sistema en
  la que se probaron, por ejemplo «Verificada · VOSS 9.3.0.0»; las demás dicen «Sin verificar». Hay un filtro «Solo
  verificadas» y un administrador puede poner o quitar el sello.
- [mejora] **Aviso en las recetas:** Arriba de la lista, un aviso pide revisar cada receta antes de usarla en producción, porque
  OpenNCCM no tiene todas las plataformas para probarlas. Al aplicar una receta hay que confirmar que se revisaron los comandos, se
  avisa si el equipo tiene otra versión que la del sello, y cada receta se puede reportar en el foro o en GitHub.
- [mejora] **Firmware y CVE de Ubiquiti:** airMAX, airFiber, los AP UniFi, el USG y el EdgeSwitch X muestran el firmware
  disponible y sus vulnerabilidades. La búsqueda de CVE usa además el modelo del equipo y cubre 70 tipos de equipo.
- [mejora] **Copia de airOS como system.cfg:** En airMAX, airFiber y los AP UniFi, la copia de seguridad se descarga como
  system.cfg, lista para restaurar en el equipo.
- [mejora] **Grupos y Nodos con panel lateral:** «Crear grupo» y «Crear nodo» están siempre arriba y abren un panel a la
  derecha, como en Credenciales; cada fila tiene Editar y Borrar.
- [mejora] **Cambios de vecinos del mapa:** El panel ya no se abre cada vez que se entra al mapa; solo cuando hay cambios
  nuevos, y se puede abrir con el botón «Cambios de vecinos».

## 2.28.0
- [nuevo] **Logos de marca:** Cada equipo muestra el logo de su marca (73 marcas, los 131 tipos de equipo) en Dispositivos, la
  ficha, el mapa, la consola, la búsqueda Ctrl K y las demás pantallas. Dispositivos tiene un filtro «Marca» y una fila de chips
  con cuántos equipos hay de cada una.
- [mejora] **Distribuciones del mapa más legibles:** Cada distribución usa todo el mapa y los nombres nunca quedan por debajo de
  11 px. Ninguna línea cruza los nombres. Por sitio pone el equipo central al medio, Por función parte las columnas largas, el
  árbol plegable encuadra la rama que se abre y la bandeja «Sin ubicación» baja con la rueda. Debajo del mapa, una franja explica
  la distribución elegida y la leyenda se pliega.
- [mejora] **Nodos más fáciles de usar:** Filtro «Nodo» y «Asignar al nodo» en Dispositivos, enlace a los equipos de cada nodo,
  Editar y Borrar a la vista, y un aviso propio para el nodo sin equipos. En el mapa, los vecinos de otros nodos salen
  atenuados con el nombre de su nodo.
- [mejora] **Tipo de equipo en el mapa:** Las antenas MikroTik (RB911, SXT, LHG…) y los MikroTik con Wi-Fi activo salen como
  inalámbricos, y el administrador puede corregir el tipo de un equipo a mano.
- [arreglo] **Filtro de nodos del mapa:** Con un nodo elegido, los equipos de otros nodos salían como vecinos «que no están en
  OpenNCCM».

## 2.27.0
- [nuevo] **Distribución del mapa:** Un menú sobre el mapa con 10 formas de ordenar los equipos: libre, escalonada hacia abajo o
  hacia la derecha, radial, por sitio, por función, circular, árbol plegable, sobre un plano y «Mi distribución», que se guarda
  arrastrando equipos, solo para ti o para todo el equipo.
- [nuevo] **Nodos:** Agrupa los equipos por lugar (sucursal, torre, edificio). Se crean en Administración › Nodos o al crear o
  editar un equipo, como las credenciales, y el filtro «Nodo» del mapa muestra solo los equipos de ese nodo, o todos.
- [mejora] **IP de los MikroTik encontrados por RoMON:** El mapa toma su IP de los vecinos MNDP/LLDP o de las tablas ARP, así ya no
  salen solo con la MAC.
- [nuevo] **Receta VOSS para cambiar el usuario y la contraseña:** En Recetas, «Cambiar el usuario y la contraseña de la CLI» de
  Extreme VOSS / Fabric Engine, con admin por defecto, y guarda la configuración al terminar.
- [mejora] **Puertos y parte de atrás en Extreme VOSS:** La ficha de los VSP / Fabric Engine muestra qué puertos tienen enlace y
  las fuentes y los ventiladores.
- [mejora] **Extreme VOSS en el mapa y en syslog:** El mapa lee sus vecinos LLDP, y syslog detecta sus cambios de configuración y
  cada «save config».
- [arreglo] **Cambios que renombran el equipo:** Una solicitud que cambiaba el nombre del equipo (y su prompt) se quedaba esperando
  y no guardaba la configuración. Ahora se relee el prompt antes de salir y guardar.
- [arreglo] **Referencia y tabla MAC en VOSS:** La configuración de referencia respeta los bloques que terminan en «exit», y la
  búsqueda de MAC reconoce los puertos «Port-1/2».
- [arreglo] **Equipos del mapa marcados como perdidos:** Un equipo de OpenNCCM ya no se pinta entero como nuevo o perdido: el enlace
  perdido se dibuja aparte, en su puerto.

## 2.26.0
- [nuevo] **Buscar una MAC o una IP:** Escribe una MAC o una IP en el buscador de la barra superior y OpenNCCM dice en qué switch
  y en qué puerto está conectado, con su VLAN, la descripción del puerto, cuándo se vio y el camino. Las tablas MAC y ARP se
  leen en cada respaldo y queda un historial para «Visto por última vez»; «Leer las tablas ahora» las vuelve a leer.
- [nuevo] **Aviso de vecino nuevo o perdido:** Una alarma cuando aparece un equipo que antes no estaba o deja de verse uno que sí
  estaba (LLDP, CDP, MNDP y RoMON), con su ficha, «Es conocido» y «Preparar shutdown del puerto». El perdido avisa recién tras 2
  lecturas y la alarma se cierra sola si vuelve. El mapa resalta los cambios de las últimas 24 h. Se activa en Ajustes › Mapa de
  la red.
- [nuevo] **Configuración de referencia por modelo:** Elige el equipo bien configurado y compara contra él a los demás del
  mismo modelo, por sección (AAA, SNMP, NTP, VLAN…), sin nombre, IP de gestión, descripciones ni claves. Se pueden ignorar
  líneas y preparar una solicitud de cambio para igualarlos, que siempre pasa por aprobación.
- [nuevo] **Comandos favoritos en la consola:** Una barra sobre la terminal con tus comandos de verificación por tipo de equipo;
  un clic los escribe y envía. Los que cambian la configuración se escriben sin enviar. Se ordenan, editan y, los
  administradores, comparten con todo el equipo.
- [arreglo] **RoMON en redes reales:** El mapa no encontraba los MikroTik por RoMON en RouterOS 7 (la salida real trae la marca
  «A», el camino en varias líneas y la identidad solo con «proplist»). Ahora salen todos, también en RouterOS 6.
- [arreglo] **La «ñ» de los nombres MikroTik:** Los nombres como «Cabañas» ya no salen como «CabaF1as», ni en el mapa ni por
  RoMON.
- [mejora] **«Compartir» en Operar › Consola:** Es un ícono, igual que en la consola de la ficha del equipo.

## 2.25.0
- [nuevo] **MikroTik por RoMON en el mapa:** Con «Buscar también los MikroTik por RoMON» (Ajustes › Mapa de la red,
  desactivado de fábrica), el respaldo de cada MikroTik con RoMON activado encuentra los que están a varios saltos o sin
  dirección IP, y el mapa los une con una línea punteada. RoMON no dice por qué puerto se conectan, así que no cuentan para el
  análisis de impacto ni para Puertos sin uso.
- [arreglo] **«Reconocer todas» con cientos de alarmas:** Con muchas alarmas aparecía «OpenNCCM se está reiniciando» y después
  «Method Not Allowed». Ahora responde enseguida, «Deshacer» funciona con miles y una recarga vuelve a la lista. Las alarmas
  críticas de CVE se reconocen sin pedir un caso de la Base de conocimiento: se resuelven al actualizar el firmware.
- [mejora] **Mapa del documento de la red ordenado:** Cada grupo de equipos enlazados se arma por niveles, los grupos van uno
  al lado del otro y los equipos sin enlaces al final, en una grilla: ya no se apilan ni se encima nada.
- [mejora] **Documento de la red más prolijo:** Cada título va en la misma página que su tabla, todas las tablas tienen el
  mismo ancho, la columna «Sitio» solo sale si hay más de uno y Cumplimiento avisa si todavía no se auditó. El logo se sube con
  un botón propio, se ve en la portada de la vista previa y se puede quitar.
- [mejora] **Impacto y puertos más claros:** La frase dice «quedan aislados 3 equipos y otros 2 de otras ubicaciones pierden la
  conexión», «Aprobar» se ve claramente desactivado hasta marcar «Leí el impacto», Puertos sin uso nunca muestra más días de
  los que hay datos y los textos usan el singular cuando es uno.

## 2.24.0
- [nuevo] **¿Qué se cae si toco este equipo?:** Cuando una solicitud de cambio reinicia equipos o apaga puertos, OpenNCCM
  calcula con el mapa de la red (LLDP/CDP) qué queda sin servicio: en rojo lo que queda aislado, en ámbar los routers y
  sucursales que se cortan y en verde lo que sigue por otro camino, con una frase como «Si reinicias CORE-01 quedan aislados 3
  equipos». «Aprobar» espera a que se marque «Leí el impacto», el impacto queda en Auditoría y la solicitud se puede dejar
  para una hora (la ventana de mantenimiento). Dice también lo que no sabe: los equipos que no informan vecinos.
- [mejora] **Firmware de varios equipos en el orden que menos corta:** Con reinicio, primero se actualizan los que no aíslan
  a nadie y, de a uno, los que sí; nunca dos que juntos dejan más sin servicio. El mismo análisis aparece en la ficha, en las
  tareas que reinician y en el mapa («¿Qué se cae?»).
- [nuevo] **Puertos sin uso:** Informe nuevo con los puertos de acceso de cada switch sin enlace hace más de 30, 60 o 90
  días, cuántos quedan libres y qué switches están casi llenos. Excluye uplinks, troncales, 802.1X y excepciones, y prepara el
  apagado de los elegidos como solicitud de cambio, con la sintaxis de cada fabricante. El historial se empieza a anotar con
  esta versión: hasta tener los días pedidos, el informe dice desde cuándo hay datos.
- [nuevo] **Documento de la red:** Un PDF o un Word (.docx) por sitio, grupo o toda la red con inventario, mapa, VLAN e IP,
  enlaces, cumplimiento y vulnerabilidades y el último respaldo de cada equipo, con portada, logo del cliente y la opción de
  ocultar las IP públicas. Nunca lleva contraseñas ni claves. Avisa si la red cambió desde que se generó.
- [mejora] **Operar › Consola como la consola de la ficha:** Cada sesión tiene A−/A+, «Ampliar» en la ventana, abrir en una
  ventana aparte y pantalla completa; el botón «Asistente» lleva el logo del proveedor y la cabecera del asistente se oculta
  desde ella misma con «Ocultar». Además, el configlet que prepara el asistente ya aplica a sus tipos de equipo.

## 2.23.0
- [mejora] **Panel del asistente más amplio:** En la consola, las preguntas sugeridas ya no se salen del panel, se pliegan
  solas al preguntar y se vuelven a abrir con «✦ Preguntas sugeridas». El botón ⓘ oculta la parte de arriba (proveedor,
  contexto y aviso) para dejar más espacio a las respuestas, y la pregunta se escribe al fondo del panel.
- [nuevo] **Elegir el modelo de IA:** En Ajustes › Asistente IA el modelo se elige en un desplegable con los de la cuenta
  del proveedor. Cada usuario puede cambiarlo a su gusto desde el chat de la barra superior o desde el panel de la consola,
  sin cambiar el de los demás.
- [mejora] **El asistente solo habla de la red:** Responde sobre redes y seguridad, los equipos que OpenNCCM admite y
  administra y el uso de OpenNCCM. A una pregunta ajena contesta en una frase que solo ayuda con la red.
- [arreglo] **Asistente en Operar › Consola:** Cada sesión abierta tiene su botón «✦ Asistente», como la consola de la
  ficha del equipo.
- [nuevo] **El asistente ayuda en todo el sistema:** El chat consulta las vulnerabilidades, la Base de conocimiento, los
  configlets, las tareas programadas y los tipos de equipo admitidos. Prepara configlets y scripts, borradores de casos de
  la Base de conocimiento, tareas, correcciones de vulnerabilidades como solicitud de cambio y migraciones desde otro
  sistema (el CSV para importar equipos). Todo se hace solo cuando el usuario confirma.

## 2.22.0
- [mejora] **Feature key de WatchGuard lista para restaurar:** OpenNCCM guarda la feature key tal como se pega en el Firebox
  para restaurarla (Serial Number, License ID, Name, Model, Version, una línea Feature por función, Expiration y Signature). La
  firma no sale por la CLI, así que entra un momento a la Web UI del Firebox con la misma credencial, la lee y cierra la sesión.
  Si la Web UI no responde lo reintenta pasada una hora, y si rechaza el ingreso, al día siguiente, para no insistir con la
  contraseña; el aviso sale una sola vez por motivo. Lee los campos aunque otra versión de Fireware cambie un poco la página.
- [nuevo] **Ver las credenciales de los equipos:** En Ajustes › Seguridad y acceso se elige quién puede verlas: nadie (de
  fábrica), solo los administradores o también los operadores. Hace falta tener activa la verificación en dos pasos, y cada vez
  que se pulsa «Ver» se confirma con el usuario y la contraseña propios (las cuentas de Microsoft, con el código de su app). Se
  muestran el usuario, la contraseña y el enable con botón Copiar y se ocultan a los 60 segundos. Cada vista y cada intento fallido
  quedan en la Auditoría, y 5 intentos fallidos bloquean la cuenta 15 minutos.
- [nuevo] **Aviso de licencias por vencer:** La ficha de un WatchGuard muestra cuándo vence cada servicio de su feature key
  (antivirus, IPS, LiveSecurity…), en ámbar si faltan 60 días o menos y en rojo si ya venció. OpenNCCM avisa a los 60, 30 y 7 días
  y al vencer, una sola vez por fecha. Cuando algo está por vencer relee la feature key una vez al día para ver la renovación, sin
  entrar a la Web UI si sigue siendo la misma. El informe nuevo «Licencias por vencer» lista lo de todos los equipos.
- [nuevo] **Aviso de equipo reemplazado:** Si el número de serie que lee el respaldo cambia, es otro aparato con la misma
  dirección (un RMA o un cambio de equipo). OpenNCCM avisa con las dos series, lo deja en la Auditoría, etiqueta la versión nueva
  «equipo nuevo» y no le aplica sola la reversión automática con la configuración del aparato anterior.
- [mejora] **Comandos de la consola según el equipo:** Las sugerencias y los comandos frecuentes de la consola (en la ficha y en
  Operar › Consola) ya no son genéricos: cada tipo de equipo que admite OpenNCCM tiene los suyos, ajustados a su modelo y su
  versión de firmware. Por ejemplo, WatchGuard sugiere los de Fireware y, desde la 12.4, SD-WAN; RouterOS 7 y 6 sugieren lo de
  cada versión, y PoE, Wi-Fi o stack aparecen solo en los modelos que los tienen. El historial se guarda por tipo de equipo, y
  las consultas de cada equipo se pueden usar en modo solo lectura aunque no empiecen por «show».
- [mejora] **El asistente IA recomienda comandos de ese equipo:** El asistente recibe la plataforma, el modelo, la versión de
  firmware y la lista de comandos del equipo con que se trabaja, y responde solo con comandos de esa plataforma y esa versión.
  Sus preguntas sugeridas también cambian según el equipo.

## 2.21.0
- [arreglo] **Clave de la imagen .fxi:** Fireware solo acepta claves de 8 a 15 caracteres, y «Generar» creaba 24, así que la
  imagen fallaba con «the length of password should be between 8 and 15». Ahora «Generar» crea 15 y el formulario y el servidor
  validan de 8 a 15. Si hay guardada una clave más larga, la imagen falla antes de conectar y explica cómo cambiarla. Probado en
  un Firebox T40 con Fireware 12.8.1.
- [mejora] **Feature key de WatchGuard con su detalle:** `show feature-key` solo trae el resumen (sincronización, avisos y la
  lista de claves). Ahora OpenNCCM guarda `show feature-key ID` de cada clave: modelo, vencimiento y cada función con su fecha.
  La guardada antes, solo con el resumen, se vuelve a leer una vez en el siguiente respaldo. La firma no sale por la CLI de
  Fireware; viene dentro de la imagen .fxi.
- [arreglo] **Luz Mode del frente del T40:** Se ve verde cuando alguna interfaz externa tiene enlace y parpadea si ninguna lo
  tiene, como en el Firebox. Status queda apagada, porque el equipo solo la enciende 30 segundos cuando alguien entra por la Web
  UI o la CLI. Cada luz explica su estado al pasar el ratón.
- [mejora] **Qué trae la imagen .fxi:** La pestaña Respaldos y el manual aclaran que la imagen no incluye el sistema operativo
  (desde Fireware 12.2.1 no lo trae, y por eso pesa alrededor de 1 MB). Para restaurarla, el Firebox debe tener una versión de
  Fireware compatible.
- [arreglo] **Archivo recibido de la imagen .fxi:** El paso «Recibir el archivo» mostraba el nombre con coma («…,fxi»); ahora
  sale con punto y solo el tamaño lleva coma.

## 2.20.0
- [nuevo] **Imagen completa de WatchGuard (.fxi):** OpenNCCM puede guardar la imagen completa y cifrada de un Firebox, para
  recuperar ese mismo equipo tras un fallo. Además de la configuración trae los certificados con sus claves, la feature key, las
  contraseñas y la información propia del equipo. Se configura en la nueva pestaña **Respaldos** de la ficha con una credencial de
  administrador del Firebox y una clave de cifrado; se crea semanal, diaria o a mano, y se guardan las N últimas. «Probar
  conexión» comprueba que el Firebox llega a OpenNCCM sin crear ninguna imagen.
- [nuevo] **Receptor FTP temporal:** Fireware solo entrega la imagen por FTP, así que OpenNCCM abre un receptor propio solo
  mientras dura el respaldo. Acepta únicamente la IP del Firebox, con usuario y contraseña de un solo uso, y se cierra al recibir
  el archivo o a los 5 minutos (puerto 2121 y pasivos 50000–50010, configurables). Si el servidor usa ufw, abre esos puertos solo
  para el Firebox y los cierra al terminar; la regla se quita sola a los 6 minutos aunque el servicio se detenga. En el Firebox no
  queda ninguna copia.
- [nuevo] **Respaldos de WatchGuard en la ficha:** La pestaña Respaldos explica qué guarda la configuración (XML) y qué la imagen
  (.fxi), y cuándo usar cada una. Cada archivo se descarga en su formato (`EQUIPO_v48.xml`, `EQUIPO_img-12.fxi`), nunca en
  .zip; «Descargar .xml y .fxi» baja los dos. La clave de la imagen nunca va dentro de un archivo: los administradores la ven en
  «Cómo restaurar» y queda en Auditoría quién la vio.
- [mejora] **Puertos de WatchGuard con su enlace:** La ficha de un WatchGuard muestra qué puertos tienen enlace y a qué velocidad,
  delante y detrás. OpenNCCM lo lee en cada respaldo con `show status-report` (solo lectura; basta un usuario Device Monitor) y
  guarda solo la sección de los puertos. Atrás, el puerto con enlace sale en verde; delante se enciende la luz de su velocidad,
  amarilla para 1000 y verde para 10/100, como en el Firebox T40.
- [mejora] **Si la imagen falla:** La pantalla muestra el mensaje exacto del Firebox y qué revisar (cortafuegos, dirección,
  política del Firebox o credencial sin permiso). Al segundo fallo seguido se abre la alarma «Imagen .fxi sin crear»; el respaldo
  XML no se ve afectado.

## 2.19.0
- [mejora] **WatchGuard Fireware:** Los firewalls WatchGuard se respaldan completos y su ficha muestra los puertos. El respaldo
  usa `export config to console` (Fireware no tiene `show running-config`; probado en un Firebox T40 con 12.8.1 y un usuario de
  solo lectura Device Monitor). Los puertos salen de la configuración: nombre de la interfaz, si está habilitada y su IP o las
  VLAN que pasan por él.
- [nuevo] **Feature key de WatchGuard:** Al agregar un WatchGuard se guarda su feature key una sola vez, cifrada, y se descarga
  desde la ficha del equipo. Si el equipo no la da en el primer respaldo, se intenta en el siguiente; la descarga queda en
  Auditoría.
- [nuevo] **Frente y parte de atrás del Firebox T40:** La ficha dibuja el T40 como es, con los puertos detrás y las luces
  delante. Atrás: consola serie, dos USB, 0/WAN, 1/LAN, 2, 3 y 4-PoE, Reset, interruptor y entrada de 54 V DC; frente: Failover,
  las luces de cada puerto, Status, Mode, Attn y encendido. La ficha se abre por detrás.
- [nuevo] **Cumplimiento para WatchGuard:** Un paquete nuevo de 10 reglas revisa la seguridad básica de los Firebox. Web UI y
  administración no abiertas a Internet, bloqueo de cuentas, sesión inactiva ≤ 15 min, SNMP sin comunidades por defecto y v3, NTP,
  envío de logs, bloqueo de paquetes falsificados y aviso antes del inicio de sesión. Se carga solo al actualizar.
- [arreglo] **Respaldos con error:** Un mensaje de error del equipo ya no se guarda como si fuera una versión de la
  configuración. Antes solo se miraba la primera línea, y Fireware pone el error en la segunda («^» y «% Invalid input…»).
- [arreglo] **Ficha lenta con configuraciones enormes:** La ficha de un equipo con una configuración de decenas de miles de
  líneas vuelve a abrir en menos de un segundo. La comparación aparta lo igual antes de comparar (de 26 s a 0,1 s con 40.000
  líneas) y dibuja como mucho 2.000 filas, con la descarga del .diff completo.
- [arreglo] **Pestañas de página:** Al abrir la ficha de un equipo ya no se ven un momento el Historial y la Comparación dentro
  de Resumen.
- [mejora] **Firmware de un equipo nuevo:** La versión de firmware disponible aparece en cuanto se agrega un equipo, sin esperar
  a la comprobación diaria. La tarjeta «Actualizar firmware» la muestra también en los equipos que OpenNCCM no puede actualizar.
- [arreglo] **Análisis en inglés y en español:** El origen de los cambios hechos desde la consola sale como «Consola» («Console»)
  y no como «console».
- [mejora] **Novedades con su tipo:** Cada punto de las novedades lleva su tipo (Nuevo, Mejora, Arreglo o Seguridad) como una
  etiqueta de color. La ventana tiene la cabecera y el pie fijos, con «Cerrar» siempre a la vista, y se cierra también con un clic
  fuera.
- [arreglo] **Teléfono:** «Soporte profesional» está también al final del menú lateral y el saludo del Panel ya no se monta
  sobre los botones. La insignia de la base de conocimiento es ámbar en todas las páginas.
- [arreglo] **Inglés:** El grupo «Equipo» del menú (las personas) se traduce «Team» y no «Device».

## 2.18.0
- [nuevo] **Botón «Soporte profesional»:** Un botón nuevo en la barra superior lleva a la página de soporte de pago. Está a la
  izquierda de «Apoya OpenNCCM», se abre en otra pestaña y tiene una animación suave que se detiene si el sistema pide reducir
  el movimiento. No cambia ninguna función; el administrador lo oculta para todos en **Ajustes › Organización**.
- [nuevo] **Base de conocimiento:** Cada problema resuelto queda escrito como un caso, para que el equipo lo encuentre y lo
  resuelva igual la próxima vez. Está en Equipo › Base de conocimiento; cada caso tiene síntoma, causa, pasos con sus comandos
  («Copiar» sin la clave oculta y «Abrir en consola», que lo deja escrito sin ejecutarlo), comprobación y prevención.
  «Falta documentar» se llena sola con las alarmas críticas cerradas y los cambios revertidos (y, si se activa, las sesiones de
  consola de más de 15 min); una alarma crítica ya no se cierra sin caso o enlace a uno existente. Los operadores envían a
  revisión y el administrador aprueba o pide cambios; «Proponer cambio» en casos ajenos, versiones, comentarios, «Me sirvió»,
  «Convertir en configlet», asistente IA para proponer causa y comprobación, casos parecidos y resumen por correo los lunes.
  Solo para administradores y operadores (solo lectura, si el administrador lo activa); las contraseñas, claves y comunidades
  SNMP se ocultan al guardar y cada lectura y edición queda en Auditoría.
- [arreglo] **Restaurar en un servidor nuevo:** Una copia del sistema restaurada en un servidor nuevo ya se lee completa,
  porque se usa la clave que viene en la copia. `sudo opennccm restore` con una copia que incluye la clave reemplaza la que creó
  el instalador (la anterior queda como `secret.key.antes-de-restaurar-FECHA`); antes se quedaba con la del instalador y las
  configuraciones y contraseñas no se podían leer. Si la copia no trae la clave y la del servidor no descifra los datos
  restaurados, el comando lo avisa y dice qué hacer en lugar de terminar con «Restaurado.».
- [mejora] **Restaurar con PostgreSQL:** Las copias del sistema también se pueden restaurar en servidores que usan PostgreSQL.
  `sudo opennccm restore` usa `pg_restore`; una copia de otra base de datos (SQLite en un servidor con PostgreSQL o al revés) se
  rechaza con el motivo.

## 2.17.0
- [arreglo] **Operar › Consola:** Elegir un equipo vuelve a abrir su consola y las recetas se ven todas otra vez. En la 2.16.0
  un fallo de la página dejaba las dos cosas sin funcionar; ahora en **Recetas**, con «Todas» y «Todos los fabricantes», se ven
  todas.
- [mejora] **Herramientas con parámetros:** Ping, traceroute, puerto TCP y DNS aceptan las opciones de siempre, de Windows o de
  Linux. Detrás de la IP o el nombre: `8.8.8.8 -t` (ping continuo hasta pulsar **Detener**), `-n 10`, `-l 1400 -f`, `-i 0.2`,
  `--ttl 5`; traceroute `-h 30 -q 3`, `-I` o `-T -p 443`; puerto TCP `host:443 -c 5`; DNS `ejemplo.com MX @1.1.1.1` o
  `-x 8.8.8.8`. Debajo de la casilla se ven las de cada herramienta. La salida llega a medida que se produce. Solo se aceptan
  esas opciones (nada se pasa tal cual al sistema). El instalador y `sudo opennccm update` instalan `dnsutils` (dig) para los
  tipos de registro DNS.
- [mejora] **Chat del asistente:** El asistente solo nombra equipos que existen y dice cuántos equipos toca una acción antes de
  hacerla. La confirmación lista los equipos con su número («Respaldar 3 equipos»); la barra superior se acomoda con el chat
  abierto y el chat vacío saluda con sugerencias.
- [mejora] **Errores en pocas palabras:** Los fallos de conexión y de respaldo se explican con un motivo corto y traducido. En
  el Panel («Necesita tu atención») y en el chat salen como «sin respuesta por SSH», «credencial rechazada», «cambió la clave SSH
  del equipo», «falta la contraseña enable»…; el mensaje completo sigue en la ficha del equipo.

## 2.16.0
- [nuevo] **Chat del asistente en todo el sistema:** Un chat con el asistente IA se abre desde cualquier página para preguntar
  por la red y pedir acciones, que siempre se confirman antes. El botón «Asistente» de la barra superior (con el logo del
  proveedor) abre el chat a la derecha. Consulta OpenNCCM con funciones de solo lectura (equipos, respaldos, alarmas, cambios y
  versiones, cumplimiento, ciclo de vida, búsqueda en configuraciones) y el manual, con el rol y los grupos del usuario y los
  secretos ocultos. Respuestas cortas, equipos en una línea, como mucho 2 botones y «Más acciones», y la fuente. Las acciones que
  cambian algo (respaldar, crear una tarea, crear una solicitud de cambio) piden confirmación en el chat. La conversación se
  guarda por usuario; cada pregunta queda en Auditoría con las funciones usadas.
- [mejora] **Recetas:** La consola trae al menos 10 recetas paso a paso por fabricante para Cisco IOS, MikroTik RouterOS,
  Extreme EXOS y Extreme VOSS / Fabric Engine. Cubren nombre, usuarios y contraseñas, SSH, HTTPS, SNMP, syslog hacia OpenNCCM,
  RADIUS, NTP, VLAN, troncales, rutas, guardar… La consola reconoce VOSS (autocompletado, frecuentes, paginado y comandos que
  cortan el servicio).
- [arreglo] **Herramientas de la consola:** Ping y traceroute ya funcionan desde el servidor. El servicio tiene permiso para
  ICMP y se instalan `iputils-ping` y `traceroute` al instalar o actualizar. Si traceroute tarda, se ve lo que alcanzó a
  responder.
- [mejora] **Asistente IA:** Cada proveedor de IA se reconoce por su logo, en un cuadrito con el color de su marca. Se ve en
  Ajustes, en la consola y en la barra superior, y las tarjetas de proveedor tienen la misma altura; si al guardar no hay ningún
  rol marcado, quedan administradores y operadores.
- [arreglo] **Consola:** El aviso verde de cambio sale en el momento correcto y el panel de la consola cabe en la ventana. El
  aviso sale al salir del modo configuración (tras el «end»), nunca antes del comando; el panel de Operar › Consola tiene una
  sola fila de botones; textos en singular («1 sesión abierta»).
- [mejora] **Grabaciones:** El reproductor muestra la sesión grabada con los colores de la consola y la lista de comandos con su
  hora. Las sesiones vacías se ocultan («Mostrar sesiones vacías»); al arrancar, las sesiones que quedaron «en curso» se cierran.
- [mejora] **Filtros de recetas:** Los filtros de las recetas quedan separados del buscador.
- [arreglo] **Puertos de los Ubiquiti EdgeSwitch X:** Los EdgeSwitch X (ES-10X, ES-18X…) ya muestran qué puertos están
  activos y cuáles son de fibra. Estos equipos no tienen «show interfaces status»: se lee «show interfaces GigabitEthernet 1-N»
  (verificado en un ES-18X real).

## 2.15.0
- [nuevo] **Consola nueva:** La consola web es ahora una terminal clásica con colores, autocompletado y protección ante
  comandos peligrosos. Colores por significado según el sistema (prompt, rutas y comandos, verbos, clave=valor, IP, estados
  activos, caídos y deshabilitados), tres temas (Sala de control, Grafito y Alto contraste) y una línea de comando fija con
  autocompletado (texto fantasma, sugerencias del sistema del equipo, Tab, historial con ↑) y comandos frecuentes. Antes de un
  comando que corta el servicio (reload, reboot, borrar la configuración) pide confirmación; el modo **solo lectura** bloquea
  los cambios; buscar en la salida, tamaño de letra, copiar, descargar el registro y pantalla completa. Si un comando cambia la
  configuración, lo avisa en verde y pone un respaldo en cola (origen «Consola»).
- [nuevo] **Operar › Consola:** Una página nueva reúne las sesiones de consola, varias consolas a la vez, consultas a muchos
  equipos, recetas, grabaciones y herramientas. Sesiones con lanzador (favoritos, recientes, grupos, Ctrl K), varias consolas a
  la vez (1, 2 o 4), compartir en solo lectura y archivos del equipo por SFTP; **multi-equipo** (la misma consulta en N equipos,
  con las respuestas agrupadas en iguales y distintas; los cambios van como solicitud de cambio); **recetas** paso a paso con
  variables, revisadas contra la documentación oficial de Cisco, MikroTik y Extreme Networks, y las propias de cada usuario;
  **grabaciones** de cada sesión con búsqueda y reproductor (1×, 2×, 4×, marcas de comandos peligrosos, exportar; 180 días);
  **herramientas** (ping, traceroute, puerto TCP y DNS desde el servidor) y **acceso**: consola por rol (sin consola, solo
  lectura, lectura y escritura), comandos prohibidos y aprobación previa por grupo, con solicitudes de acceso con motivo y plazo.
- [nuevo] **Asistente IA:** Un asistente de IA opcional ayuda en la consola, en la comparación de versiones y en Cumplimiento,
  y nunca ejecuta nada. Está desactivado por omisión; cada organización conecta su proveedor: Anthropic (Claude), OpenAI, Google
  (Gemini), Mistral o cualquier servicio compatible con OpenAI. Antes de enviar se ocultan siempre contraseñas, comunidades SNMP
  y claves. En la consola (panel con preguntas sugeridas y comandos con su riesgo, Copiar, Insertar y «Enviar como solicitud de
  cambio»), en la barra superior, en la comparación de versiones («✦ Explicar este cambio») y en Cumplimiento («✦ ¿Cómo lo
  corrijo?»). Roles que pueden usarlo, límite diario, uso del mes y cada pregunta en Auditoría.

## 2.14.0
- [nuevo] **API para apps móviles:** Una API nueva (/api/mobile) permite usar OpenNCCM desde un teléfono con los mismos
  permisos que en la web. Sesión con usuario y contraseña (y código de dos pasos si la cuenta lo tiene), panel, equipos, ficha
  con puertos de frente y de atrás, respaldo, versiones, alarmas, cambios con aprobación y avisos, con los mismos roles y grupos
  que la web; las acciones quedan en Auditoría con «[Android]». Los teléfonos conectados aparecen en Mi cuenta › Tokens de API
  y se pueden revocar.
- [mejora] **EdgeSwitch 18X:** La ficha del EdgeSwitch 18X muestra bien sus puertos y su parte de atrás. Frente con sus 16
  RJ45 y los 2 SFP (gi17 y gi18), sin comillas en las descripciones y, atrás, su entrada de 24 V y «sin ventiladores». La ficha
  guarda lo que respondió el equipo al leer los puertos (pestaña Puertos, solo administradores) para corregir las plataformas que
  no den el estado de enlace.
- [mejora] **Aprobación de cambios:** Aprobar o rechazar un cambio usa la misma comprobación en la web y en la API.

## 2.13.1
- **Puertos de los EdgeSwitch con chip Realtek (por ejemplo ES-18X, puertos «gi1»…).** No salían: se probaba solo la orden
  de los EdgeSwitch clásicos. Ahora se lee también «show interfaces status» y, si el equipo no da la tabla, los puertos
  salen de la configuración sea cual sea su estilo. El firmware es el real (1.11.1) y no la versión del cargador, y el
  modelo se toma de la cabecera de la configuración.
- **Ficha con puertos:** los números y rótulos quedan a 12 px del contorno del puerto elegido; en el móvil, la tabla
  Interfaces va en dos renglones por puerto y la ruta muestra solo «‹ Dispositivos».

## 2.13.0
- **Puertos en todas las plataformas.** El estado de los puertos se lee con la orden de cada fabricante: Ubiquiti
  EdgeSwitch y UniFi (FastPath), Huawei, H3C/Comware, HPE ProCurve/Aruba OS-Switch, Aruba AOS-CX, Cisco IOS/IOS-XE,
  NX-OS, ASA/FTD y WLC, Arista, Dell, TP-Link y demás switches con CLI tipo Cisco, Juniper, Extreme EXOS, Nokia SR OS,
  EdgeRouter/USG/VyOS, FortiGate, Palo Alto, Check Point, Linux (también airOS, airFiber y UniFi AP) y pfSense/OPNsense.
  Si el equipo no responde a ninguna, los puertos salen de la configuración (apagado o habilitado).
- **Frente fiel al modelo.** Una fila en routers y switches pequeños (hEX, RB4011 con su separación, US-8, firewalls de
  escritorio) y dos filas en los grandes, en bloques como en el equipo (CRS326 de 4 en 4 con los SFP+ abajo; UniFi,
  EdgeSwitch y Cisco de 6 en 6 con los SFP de dos en dos). Los SFP sin tipo se reconocen por el modelo (ES-24: 25 y 26).
- **Parte de atrás: fuentes y ventiladores.** Botón «Atrás» en la franja de puertos: fuentes de alimentación y
  ventiladores en verde si funcionan y en rojo si fallan o están sin corriente; aviso en la franja si alguno falla.
  Se lee en el respaldo (MikroTik, Cisco, Juniper, Huawei, HPE, Aruba, Arista, FastPath, Extreme y FortiGate).
- **Ficha con puertos, ajustes de diseño:** resumen en la columna derecha, números fuera del contorno de selección,
  tabla Interfaces en una línea por puerto con 10 a la vista y barra para desplazarse, «Dirección», «Bridge» o «Red»
  según el puerto, frente desplazable en el móvil y cabecera del equipo ordenada en pantallas estrechas.

## 2.12.0
- **Puertos en la ficha del equipo.** Una franja entre la cabecera y las pestañas dibuja el frente del equipo con cada puerto en
  su orden físico (RJ45 de cobre, jaula SFP con o sin módulo; dos filas en equipos grandes) y su estado: activo, sin enlace,
  apagado o sin módulo. Al tocarlo se ven su descripción, velocidad, dirección y vecino. Pestaña nueva **Puertos** con la tabla
  de interfaces, filtros, «Exportar CSV» y «Lo que dice el respaldo» (la línea de la configuración y la de estado). El estado se
  lee en la misma conexión del respaldo: MikroTik, Cisco IOS/IOS-XE, ASA/FTD y Juniper; en el resto, apagado o habilitado según
  la configuración.
- **Restaurar una copia en otro equipo del mismo modelo.** Desde la versión o el menú «…» de la ficha: en otro equipo de
  OpenNCCM, en uno nuevo por SSH (IP, usuario y contraseña, que no se guardan) o por cable de consola conectado al PC del
  administrador (puerto serie desde Chrome o Edge). Primero se valida que el modelo coincide y se muestran los comandos; el
  equipo de origen y sus copias no se tocan.

## 2.11.1
- **Mapa de la red: el clic en un equipo vuelve a abrir su panel.** Con un clic de ratón no pasaba nada; ahora se abre
  el panel con cómo se reconoció, sus enlaces y «Agregar a OpenNCCM» o «Abrir el equipo».

## 2.11.0
- **Importar desde Zabbix por grupos.** Al conectar salen los grupos de Zabbix con cuántos hosts y nuevos tiene cada uno.
  Se marcan los que se quieren migrar y solo se listan e importan sus equipos; en la lista se desmarcan los que no se
  quieren traer.
- **MikroTik: descargar el .rsc y el .backup.** En el menú «…» del equipo: el .rsc del último respaldo y el .backup, que
  el router crea en el momento; OpenNCCM lo trae por SFTP, lo borra del equipo y puede cifrarlo con una contraseña.
- **Mapa de la red: switches Ubiquiti.** Los UniFi y EdgeSwitch se reconocen por el modelo (US-8/16/24/48, USW, USL, ES-)
  o por la MAC de Ubiquiti. En MikroTik, un vecino LLDP con «platform» vacío ya no pierde la descripción con su modelo.
- **Mapa de la red: redes juntas y filtro «Red».** Las redes sin enlaces entre sí ya no quedan muy separadas: se colocan
  en filas y se puede ver solo una. Iconos por tipo de equipo y distintivos por fabricante, con filtro por fabricante y
  «Cómo se reconoció» en el panel. Se nombran los vecinos que no salen por su tipo.
- **Mapa de la red: puertos legibles y panel que no tapa.** Cada puerto va sobre su enlace, junto al equipo y con fondo;
  los que se pisan se apartan. En un equipo con más de 8 enlaces solo se ve el puerto del vecino (los propios están en
  «Enlaces») y al pasar el ratón por un enlace se ven los dos. El panel ocupa su propio espacio (abajo en pantallas
  estrechas) y el mapa se encuadra para que se vean el equipo y sus vecinos.
- **Copias de recuperación: 4 a la vista** y una barra en el borde para subir y bajar.
- **Respaldar con su propio icono**, que ya no parece el de descargar.

## 2.10.3
- **Mapa de la red: los CPE de los clientes ya no salen aunque cada uno vaya en su VLAN.** Un modelo de antena de cliente
  (LiteBeam, NanoStation, NanoBeam, SXT, LHG…) visto por su radio nunca se dibuja. Antes, con una VLAN por cliente, cada
  CPE llegaba solo por su interfaz y se tomaba por el otro extremo de un PtP.

## 2.10.2
- **Mapa de la red: tipos de equipo.** En Ajustes › Mapa de la red se eligen los equipos descubiertos que salen (MikroTik,
  Ubiquiti, Juniper, Cisco, Huawei, HP/Aruba, Extreme y otros). El tipo se reconoce por lo que anuncia el vecino o por el
  nombre de sus puertos. Los equipos que ya están en OpenNCCM salen siempre.
- **Enlaces inalámbricos con línea discontinua.** Si por un puerto se ve un solo equipo por su radio, es el otro extremo de
  un PtP o la estación, y sale. Si se ven varios, son los CPE de un punto de acceso y no salen; el punto de acceso, sí.
- **«ñ» y acentos en los nombres de vecinos MikroTik**, que RouterOS escribe como `\F1` o `\C3\B1`.

## 2.10.1
- **Mapa de la red con MikroTik reales**, corregido:
  - los nombres con espacios (`SW-C-Los Llanos`) se leen enteros;
  - el puerto de RouterOS 7 llega sin el bridge delante (`bridge1/ether1` → `ether1`);
  - la IPv6 local (`fe80::…`) ya no se toma como IP del vecino;
  - en un puerto con varios vecinos solo cuenta el visto por LLDP: MNDP y CDP atraviesan los bridges y traían a todos
    los MikroTik del segmento como si estuvieran enchufados ahí.
- **El mapa muestra solo equipos de red, no de usuario**: los CPE de los clientes (LiteBeam, NanoStation…) vistos por su
  radio se quitan, también de lo leído antes de esta versión, y una nota dice cuántos. Un radioenlace entre dos equipos sí
  se dibuja.
- **Moverse por el mapa**: arrastrando el fondo, con los botones +, −, «Ver todo el mapa» y «Pantalla completa», con dos
  dedos en el panel táctil o con las flechas del teclado.

## 2.10.0
- **Mapa de la red** (menú lateral): dibuja los equipos y los enlaces entre ellos, con el puerto de cada extremo.
  - Los vecinos LLDP/CDP se leen en cada respaldo, con una orden de solo lectura: Cisco, Arista, Juniper, Huawei,
    HP/Aruba, Dell, MikroTik, EdgeSwitch y EXOS, entre otros.
  - Los vecinos que aún no están en OpenNCCM aparecen marcados, y «Agregar a OpenNCCM» abre el alta con su nombre e IP.
  - Se puede arrastrar, hacer zoom, buscar y filtrar por grupo.
  - Se desactiva en Ajustes › Mapa de la red.
- **Copia en Git** (Ajustes › Copia en Git):
  - cada versión nueva de una configuración se sube como un commit a GitHub, GitLab, Gitea… (un archivo por equipo y una
    carpeta por grupo), por HTTPS con token o por SSH;
  - las contraseñas, claves y comunidades SNMP se ocultan;
  - «Probar y subir todo ahora» hace la exportación completa.
- **IP y VLAN** (menú lateral): inventario de todas las IP y VLAN, sacado de las configuraciones guardadas.
  - Avisa de las IP repetidas y de las VLAN con nombres distintos según el equipo.
  - Se puede filtrar, buscar y exportar a CSV.
- **API con tokens** (Mi cuenta › Tokens de API):
  - cada usuario crea los suyos, que actúan con sus permisos y grupos, solo sirven en `/api/…` y caducan si se quiere;
  - nuevas peticiones: ver un equipo, sus versiones y su configuración, dar de alta equipos y lanzar un respaldo;
  - todo queda en Auditoría.
- **Firmware en remoto para MikroTik y Ubiquiti**:
  - **MikroTik**: con el paquete `.npk` o **en línea**, desde MikroTik por canal (stable o long-term). Probado en RouterOS
    reales: de 7.23.8 a 7.25 con el paquete, y en línea.
  - **Ubiquiti airMAX y airFiber**: método de Ubiquiti, `/tmp/fwupdate.bin` y `fwupdate -m`.
  - **EdgeRouter**: `add system image`.
  - En todos, el equipo se respalda antes y se comprueba que la imagen llegó entera.

## 2.9.0
- **Reversión revisada a fondo, plataforma por plataforma.** Un error al restaurar puede dejar un equipo sin servicio, así
  que se rehízo con estas reglas:
  - **MikroTik RouterOS** tiene reversión propia. Compara las exportaciones elemento a elemento:
    - `set` con su selector para lo que cambió;
    - `add` para lo que falta, con las reglas de firewall en su posición (`place-before`);
    - `remove [ find where … ]` para lo que sobra, en orden inverso.

    Se aplica en **modo seguro**: si un comando falla o se corta la conexión, RouterOS deshace todo lo aplicado. Probado
    en **RouterOS 7.25, 7.23.8 y 6.49.23 reales** en la CI, que comprueba que el equipo queda exactamente como antes y que
    un fallo a mitad se deshace.
  - **Extreme EXOS** y **VOSS / Fabric Engine** tienen reversión propia (`delete vlan`, `configure vlan … delete ports`,
    `vlan members remove`…). Borran las VLAN que sobran antes de mover sus puertos.
  - **Junos** se aplica con `commit confirmed 5`: si el cambio corta el acceso, el equipo vuelve solo a la configuración
    anterior.
  - **Nunca se toca la dirección por la que OpenNCCM gestiona el equipo.** Lo que no se puede deshacer con seguridad se
    muestra en la solicitud como «Revisar a mano», y esa solicitud no se ejecuta sola.
  - **WatchGuard, FortiGate, UniFi, airOS, SwitchOS y las demás plataformas sin reversión segura por comandos** ya no
    vuelven a aplicar la configuración entera, que se detenía en la primera línea existente y dejaba el equipo a medias.
    OpenNCCM explica cómo restaurar con la herramienta del fabricante. La reversión automática no se puede activar en ellas.
- **MikroTik**:
  - con **RouterOS 7.25** los respaldos fallaban, porque al iniciar sesión pide el tamaño del terminal; ya funcionan;
  - las respuestas de error de RouterOS («no such item», «invalid value»…) ya se reconocen; antes se daban por buenas.
- **Ubiquiti**:
  - nuevos perfiles: **airMAX M** (M2, M5 con airOS 6), **airMAX AC** (airOS 8), **airFiber** (AF5XHD, AF60, AF24HD, AF5X),
    **AP UniFi** y **USG**;
  - el respaldo de airOS, airFiber y UniFi AP es `/tmp/system.cfg`, con la versión y el modelo.
- **MikroTik SwitchOS** (SwOS y SwOS Lite): respaldo por HTTP (`backup.swb`) con el modelo, la versión y la serie.
- **Importar equipos desde Zabbix** (6.0 a 7.x):
  - trae el nombre, la IP, la descripción y el inventario, y deduce la plataforma;
  - sin credencial, los equipos quedan desactivados hasta asignársela;
  - probado con Zabbix 6.0, 7.0 y 7.4 reales.
- **Dispositivos**: paginador de 15, 25, 50 o 100, que respeta el filtro al escribir.
- **Línea base**: «Difiere de la base» ya no se queda para siempre cuando la base guardaba la hora o el tiempo encendido.
- **Ficha del equipo**:
  - el menú «…» ya no corta «Marcar la última versión como línea base»;
  - si algunas versiones se cifraron con otra clave (`secret.key`), la ficha se abre con un aviso en lugar de dar un error,
    y los respaldos siguen funcionando.
- **Menú lateral**: se quitó el buscador bajo el logo, que se cortaba. El de la barra superior hace lo mismo con Ctrl K.
- 131 plataformas.

## 2.8.0
- **Ajustes › Vulnerabilidades (NVD)** aparece en el menú lateral de Ajustes. En la 2.7.0 la sección existía pero no se podía
  abrir desde el menú.
- **Cumplimiento**: si una regla la incumplen varios equipos, un solo botón **«Corregir en N equipos…»** abre la lista de equipos
  con casillas (todos marcados) y crea una solicitud de corrección por cada uno. Antes había un botón por equipo. La lista
  de nombres de la regla muestra los cinco primeros y «y N más».
- **Nueva tarea**: el nombre propuesto corresponde a la acción elegida («Auditoría semanal», «Búsqueda semanal de CVE»,
  «Copia de recuperación semanal»…) y cambia con ella mientras no se haya escrito otro. Antes se quedaba «Respaldo nocturno».
- **Consola SSH**: si el equipo tiene una credencial asignada y el inicio automático está desactivado, la consola lo avisa.
  A los administradores les enlaza a Ajustes para activarlo. El valor por defecto no cambia.
- **Instalador**: las listas de opciones («Acceso HTTPS», «Base de datos»…) ya no se salen del recuadro: se dimensionan
  según la opción más larga y los textos caben en una consola de 80 columnas.
- **`opennccm status`** ya no muestra «(?)» junto a la carpeta del código en las instalaciones hechas desde el paquete
  publicado.

## 2.7.0
- **Ubuntu Server y Debian** (Ubuntu 26.04, 24.04 y 22.04 LTS; Debian 12 y 13): perfil nuevo «Ubuntu Server / Debian».
  - Respalda solo configuración en texto: red (netplan o interfaces), DNS, SSH, cuentas y sudo, repositorios, paquetes
    instalados a mano, servicios, cron, hora (chrony en 26.04 o timesyncd) y cortafuegos (ufw, iptables, nftables).
  - Nunca copia contraseñas, claves privadas, certificados ni datos; las contraseñas de wifi de netplan salen como
    «***». Un respaldo ocupa unos 20–60 KB.
  - Sin contadores de paquetes ni versiones de cada paquete, para que los respaldos no detecten cambios falsos.
  - Con sudo sin contraseña respalda también lo que solo lee root; sin él, el resto.
  - Paquete de cumplimiento «Ubuntu / Debian · Seguridad básica» (8 reglas, sin corrección automática).
  - Actualizaciones disponibles: avisa de la nueva LTS o estable y de la fecha de fin de soporte de la instalada.
- **Respaldos**: la fecha, la hora y el tiempo encendido que algunos equipos escriben en comentarios (por ejemplo
  «! System Up Time» de Ubiquiti) ya no cuentan como cambio, en ninguna marca. El primer respaldo tras actualizar
  tampoco ve un cambio falso por las versiones guardadas antes.
- **Consola SSH**: si no puede abrirse, explica el motivo (sesión caducada, sin permiso, dirección distinta por un
  proxy, o conexión bloqueada) en lugar de mostrar solo «[Desconectado]».
- **Clave de la API de NVD desde Ajustes** (sección «Vulnerabilidades (NVD)»): se guarda cifrada y acelera la
  búsqueda de CVE y de versiones de firmware. La de opennccm.env sigue teniendo prioridad.
- **Apoya OpenNCCM**: GitHub Sponsors junto a Ko-fi, y botón «Sponsor» en el repositorio de GitHub.

## 2.6.0
- **Paginador** debajo de las listas de Cumplimiento, Vulnerabilidades, Alarmas, Usuarios, Credenciales, Grupos y Auditoría:
  - 15 por página por omisión, con la opción de ver 25, 50 o 100;
  - la elección no se guarda: al salir de la página vuelve a 15;
  - en Alarmas, los filtros (severidad, «Sin reconocer») se mantienen al cambiar de página.
- **Cumplimiento y Vulnerabilidades vuelven a verse como en la 2.4.1.** La densidad «Compacta» de la 2.5.0 también
  cambiaba esas tablas; ahora solo acorta Dispositivos, Cambios y Alarmas, como se pidió.
- **«Recordarme»** ahora pide además al gestor de contraseñas del navegador que guarde la contraseña (OpenNCCM no la guarda).
  Los navegadores solo lo hacen si confían en el certificado del servidor: si muestran «No es seguro», instala el
  certificado con el enlace del inicio de sesión.

## 2.5.0
- **«Recordarme en este equipo»** en el inicio de sesión:
  - recuerda el usuario en ese navegador y mantiene la sesión abierta varios días (14 por omisión), aunque se cierre el
    navegador y sin cierre por inactividad;
  - la contraseña no la guarda OpenNCCM: el formulario permite que la guarde el gestor de contraseñas del navegador;
  - en Ajustes se elige la duración (1 a 30 días; 0 quita la opción). Bajarla acorta también las sesiones ya abiertas, y
    cambiar la contraseña las cierra todas;
  - funciona con la verificación en dos pasos, Active Directory y Microsoft Entra ID.
- **Aviso «Actualización disponible»**: su botón principal no hacía nada si ya se estaba en Sistema (solo cambiaba el
  ancla de la página y el aviso seguía tapándola).
  - Si el servidor puede actualizarse desde la web, el aviso tiene un botón «Actualizar ahora» que inicia la actualización
    (copia previa y vuelta atrás automática si falla).
  - «Ver detalles» o «Cómo actualizar» cierran el aviso y llevan a la sección Actualizaciones.
- **Densidad de tablas**: la opción «Compacta» casi no se notaba. Ahora acorta también las filas de Dispositivos, Cambios
  y Alarmas, y Mi cuenta muestra una vista previa. Además, cualquier clic en Mi cuenta volvía a guardar la preferencia.
- **Selector de fabricante** al agregar o editar un equipo: los nombres de grupo («A10 Networks», «Adtran»…) salían en
  blanco en el tema oscuro y casi ilegibles en el claro. Ahora usan los colores del tema con buen contraste.

## 2.4.1
- **«Corregir» ya no propone direcciones de ejemplo.** Algunas reglas de los paquetes de VOSS, Ubiquiti, MikroTik, Junos
  y FortiGate traían como corrección comandos con direcciones de documentación (192.0.2.x) o servidores que dependen de
  cada red (syslog, NTP, y el acceso a Winbox de MikroTik, que habría quedado limitado a una red inexistente).
  - Esas reglas siguen comprobándose, pero ya no tienen corrección automática: la dirección real la decide cada red.
  - Al actualizar se quitan de las reglas ya cargadas, salvo que un administrador las haya modificado.
- **Red de seguridad en las solicitudes de cambio**: si los comandos usan direcciones de ejemplo (192.0.2.x,
  198.51.100.x o 203.0.113.x), la solicitud lo avisa y no se puede aprobar (sí rechazar). Tampoco se ejecuta sola
  cuando la aprobación está desactivada.
- **Protección contra quedarse sin acceso**: una solicitud no se puede aprobar (y lo explica) si dejaría a OpenNCCM sin
  entrar al equipo:
  - apaga Telnet en un equipo al que OpenNCCM entra por Telnet (por ejemplo, «Sin Telnet» de los paquetes de
    cumplimiento), o apaga SSH en uno al que entra por SSH;
  - bloquea root por SSH en un Junos al que OpenNCCM entra con el usuario root.
- Ubiquiti: «Sin HTTP sin cifrar» ya no tiene corrección automática, porque «ip http secure-server» necesita un
  certificado y sin él se perdería la interfaz web.
- Revisadas una a una las correcciones de los paquetes de VOSS, Ubiquiti, MikroTik, Junos y FortiGate. En los switches
  UniFi, el controlador puede deshacer los cambios hechos por CLI.

## 2.4.0
- **Actualizaciones de firmware disponibles**: OpenNCCM comprueba una vez al día (o con «Comprobar ahora») si hay una
  versión más nueva del firmware de cada equipo, dice qué mejora y, cuando el fabricante lo permite, la descarga.
  - **MikroTik**: versión estable y long-term desde el sitio oficial, con las novedades del fabricante y el archivo para
    la arquitectura del equipo.
  - **Ubiquiti** (EdgeSwitch, EdgeRouter y UniFi Switch): la versión para el modelo exacto, desde el catálogo oficial.
  - **Resto de equipos con CPE** (Cisco, Fortinet, Juniper, Palo Alto, Arista, WatchGuard, Aruba…): la versión más nueva
    de su rama según el diccionario de NVD (NIST), y la más reciente aunque sea de otra rama. En Cisco IOS solo se
    recomienda la misma serie, porque las series dependen del hardware.
  - **Qué mejora**: cuántos CVE de la versión instalada corrige la nueva (con los críticos y altos), las novedades del
    fabricante cuando las publica y el enlace a las notas de la versión (Fortinet, Palo Alto y otros).
  - **Descarga a la biblioteca de Firmware** (MikroTik y Ubiquiti, solo administradores): desde el sitio oficial y
    verificando la suma SHA-256 del fabricante; si no coincide, el archivo se descarta. Después se instala con el flujo de
    siempre (solicitud, aprobación y respaldo previo).
  - Los fabricantes que piden cuenta para descargar se indican con un aviso: la versión nueva y lo que mejora se muestran
    igual, y el archivo se baja del portal del fabricante y se sube en Firmware › Cargar imagen.
  - Dónde se ve: una etiqueta «Firmware X disponible» en la cabecera del equipo, la sección «Actualizaciones de firmware»
    de su pestaña Firmware y la pestaña nueva **Actualizaciones disponibles** en Firmware y ciclo de vida. Además, un
    aviso en Alarmas una sola vez por versión.
  - La comprobación diaria se puede desactivar en esa pestaña. Solo consulta los sitios de MikroTik, Ubiquiti y NVD; no
    envía datos de los equipos.
- **Más equipos con búsqueda de CVE y de versiones**: Cisco WLC, Aruba OS-Switch y ProCurve, ArubaOS, Check Point Gaia,
  F5 BIG-IP, ExtremeXOS, VyOS y Brocade Fabric OS ahora tienen su nombre de producto en NVD (antes se buscaban por texto
  y casi nunca se encontraba nada). pfSense compara su versión sin el sufijo «-RELEASE».

## 2.3.0
- **El código QR de la verificación en dos pasos vuelve a poder escanearse.** Se mostraba recortado: el navegador cortaba
  el borde derecho e inferior (con parte de las esquinas de posición) en lugar de reducirlo al tamaño de la página, y las
  apps de autenticación no lo reconocían. Ahora se escala completo y lleva el margen blanco que piden los lectores.
- **Nuevos paquetes de cumplimiento** (llegan solos al actualizar; si se borra uno, no vuelve a aparecer):
  - **MikroTik RouterOS · Seguridad básica** (13 reglas): Telnet, FTP, web HTTP y API desactivados; Winbox y acceso por
    MAC limitados; descubrimiento de vecinos limitado; sin bandwidth-test; SSH con cifrado fuerte; cortafuegos que descarta
    lo no permitido hacia el router; sin comunidad SNMP «public»; syslog y NTP.
  - **Juniper Junos · Seguridad básica** (13 reglas): sin Telnet, FTP ni J-Web por HTTP; SSH sin acceso de root;
    contraseña de root; bloqueo por intentos; cierre de sesión inactiva; RADIUS o TACACS+; sin comunidades SNMP por
    defecto; syslog, NTP y banner.
  - **FortiGate · Seguridad básica** (11 reglas): sin Telnet ni HTTP en las interfaces; cierre de sesión ≤ 15 min; hosts
    de confianza para los administradores; política de contraseñas; cifrado fuerte; sin comunidades SNMP por defecto;
    syslog o FortiAnalyzer; NTP; banner y cuenta «admin» renombrada.
  - Muchas reglas traen el comando para corregirlas con «Corregir».
- **Vulnerabilidades de Juniper**: NVD registra Junos con la versión y la actualización por separado («21.4:r3-s5»).
  OpenNCCM enviaba «21.4R3-S5.4» y encontraba menos de la mitad de los CVE (38 de 87 en Junos 21.4R3-S5). Ahora convierte
  la versión, también en las ramas X de los SRX («15.1X49-D200» → «15.1x49:d200»).
- **Vulnerabilidades de WatchGuard**: Fireware se busca en NVD por su CPE (antes no se buscaba). El inventario usa
  «show sysinfo» para leer el modelo, la serie y la versión.

## 2.2.1
- **Buscar CVE de firmware** ya no falla con «Error consultando NVD: Response ended prematurely» en equipos con muchos CVE,
  como Cisco IOS 12.4:
  - OpenNCCM pide los CVE a NVD en páginas pequeñas (40 por petición, menos si NVD corta la respuesta) en lugar de 200 de
    una vez; antes una sola respuesta podía pesar más de 1,5 MB, tardar casi un minuto y cortarse a medias;
  - cada página se reintenta hasta 4 veces ante cortes, tiempos agotados o el límite de peticiones de NVD, que ahora se
    respeta también entre búsquedas;
  - la búsqueda va en segundo plano: el botón responde al instante y la pestaña Vulnerabilidades muestra «Buscando en
    NVD…» y se actualiza sola al terminar;
  - si NVD no responde, se conservan los CVE de la búsqueda anterior y el aviso explica qué pasó.
- **Vulnerabilidades de switches Ubiquiti**: EdgeSwitch, UniFi Switch y EdgeRouter se buscan ahora por sus nombres en
  NVD (antes se buscaba por texto y no se encontraba nada). La versión se compara sin el número de compilación.
- **Extreme VOSS / Fabric Engine**: NVD casi no registra sus vulnerabilidades; la pestaña Vulnerabilidades lo indica y
  enlaza a los avisos de seguridad de Extreme. También se consulta el nombre antiguo del producto (Avaya VSP).
- **Nuevos paquetes de cumplimiento** (llegan solos al actualizar):
  - **Extreme VOSS · Seguridad básica** (12 reglas): sin Telnet, FTP, TFTP ni rlogin; SSH; web solo por HTTPS; sin
    comunidades SNMP por defecto; cierre de sesión inactiva; RADIUS o TACACS+; syslog; NTP y banner.
  - **Ubiquiti EdgeSwitch/UniFi · Seguridad básica** (7 reglas): sin la cuenta por defecto «ubnt»; SSH; sin Telnet ni HTTP
    sin cifrar; sin comunidades SNMP por defecto; syslog y SNTP.
  - Muchas reglas traen el comando para corregirlas con «Corregir». Si se borra un paquete, no vuelve a aparecer.

## 2.2.0
- **Apoya OpenNCCM**: forma de apoyar el proyecto con una donación, opcional y sin rastreo.
  - Los administradores ven un botón en la barra superior que abre las opciones (en el móvil, en un diálogo).
  - Acerca de y Sistema muestran una tarjeta con las mismas opciones.
  - Tras actualizar a una versión nueva, el primer administrador que entra ve una vez un aviso con «Donar» y «Ahora no».
  - Por ahora la opción es Ko-fi. Los enlaces solo se abren si se pulsan, en una pestaña nueva, y no se envía ningún dato del servidor; la página de privacidad lo explica.
- **Cambiar la zona horaria** ya no termina en una página en blanco («502 Bad Gateway»):
  - la página de espera vuelve sola a Sistema cuando el servicio reiniciado responde de verdad;
  - «Volver a Sistema» espera a que el servicio esté listo.
- Además, mientras OpenNCCM se reinicia (zona horaria o actualización), nginx muestra una página que se recarga sola en lugar del error.
- Los avisos flotantes vuelven a aparecer abajo a la derecha de la ventana también en las páginas largas (antes podían quedar al final del contenido, fuera de la vista).

## 2.1.0
- **Inicio de sesión con Active Directory** (LDAP/LDAPS o StartTLS): las personas entran con su usuario y contraseña de la red en
  el mismo formulario. Su rol (administrador, operador o lector) sale de los grupos del directorio, también anidados; la cuenta se
  crea al primer ingreso y el rol se actualiza en cada ingreso. **Probar conexión** comprueba la cuenta de servicio y el rol que
  tendría un usuario.
- **Inicio de sesión con Microsoft Entra ID (Azure AD)**: botón **Entrar con Microsoft** (OpenID Connect con PKCE, state y nonce;
  se validan la firma, el emisor, la audiencia y la caducidad del token). Los roles salen de los grupos o de los roles de
  aplicación del token. Con la URL de descubrimiento sirve también para Google Workspace, Keycloak, Okta y otros proveedores.
- Las cuentas locales siguen funcionando siempre y nunca se toman desde un directorio. En **Usuarios** se marca el origen de cada
  cuenta; la verificación en dos pasos, los bloqueos por intentos fallidos y la auditoría también se aplican a estas cuentas.
- Nueva sección **Ajustes → Inicio de sesión** y capítulo en el manual de funciones.

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
