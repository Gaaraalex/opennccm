"""Rendimiento del servidor para el panel: CPU, memoria, disco y su historial de las últimas 24 horas."""
from __future__ import annotations

import os
import time
from collections import deque
from pathlib import Path

import psutil

from .config import DATA_DIR

SAMPLE_SECONDS = 60
HISTORY = deque(maxlen=24 * 60)  # (epoch, cpu %, memoria %) cada minuto durante 24 h
_dir_size_cache: tuple[float, int] = (0.0, 0)


def sample() -> None:
    HISTORY.append((int(time.time()), psutil.cpu_percent(interval=None), psutil.virtual_memory().percent))


def _dir_size(path: Path) -> int:
    global _dir_size_cache
    if time.time() - _dir_size_cache[0] < 600:
        return _dir_size_cache[1]
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    _dir_size_cache = (time.time(), total)
    return total


def snapshot() -> dict:
    mem, swap = psutil.virtual_memory(), psutil.swap_memory()
    disk_path = DATA_DIR if DATA_DIR.exists() else Path.cwd()
    disk = psutil.disk_usage(str(disk_path))
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):  # Windows (solo en desarrollo)
        load = None
    proc = psutil.Process()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "cpus": psutil.cpu_count() or 1,
        "load": load,
        "mem_total": mem.total, "mem_used": mem.total - mem.available, "mem_pct": mem.percent,
        "swap_total": swap.total, "swap_used": swap.used, "swap_pct": swap.percent,
        "disk_total": disk.total, "disk_used": disk.used, "disk_free": disk.free, "disk_pct": disk.percent,
        "disk_path": str(disk_path),
        "data_size": _dir_size(DATA_DIR) if DATA_DIR.exists() else 0,
        "app_rss": proc.memory_info().rss,
        "uptime": int(time.time() - psutil.boot_time()),
        "history": list(HISTORY),
    }


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d} d " if d else "") + f"{h} h {m} min"
