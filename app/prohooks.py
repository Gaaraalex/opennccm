"""Puntos donde un módulo Pro se engancha en el núcleo sin cambiar su código (los deja register() del módulo).

Sin módulos, todo queda en None y OpenNCCM funciona como siempre.

* remote_backup(device_id, trigger, username) -> str | None: «Recolectores por sede». Si el equipo se respalda desde
  el recolector de una sede, el módulo le pasa el pedido y devuelve "queued"; si no, None y el núcleo se conecta
  como siempre. Lo que lee el recolector vuelve por services.store_remote() y se guarda igual que un respaldo de aquí.
* remote_site(device_id) -> str | None: el nombre de la sede si el equipo se respalda desde un recolector (para no
  intentar desde aquí lo que solo se puede hacer con el equipo al alcance, como la reversión automática)."""
from __future__ import annotations

from typing import Callable

remote_backup: Callable[[int, str, str | None], str | None] | None = None
remote_site: Callable[[int], str | None] | None = None


def site_of(device_id: int) -> str | None:
    try:
        return remote_site(device_id) if remote_site is not None else None
    except Exception:  # noqa: BLE001  (un módulo que falla nunca tumba el respaldo)
        return None
