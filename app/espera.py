"""Modo espera de alta disponibilidad: este OpenNCCM se suma como servidor en espera de otro (el activo).

Puerta del núcleo, como app/recolector.py: baja del activo el módulo «alta_disponibilidad» firmado (la clave de alta lo
autoriza), comprueba su firma con las claves del núcleo y le pasa el alta. Lo corre install.sh --espera antes de
arrancar el servicio; después, el servidor corre normal y su rol (activo · espera · aislado) lo maneja el módulo con
prohooks.set_rol().

    python -m app.cli espera alta <dirección del activo> <clave de alta> [<dirección de este servidor>]
"""
from __future__ import annotations

import logging
import sys

from .recolector import RecolectorError, bajar_modulo, modulo

MID = "alta_disponibilidad"


def main(args: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(args) >= 3 and args[0] == "alta":
        try:
            bajar_modulo(args[1], args[2], MID, "/api/ha/paquete")
            mod = modulo(MID, "espera", "El módulo de alta disponibilidad no se pudo instalar.")
            return int(mod.espera("alta", activo=args[1].rstrip("/"), clave=args[2], mi_url=(args[3] if len(args) > 3 else "").rstrip("/")) or 0)
        except RecolectorError as e:
            print(e, file=sys.stderr)
            return 1
    print(__doc__.split("\n\n")[-1].rstrip(), file=sys.stderr)
    return 2
