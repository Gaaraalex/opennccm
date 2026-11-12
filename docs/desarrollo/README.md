# Guía del desarrollador de OpenNCCM

Esta guía es para quien continúa el desarrollo de OpenNCCM: cómo está hecho, dónde está cada cosa, cómo se prueba y
cómo se publica. Está escrita para poder trabajar en el proyecto sin ayuda de nadie más: cada capítulo dice qué
archivos tocar, en qué orden y cómo comprobar que funciona.

| Capítulo | De qué trata |
|---|---|
| [1. Arquitectura](01-arquitectura.md) | Las piezas del sistema, cómo viaja una petición, procesos, hilos, tareas programadas y syslog |
| [2. Mapa del código](02-modulos.md) | Qué hace cada archivo de `app/`, agrupado por área |
| [3. Datos](03-datos.md) | Base de datos, tablas, migraciones, ajustes, cifrado y directorio de datos |
| [4. Equipos y plataformas](04-equipos-y-plataformas.md) | Perfiles, respaldo, inventario, cambios, reversión, recetas, configlets, cumplimiento; cómo agregar una plataforma |
| [5. Interfaz web](05-interfaz-web.md) | Rutas, plantillas, `render()`, permisos, idiomas, archivos estáticos y convenciones de diseño |
| [6. API y app móvil](06-api-y-app-movil.md) | API REST con token, API de la app móvil y el puente de acciones |
| [7. Pruebas y laboratorio](07-pruebas-y-laboratorio.md) | Pruebas automáticas, CI, equipo simulado y el laboratorio GNS3 con los sellos «Verificada» |
| [8. Publicar y actualizar](08-publicar-y-actualizar.md) | Versiones, CHANGELOG, etiquetas, instalador y actualización de los servidores |
| [9. Rendimiento](09-rendimiento.md) | Reglas para que las páginas sigan rápidas con miles de equipos, y cómo medirlo |

## Primeros pasos (en 10 minutos)

Requisitos: Python 3.10 o más nuevo (la CI prueba en Ubuntu 22.04 con 3.10 y en 24.04 con 3.12) y Git.

```bash
git clone https://github.com/Gaaraalex/opennccm.git && cd opennccm
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export NCM_DATA_DIR=$PWD/.datos NCM_KEY_FILE=$PWD/.datos/secret.key NCM_SYSLOG_PORT=5514
mkdir -p .datos
.venv/bin/python -m app.cli create-admin admin Admin12345
.venv/bin/python -m app.cli seed            # políticas, configlets y ejemplos
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Abra `http://127.0.0.1:8000` y entre con `admin` / `Admin12345`. Para tener un equipo con el que probar respaldos y
cambios sin red, arranque el Cisco simulado y agréguelo con la dirección `127.0.0.1`, puerto `2222`, tipo «Cisco IOS»
y usuario/contraseña `admin` / `cisco`:

```bash
.venv/bin/python tests/fakedev.py 2222
```

Las pruebas completas, como las corre la CI:

```bash
bash tests/run-tests.sh .venv/bin/python
```

## Reglas del proyecto (las que más importan)

1. **Todo en español** en la interfaz, el código (comentarios y docstrings) y los mensajes. Cada texto nuevo de la
   interfaz se traduce en `app/locales/en.json` (la prueba `e2e_i18n` falla si falta alguno).
2. **Las actualizaciones nunca pierden datos.** La base se migra sola agregando columnas, tablas e índices
   (`app/db.py`); renombrar o transformar datos exige un paso de migración explícito y su prueba.
3. **Nada se ejecuta en un equipo sin que quede rastro.** Los cambios van por solicitud de cambio (aprobación,
   respaldo antes y después, reversión); las consultas quedan en Auditoría.
4. **Lo dudoso cuenta como cambio.** Si no se sabe si un comando solo consulta, se trata como cambio
   (`app/consolecore.py`).
5. **Cada arreglo lleva su prueba** y su línea en `CHANGELOG.md` con el formato
   `- [nuevo|mejora|arreglo|seguridad] **Título:** explicación`.
6. Un equipo real de un cliente nunca se usa para probar: se prueba con el equipo simulado o en el laboratorio
   (capítulo 7).
