"""Actualización remota de firmware: repositorio de imágenes, transferencia SCP, arranque y reinicio."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from . import services
from .config import DATA_DIR
from .db import Device, FirmwareImage, SessionLocal, now
from .drivers import get_profile, scp_supported

FIRMWARE_DIR = DATA_DIR / "firmware"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]{1,200}$")


def supports_upgrade(device_type: str) -> bool:
    return bool(get_profile(device_type).firmware) and scp_supported(device_type)


def save_image(filename: str, stream, username: str, version: str | None, flt: str | None) -> FirmwareImage:
    if not SAFE_NAME.match(filename):
        raise ValueError("Nombre de archivo no válido (use letras, números, punto, guion)")
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIRMWARE_DIR / filename
    md5, size = hashlib.md5(), 0
    with open(path, "wb") as f:
        while chunk := stream.read(1024 * 1024):
            f.write(chunk)
            md5.update(chunk)
            size += len(chunk)
    with SessionLocal() as db:
        img = db.scalars(select(FirmwareImage).where(FirmwareImage.filename == filename)).first() \
            or FirmwareImage(filename=filename)
        img.size, img.md5, img.version = size, md5.hexdigest(), version or None
        img.device_type_filter, img.uploaded_by, img.uploaded_at = flt or None, username, now()
        db.add(img)
        db.commit()
        return img


def delete_image(image_id: int):
    with SessionLocal() as db:
        img = db.get(FirmwareImage, image_id)
        if img:
            (FIRMWARE_DIR / img.filename).unlink(missing_ok=True)
            db.delete(img)
            db.commit()


def _answer_prompts(conn, cmd: str) -> str:
    """Envía el comando de reinicio respondiendo las confirmaciones habituales."""
    out = conn.send_command_timing(cmd, read_timeout=30)
    full = out
    for _ in range(5):
        low = out.lower()
        if "yes/no" in low or "[yes,no]" in low:
            out = conn.send_command_timing("yes", read_timeout=30)
        elif "[y/n]" in low:
            out = conn.send_command_timing("y", read_timeout=30)
        elif "confirm" in low or "proceed" in low:
            out = conn.send_command_timing("\n", read_timeout=30)
        else:
            break
        full += out
    return full


def upgrade_device(device_id: int, image_id: int, reload: bool) -> str:
    from netmiko import file_transfer

    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        img = db.get(FirmwareImage, image_id)
        prof = get_profile(dev.device_type)
        fw = prof.firmware
        if not supports_upgrade(dev.device_type):
            raise RuntimeError(f"Actualización de firmware no soportada para {prof.label}")
        if img.device_type_filter and not re.search(img.device_type_filter, dev.device_type):
            raise RuntimeError(f"La imagen {img.filename} no es para {dev.device_type}")
        dev.maintenance_until = now() + timedelta(minutes=60)
        db.commit()
        log = [f"Imagen {img.filename} ({img.size} bytes, MD5 {img.md5}) -> {fw['fs']}"]
        try:
            with services.connect(dev) as conn:
                res = file_transfer(conn, source_file=str(FIRMWARE_DIR / img.filename), dest_file=img.filename,
                                    file_system=fw["fs"], direction="put", overwrite_file=False,
                                    socket_timeout=60.0)
                log.append(f"Transferencia: ya existía={res['file_exists']}, transferida={res['file_transferred']}, "
                           f"MD5 verificado={res['file_verified']}")
                if not res["file_verified"]:
                    raise RuntimeError("La verificación MD5 en el equipo falló; no se cambia el arranque")
                boot = [c.format(file=img.filename) for c in fw.get("boot", [])]
                if boot:
                    out = conn.send_config_set(boot, cmd_verify=False)
                    if re.search(r"(?im)^\s*(% ?Invalid|error)", out):
                        raise RuntimeError("El equipo rechazó los comandos de arranque:\n" + out)
                    log.append(out)
                    try:
                        log.append(conn.save_config())
                    except (NotImplementedError, AttributeError):
                        pass
                for c in fw.get("exec", []):
                    log.append(conn.send_command(c.format(file=img.filename), read_timeout=3600))
                if reload:
                    try:
                        log.append(_answer_prompts(conn, fw["reload"]))
                    except Exception as e:  # noqa: BLE001 - la sesión se corta al reiniciar
                        log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
                    log.append("Reinicio enviado. El inventario se actualizará con el próximo respaldo.")
        except Exception:
            services.end_maintenance(device_id)
            raise
    # Refrescar inventario/configuración: tras el reinicio esperar 15 min, si no, enseguida
    delay = 900 if reload else 1
    t = threading.Timer(delay, lambda: services.pool.submit(services.backup_device, device_id, "push", "firmware"))
    t.daemon = True
    t.start()
    return "\n".join(str(x) for x in log)


def execute_firmware_request(db, cr):
    ids = json.loads(cr.device_ids)
    devices = list(db.scalars(select(Device).where(Device.id.in_(ids))))
    futs = [(d.name, services.pool.submit(upgrade_device, d.id, cr.firmware_id, cr.reload)) for d in devices]
    report, ok, fail = [], 0, 0
    for name, f in futs:
        try:
            report.append(f"===== {name}: OK\n{f.result()}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            report.append(f"===== {name}: ERROR\n{e}")
            fail += 1
    cr.status = "done" if fail == 0 else ("failed" if ok == 0 else "partial")
    cr.result, cr.executed_at = "\n\n".join(report), now()
    db.commit()
    services.add_event("info" if fail == 0 else "major", "Actualización de firmware",
                       f"#{cr.id} '{cr.title}': {ok} OK, {fail} con error.")


def list_images() -> list[FirmwareImage]:
    with SessionLocal() as db:
        return list(db.scalars(select(FirmwareImage).order_by(FirmwareImage.uploaded_at.desc())))


def image_path(img: FirmwareImage) -> Path:
    return FIRMWARE_DIR / img.filename
