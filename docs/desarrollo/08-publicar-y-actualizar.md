# 8. Publicar y actualizar

## Versiones

- `MAYOR.MENOR.PARCHE` en `app/__init__.py` (`__version__`). El instalador y `opennccm update` la leen de ahí con
  `grep`: deje la línea `__version__ = "X.Y.Z"` tal cual.
- MENOR sube con funciones nuevas; PARCHE con arreglos. Cada versión junta varios cambios (no se publica una versión
  por cada arreglo pequeño), salvo un arreglo urgente de seguridad o de algo que impide trabajar.

## CHANGELOG.md

Una sección `## X.Y.Z` al principio, y una línea por cambio, en palabras del usuario (qué ve o qué puede hacer), no
del código:

```markdown
## 2.37.0
- [nuevo] **Título corto:** qué hace y dónde está (Menú › Página).
- [mejora] **…:** …
- [arreglo] **…:** qué fallaba, en qué equipo o versión, y qué hace ahora.
- [seguridad] **…:** …
```

Las notas de la versión en GitHub se arman solas con esa sección.

## Cómo se publica

1. Rama nueva desde `main` (por ejemplo `proxima-version-38`) con los cambios, sus pruebas y su línea en
   `CHANGELOG.md`.
2. Suba `__version__`, escriba la sección del CHANGELOG y, si cambió lo que ve el usuario, actualice el manual
   (`docs/src/manual-funciones.html`; capturas con `docs/src/capturas.py`) y regenere los PDF con
   `python docs/src/build.py` (usa Microsoft Edge en modo sin ventana).
3. *Pull request* a `main`. Espere a que pasen **todos** los trabajos de la CI (capítulo 7) y únala.
4. Cree la etiqueta sobre `main` y súbala:

   ```bash
   git checkout main && git pull
   git tag v2.37.0 && git push origin v2.37.0
   ```

5. La CI vuelve a correr todo con la etiqueta y, si pasa, el trabajo «Publicar versión» crea la versión en GitHub
   con las notas del CHANGELOG, el `.tar.gz` del código y los PDF de `docs/`.

## Cómo se actualizan los servidores

- `sudo opennccm update` (o desde la web: Administración › Sistema › Actualizar, que deja un archivo
  `update.request` que atiende `opennccm-webupdate.path` como root).
- El actualizador (`scripts/opennccm.sh`, instalado como `/usr/local/bin/opennccm`): hace una copia de la base y del
  código, trae las etiquetas de GitHub, cambia a la versión nueva, instala las dependencias, reaplica la
  configuración del sistema (`install.sh` en modo actualización: servicio, nginx, polkit) y reinicia. Al arrancar,
  `init_db()` migra la base (capítulo 3).
- `sudo opennccm rollback` vuelve a la versión anterior con su base de datos.
- `--version vX.Y.Z` instala una versión concreta; `--channel main` la rama de desarrollo (solo para pruebas).

`tests/update-test.sh` (en la CI) instala la versión anterior, crea datos, actualiza a la nueva, comprueba que todo
sigue ahí y prueba la vuelta atrás. Si una versión cambia algo del instalador o de la base, esa prueba tiene que
seguir pasando.

## El instalador (`install.sh`)

Un solo script para instalar (`curl … | sudo bash`, interactivo o `--unattended`) y para actualizar. Instala los
paquetes del sistema, crea el usuario `opennccm`, el entorno de Python, la clave, el servicio de systemd, nginx con
HTTPS (certificado propio, de Let's Encrypt o uno suyo), el cortafuegos (si se pide) y el primer administrador. Toda
opción interactiva tiene su parámetro para el modo desatendido. Pase `shellcheck` antes de subir un cambio (la CI lo
exige).

## Documentación para el usuario

| Archivo | Fuente |
|---|---|
| `docs/OpenNCCM-Guia-de-instalacion.pdf` | `docs/src/guia-instalacion.html` |
| `docs/OpenNCCM-Manual-de-funciones.pdf` | `docs/src/manual-funciones.html` |
| `docs/OpenNCCM-Guia-del-desarrollador.pdf` | esta guía (`docs/desarrollo/*.md`), con `python docs/src/guia_dev.py` |
| `README.md` y `docs/readme/*.png` | capturas con `docs/src/readme_capturas.py` |
