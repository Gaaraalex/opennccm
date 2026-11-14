# 9. Rendimiento

OpenNCCM tiene que seguir siendo rápido con miles de equipos y decenas de miles de copias. Casi todo lo lento que se
encontró venía de la base de datos, no de Python ni de las plantillas. Estas son las reglas.

## Reglas

1. **No traiga objetos enteros para contar.** `db.scalars(select(ConfigVersion))` carga cada copia **con su texto
   cifrado**. Para contar o agrupar, pida solo las columnas (`select(ConfigVersion.device_id, ConfigVersion.created_at)`)
   o, mejor, cuente en la base (`func.count`, `group_by`). Ejemplo: `reports.analytics` agrupa por hora y origen en
   SQL (`_hour_key`, compatible con SQLite y PostgreSQL) en lugar de recorrer 20 000 copias.
2. **Nada de una consulta por equipo dentro de un bucle** (N+1). Para «la última copia de cada equipo» use
   `services.latest_versions(db, ids)` (una consulta con `max(id)` agrupado). Para relaciones, `selectinload` o
   `joinedload` en la misma consulta.
3. **Filtre en Python o en la plantilla solo lo que ya es chico.** Un `selectattr` sobre todas las fallas, dentro de un
   bucle por regla, es cuadrático: prepare un diccionario en la ruta (`fails_by_rule`) y búsquelo en la plantilla.
4. **No lea columnas grandes que la página no muestra.** La lista de equipos difiere (`defer`) inventario, vecinos,
   puertos y licencia (`main.LIST_DEFER`). Cuidado: un atributo diferido que se lee después de cerrar la sesión da
   error, así que solo se difiere lo que la plantilla no usa.
5. **Índices que cubran la consulta.** En SQLite, si la columna que filtra está después de una columna de texto
   grande, leer la fila entera es lo caro: un índice con todas las columnas que usa la consulta (`ix_cv_kind_created`,
   `ix_cv_device_kind`, `ix_events_ack_id`) la resuelve sin tocar la tabla. Antes de agregar un índice, mire el plan
   con `EXPLAIN QUERY PLAN` **de todas las consultas de esa tabla**: un índice nuevo puede hacer que SQLite elija mal
   en otra (pasó con las alarmas: un índice por `acknowledged, severity` hizo que «las 5 más recientes» ordenara
   13 000 filas; se cambió a `acknowledged, id, …` y las listas ordenan por `id`).
6. **Lo que se parsea de una copia guardada se puede guardar en memoria por id de versión** (no cambia nunca):
   `main._ipam_parse` guarda las IP y VLAN de cada copia.
7. **Las respuestas grandes van comprimidas** (`_Gzip` en `main.py`) y los estáticos con `?v=` se guardan un año en el
   navegador. Una respuesta que llega en vivo línea a línea (como ping) no se comprime: agréguela a `_Gzip.LIVE`.
8. **Lo que se calcula en cada página** (la barra lateral: `web._nav_counts`) tiene que ser una consulta barata con
   índice; mídalo con la base grande.

## Cómo medir

Base grande de prueba (2000 equipos, 20 000 copias, 60 000 eventos; direcciones de la red de pruebas 198.18.0.0/15):

```bash
export NCM_DATA_DIR=/tmp/perf NCM_KEY_FILE=/tmp/perf/secret.key NCM_SYSLOG_ENABLED=0 NCM_FW_CHECK_NEW=0
mkdir -p /tmp/perf && python scripts/perf_seed.py
uvicorn app.main:app --port 8006 &
python scripts/perf_bench.py 8006
```

Para comparar con la versión anterior: otra copia del código (por ejemplo `git worktree add /tmp/anterior v2.36.0`)
con **una copia de la base sin los índices nuevos**, en otro puerto, y `python scripts/perf_bench.py 8006 8007`:
alterna las peticiones entre los dos para que la carga de la máquina afecte igual a ambos.

Para ver **dónde** se va el tiempo de una página: `cProfile` alrededor de la llamada a la ruta (o de la función:
`reports.analytics(None, 30)`), ordenado por tiempo acumulado, y filtrado por `app/`. Si lo que más tarda es
`fetchall` o `loading.py` de SQLAlchemy, es la regla 1, 2 o 5.

## Resultado de la versión 2.37 (2000 equipos, mismo equipo y misma carga)

| Página | Antes | Después | Por la red |
|---|---:|---:|---|
| Panel (`/`) | 1422 ms | 404 ms | 41 KB → 10 KB |
| Dispositivos | 403 ms | 394 ms | 3975 KB → 208 KB |
| Cumplimiento | 1086 ms | 347 ms | 3226 KB → 74 KB |
| Búsqueda en configuraciones | 790 ms | 221 ms | 32 KB → 8 KB |
| IP y VLAN | 1741 ms | 65 ms | 32 KB → 8 KB |
| Analítica | 1008 ms | 76 ms | 45 KB → 10 KB |
| Alarmas | 53 ms | 26 ms | 44 KB → 9 KB |
| Ficha de un equipo | 37 ms | 24 ms | 77 KB → 15 KB |
| App móvil: panel | 471 ms | 198 ms | — |
| App móvil: lista de equipos | 144 ms | 153 ms | 961 KB → 50 KB |

Lo que queda más lento es dibujar la lista de 2000 equipos (unos 400 ms, casi todo en la plantilla). Si hiciera
falta, el siguiente paso sería paginarla en el servidor (hoy se pagina en el navegador con `data-pager`). Se hizo en la
2.44: ver abajo.

## Prueba de carga y capacidad (2.44)

`tests/load_test.py` levanta un OpenNCCM nuevo en una carpeta temporal (puerto 8300) y `tests/loadsim.py`, un proceso
que simula miles de Cisco IOS por SSH en un solo puerto (2300; el tamaño de la configuración lo da el usuario con el que
se entra: ~250, ~3.000 o ~40.000 líneas). Por escalones agrega equipos con su historial, hace «Respaldar todos» de
verdad, mide cada página (la primera vez y la mediana de 3), varios usuarios a la vez, la memoria y la base de datos, y
termina con error si una página pasa de `--limit` segundos o falla un respaldo:

```bash
python tests/load_test.py --steps 100,500                 # en un PC: unos 5 minutos
python tests/load_test.py --steps 100,1000,2500,5000 --backup-steps 100,1000,5000   # lo que corre la CI
```

El trabajo «Prueba de carga y capacidad» de la CI deja `capacidad.md` en el resumen y `capacidad.json` como artefacto.
Ese JSON, copiado a `app/capacity_ref.json`, es la tabla «Medido en la prueba de carga» de Administración › Capacidad
(`app/capacity.py`), que además estima con los respaldos medidos en cada instalación (`capacity.record_cycle`).

Lo que encontró y cómo se resolvió (500 equipos, un PC; 8 usuarios a la vez):

| Página | Antes | Después |
|---|---:|---:|
| Dispositivos | 1.219 KB · 0,17 s | 84 KB · 0,04 s (paginada en el servidor con más de `main.BIG_LIST` = 400 equipos) |
| Cumplimiento | 1.957 KB · 0,57 s | 157 KB · 0,10 s en el servidor (todo contado en SQL; lista de «Corregir» al abrir el cuadro) |
| Panel | 0,40 s | 0,18 s (`eol.report` una sola vez; las 10 copias recientes por el índice tipo + fecha, sin su texto) |
| Buscar en configs | 3.652 KB | 756 KB (se dibujan los primeros `main.SEARCH_MAX` = 100 equipos; los demás se cuentan) |
| Progreso de «Respaldar todos» | toda la lista cada 2 s por pestaña | solo las filas a la vista y las que fallaron (`/backup/job?ids=`) |

Reglas nuevas que salen de esto:

9. **Lo que crece con los equipos se pagina en el servidor** pasado un umbral (`BIG_LIST`): por debajo se mantiene la
   lista entera con filtro al escribir, como siempre.
10. **Un cuadro que necesita una lista larga la pide al abrirse** (`/compliance/rules/{id}/fails`), no viaja en la página.
11. **Lo que se sondea cada pocos segundos pide solo lo que se ve.**
12. **Lo caro que necesitan varias partes de una página se calcula una vez** y se pasa (`eol_rows` en el panel).

Lo que la prueba deja a la vista para más adelante: con un equipo simulado, cada respaldo tarda ~6 s y casi 4 son
de netmiko, que antes de cada comando vuelve a leer el prompt (`find_prompt`, ~0,45 s). OpenNCCM usa en promedio un
20 % de un núcleo durante los respaldos: espera al equipo. Pasar el prompt ya leído (`expect_string`) ahorraría ese
tiempo, pero hay plataformas cuyo prompt cambia en cada comando (Extreme EXOS lleva un contador), así que hay que hacerlo
plataforma por plataforma y probarlo en el laboratorio.
