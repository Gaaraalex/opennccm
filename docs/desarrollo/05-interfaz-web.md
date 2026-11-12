# 5. Interfaz web

## Rutas

- La mayoría están en `app/main.py` (equipos, versiones, cambios, cumplimiento, usuarios, ajustes…). Las demás en
  `APIRouter` propios que `main.py` incluye al final: `routes_ops.py` (firmware, EOL, analítica, recuperación),
  `consolecenter.py` y `console.py` (consola), `refconfig.py`, `kb.py`, `retire.py`, `mobile.py`,
  `mobile_app.py`, `security.py`, `assistant.py`, `aichat.py`, etc.
- Las rutas son **funciones normales** (`def`, no `async def`) salvo las que leen un formulario o JSON
  (`await request.form()` / `await request.json()`); FastAPI corre las normales en su grupo de hilos.
- Un formulario que cambia algo responde con `go(url)` (redirección 303) y deja el aviso con
  `flash(request, texto, "ok"|"error")`.
- Toda acción que importa queda en Auditoría: `log_action(request, user, "accion", "detalle")` (o `audit()`).

## Permisos

| Rol | Puede |
|---|---|
| `viewer` | ver |
| `operator` | además respaldar, auditar, pedir cambios, usar la consola (según Ajustes › Consola) |
| `admin` | además aprobar cambios, administrar usuarios, credenciales, ajustes y sistema |

- `require(request, "operator")` al principio de la ruta. Sin sesión lanza `LoginRequired` (redirige al login);
  rol insuficiente, `Forbidden` (403).
- **Alcance**: un usuario no admin con grupos asignados solo ve los equipos de esos grupos. `device_scope(user)`
  devuelve el conjunto (o `None` = todos); `check_device(user, id)` lanza 403 si no puede ver ese equipo;
  `filter_ids(user, ids)` filtra una lista. **Toda ruta que recibe un id de equipo tiene que llamar a
  `check_device`.**
- Quien pide un cambio puede aprobarlo él mismo salvo que se desactive en Ajustes (`allow_self_approval`, separación de funciones).

## Plantillas (`app/templates/`)

- Todas heredan de `base.html` (barra lateral, barra superior, avisos, buscador Ctrl K). Las partes reutilizables
  empiezan con `_` (`_timeline.html`, `_diffview.html`, `_pager.html`, `_warn.html`…).
- `render(request, "pagina.html", user, **datos)` (`app/web.py`) agrega `user`, `flashes`, `nav_counts` (contadores
  de la barra lateral), el aviso de actualización, `profiles` y lo que pase la ruta. Con `compact=True` no calcula
  los contadores (ventanas emergentes).
- Funciones globales disponibles en todas las plantillas (definidas en `app/web.py`): `icon("nombre")` (sprite
  `static/icons.svg`), `vlogo(device_type, tamaño)` (logo de marca), `pl(n, "copia", "copias")` (plurales),
  `fwpill(...)` (etiqueta de firmware), `spark(...)` (minigráfica), filtros `fecha` y `cuando` («hace 3 min»),
  `_()` / `_f()` (traducción) y constantes como `ROLES` y `VERSION`.
- Gráficas: se dibujan en el servidor en SVG (`app/charts.py`), con las reglas de diseño del docstring de ese módulo.

## Idiomas (`app/i18n.py`)

- Se escribe **en español**. El catálogo `app/locales/en.json` tiene pares `{"texto en español": "traducción"}`.
- En las plantillas **no hace falta marcar los textos**: el cargador (`I18nLoader`) envuelve cada texto visible y
  los atributos `placeholder`, `title`, `aria-label` y `alt`. Un texto con valores, «Hay {{ n }} equipos», se busca
  como «Hay {0} equipos».
- En Python: `_("texto")`, `_f("Respaldo de {0}", nombre)` y `N_("texto")` para marcar textos que se traducen
  después (constantes de módulo).
- `python scripts/i18n_missing.py` lista lo que falta traducir; la prueba `e2e_i18n` recorre las páginas en inglés
  y falla si queda texto en español. Las entradas nuevas se agregan **al final** de `en.json`.

## Archivos estáticos (`app/static/`)

- CSS: `style.css` (base), `senal.css` (diseño «Señal»: panel, tarjetas), `warn.css`, `console.css`, `kb.css`…
- JS propio, sin frameworks: `shell.js` (comportamiento común: menús, diálogos, hojas laterales `data-sheet`,
  listas desplegables, buscador, paginado `data-pager`), `center.js` (consola), `bulkbk.js` (respaldar todos),
  `topo-layouts.js` (mapa)…
- Las plantillas los enlazan con `?v={{ VERSION }}-{{ HASH }}`; el servidor los sirve con caché de un año
  (`immutable`) cuando llevan `?v=`, así que **cambiar un archivo cambia su hash y el navegador lo vuelve a pedir**.
  Un estático nuevo enlazado sin `?v=` no se guarda en caché de esa forma.
- Logos de marca: un solo sprite `vendor-logos.svg` generado con `python scripts/build_vendor_logos.py` desde
  `static/vendor-logos/*.svg` y `app/vendor_logos.json` (con el texto convertido a trazos). Las versiones chicas son
  `vl-<marca>-chico`.

## Convenciones de la interfaz

- Textos cortos y concretos, en segunda persona, sin jerga innecesaria. Los avisos explican qué pasó y qué hacer.
- Lo peligroso se confirma (`data-confirm`) y lo que corta el servicio se marca como tal.
- Cada pantalla funciona sin JavaScript para lo esencial (formularios normales); el JS mejora la experiencia.
- Accesibilidad: cada campo con su etiqueta, botones de solo símbolo con `aria-label` (`shell.js` completa los que
  falten), contraste comprobado por `tests/unit_contrast.py`.
- Temas claro/oscuro y colores de acento: variables CSS; no ponga colores fijos en las plantillas.
