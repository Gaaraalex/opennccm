# Historial de versiones

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
