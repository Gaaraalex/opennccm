# Cómo contribuir

## Agregar un fabricante

1. Añada un `Profile(...)` en `app/drivers.py` con un `device_type` de
   [Netmiko](https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md) y el comando que muestra la configuración.
2. Si lo probó con un equipo real, indique el modelo y la versión en el *pull request*.

## Ejecutar las pruebas

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash tests/run-tests.sh .venv/bin/python
```

`tests/fakedev.py` simula un router Cisco IOS por SSH/SCP. Las pruebas de instalación (`tests/update-test.sh`,
`tests/tui_drive.py`) necesitan un Ubuntu desechable: se ejecutan solas en GitHub Actions.

## Publicar una versión

1. Actualice `app/__init__.py` (`__version__`) y agregue la sección `## X.Y.Z` al principio de `CHANGELOG.md`.
2. Regenere la documentación si cambió la interfaz (`docs/src/capturas.py` y `docs/src/build.py`, en Windows con la demo).
3. Cree la etiqueta: `git tag vX.Y.Z && git push origin vX.Y.Z`.
   GitHub Actions ejecuta todas las pruebas y, si pasan, publica la versión. Los servidores la reciben con
   `sudo opennccm update`.

## Reglas para las actualizaciones

La base de datos se migra sola **agregando** columnas y tablas. Si una versión necesita renombrar o transformar
datos, incluya un paso de migración explícito y una prueba en `tests/update-test.sh`.
