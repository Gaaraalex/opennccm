"""Fecha, hora y zona horaria del servidor (timedatectl).

El servicio corre sin privilegios: el instalador agrega una regla de polkit que permite al usuario
«opennccm» solo estas tres acciones de systemd-timedated (zona horaria, hora y sincronización NTP).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime

TIMEDATECTL = shutil.which("timedatectl")


class TimeError(RuntimeError):
    pass


def available() -> bool:
    return TIMEDATECTL is not None


def _run(*args: str) -> str:
    if not TIMEDATECTL:
        raise TimeError("timedatectl no está disponible en este sistema")
    r = subprocess.run([TIMEDATECTL, *args], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if "authentication" in msg.lower() or "access denied" in msg.lower() or "not authorized" in msg.lower():
            msg += (" — falta el permiso de polkit; ejecute «sudo opennccm update» o reinstale para aplicarlo")
        raise TimeError(msg or f"timedatectl {' '.join(args)} falló")
    return r.stdout


def info() -> dict:
    data = {"now": datetime.now(), "timezone": time.tzname[0], "ntp": None, "synced": None, "available": available()}
    if available():
        try:
            out = _run("show")
            kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            data["timezone"] = kv.get("Timezone", data["timezone"])
            data["ntp"] = kv.get("NTP") == "yes"
            data["synced"] = kv.get("NTPSynchronized") == "yes"
        except (TimeError, subprocess.TimeoutExpired):
            pass
    return data


def timezones() -> list[str]:
    if available():
        try:
            return [z for z in _run("list-timezones").split() if z]
        except (TimeError, subprocess.TimeoutExpired):
            pass
    try:
        import zoneinfo
        return sorted(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001
        return []


def set_timezone(tz: str):
    if tz not in timezones():
        raise TimeError(f"Zona horaria desconocida: {tz}")
    _run("set-timezone", tz)


def set_ntp(enabled: bool):
    _run("set-ntp", "true" if enabled else "false")


def set_time(date_str: str, time_str: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str or "") or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", time_str or ""):
        raise TimeError("Fecha u hora no válidas (use AAAA-MM-DD y HH:MM)")
    datetime.strptime(f"{date_str} {time_str[:5]}", "%Y-%m-%d %H:%M")  # valida valores
    _run("set-ntp", "false")  # no se puede fijar la hora a mano con NTP activo
    _run("set-time", f"{date_str} {time_str if len(time_str) == 8 else time_str + ':00'}")


def restart_soon():
    """Reinicia el servicio para que la aplicación y el planificador tomen la nueva zona horaria.

    Bajo systemd (Restart=on-failure) basta con terminar con un código de error; fuera de systemd
    (modo desarrollo) solo se recarga la zona horaria del proceso.
    """
    if os.environ.get("INVOCATION_ID"):
        threading.Timer(1.5, lambda: os._exit(3)).start()
    elif hasattr(time, "tzset"):
        time.tzset()
