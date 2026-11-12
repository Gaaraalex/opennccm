# 6. API y app móvil

## API REST con token (`/api/…`)

Para scripts e integraciones. Cada usuario crea sus tokens en **Mi cuenta › Tokens de API** (empiezan con `ncm_`;
solo se guarda su sha256, con vencimiento opcional). Se envían en la cabecera:

```bash
curl -H "Authorization: Bearer ncm_…" https://opennccm.ejemplo/api/devices
```

El token actúa **con los permisos y grupos de su dueño** (`auth._token_user`). Solo vale en rutas que empiezan con
`/api/`. Sin token válido la respuesta es `401` en JSON.

| Método y ruta | Qué hace |
|---|---|
| `GET /api/devices` | lista de equipos (los que el dueño del token puede ver) |
| `POST /api/devices` | dar de alta un equipo |
| `GET /api/devices/{id}` | un equipo con su inventario y estado |
| `GET /api/devices/{id}/versions` | sus versiones |
| `GET /api/devices/{id}/config` | la última configuración (texto) |
| `POST /api/devices/{id}/backup` | respaldar ahora (encola el trabajo) |
| `GET /api/config-search?q=…` | buscar texto en las configuraciones |

Otras rutas `/api/…` las usa la propia interfaz (consola, mapa, buscar MAC/IP, asistente, favoritos) y responden
JSON con la sesión del navegador; no son una API pública estable.

Para agregar una ruta a la API pública: póngala bajo `/api/`, use `require(request, rol)` (vale con sesión o token),
filtre por `device_scope`, devuelva JSON y agréguela a `tests/e2e_api.py` y a esta tabla.

## API de la app móvil (`/api/mobile/…`)

La app móvil se conecta al servidor con la dirección, usuario y contraseña (locales o de Active Directory; con
verificación en dos pasos si la cuenta la tiene) y recibe un token propio.

- `app/mobile.py`: sesión (`/api/mobile/login`, `logout`, `me`) y acciones básicas (resumen, equipos, respaldar,
  versiones, alarmas, aprobar cambios, avisos).
- `app/mobile_app.py`: **una ruta JSON por pantalla**, que lee lo mismo que su página web (`/api/mobile/panel`,
  `devlist`, `device/{id}`, `diff`, `alarms`, `configlets`, `compliance`, `vulns`, `firmware`, `analytics`, `map`,
  `ipam`, `reports`, `kb`, `admin`, `account`…). Si cambia lo que muestra una página web, revise su par móvil.
- Consola por WebSocket: `/api/mobile/devices/{id}/console/ws` (mismo protocolo v2 que la consola web).

### El puente de acciones (`MobileBridge`)

Para no duplicar la lógica de cada acción (aprobar un cambio, crear un caso, dar de alta un equipo…), la app llama a
**la misma ruta web** a través de `/api/mobile/act/<ruta web>`:

1. `MobileBridge` (middleware ASGI) comprueba que la ruta esté en la lista permitida `ACT` (método + expresión).
2. Lee el token de la app de `Authorization: Bearer …` y pone el usuario en `scope["ncm_mobile_uid"]`.
   `auth.current_user()` lo mira primero, así que la ruta web ve a ese usuario como si tuviera sesión.
3. Llama a la ruta web y convierte la respuesta (redirección + avisos) en JSON para la app.

Para que la app pueda usar una acción web nueva: agréguela a `ACT` (lo más ajustada posible) y a
`tests/e2e_mobile_app.py`.

## Seguridad de las API

- Todo lo que se puede hacer por API se puede hacer en la web con el mismo rol: no hay atajos.
- Las rutas que cambian algo exigen `operator` o `admin` igual que en la web, y quedan en Auditoría.
- Los tokens se revocan desde Mi cuenta › Tokens de API.
