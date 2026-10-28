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
    fw = get_profile(device_type).firmware
    return bool(fw) and (bool(fw.get("method")) or scp_supported(device_type))


def scp_put(conn, local: Path, remote: str) -> None:
    """Copia un archivo al equipo con el protocolo SCP por la sesión SSH ya abierta (airOS y EdgeOS lo tienen;
    no todos tienen SFTP)."""
    ch = conn.remote_conn.get_transport().open_session()
    ch.settimeout(120)
    ch.exec_command(f"scp -t {remote}")

    def ok():
        r = ch.recv(1)
        if r != b"\0":
            raise RuntimeError(f"El equipo rechazó la copia SCP a {remote}: {r + ch.recv(512)!r}")

    ok()
    size = local.stat().st_size
    ch.sendall(f"C0644 {size} {Path(remote).name}\n".encode())
    ok()
    with open(local, "rb") as f:
        while chunk := f.read(65536):
            ch.sendall(chunk)
    ch.sendall(b"\0")
    ok()
    ch.close()


def _routeros(conn, img, channel: str, reload: bool, log: list) -> None:
    """MikroTik: «en línea» (/system package update, descarga de MikroTik) o con el .npk cargado (por SFTP a la raíz;
    RouterOS lo instala al reiniciar)."""
    if img is None:
        log.append(conn.send_command(f"/system package update set channel={channel}", read_timeout=30))
        out = ""
        for _ in range(24):  # la comprobación termina en unos segundos
            out = conn.send_command("/system package update check-for-updates once", read_timeout=60)
            if not re.search(r"(?i)finding out|checking", out.split("status:")[-1]):
                break
        log.append(out)
        low = out.lower()
        if "up to date" in low or "already" in low:
            log.append("Ya tiene la última versión del canal: no se instala nada.")
            return
        if "error" in low or "could not" in low:
            raise RuntimeError("RouterOS no pudo consultar las actualizaciones (¿tiene acceso a internet y DNS?):\n" + out)
        if not reload:
            log.append(conn.send_command("/system package update download", read_timeout=900))
            log.append("Paquetes descargados: se instalarán en el próximo reinicio.")
            return
        try:
            log.append(conn.send_command_timing("/system package update install", read_timeout=900))
        except Exception as e:  # noqa: BLE001 - la sesión se corta al reiniciar
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Descargado e instalándose: RouterOS se reinicia.")
        return
    import paramiko
    sftp = paramiko.SFTPClient.from_transport(conn.remote_conn.get_transport())
    try:
        sftp.put(str(FIRMWARE_DIR / img.filename), img.filename)
        size = sftp.stat(img.filename).st_size
    finally:
        sftp.close()
    if size != img.size:
        raise RuntimeError(f"El archivo llegó incompleto al equipo ({size} de {img.size} bytes); no se reinicia")
    log.append(f"Paquete {img.filename} copiado al equipo ({size} bytes, completo).")
    if reload:
        try:
            log.append(_answer_prompts(conn, "/system reboot"))
        except Exception as e:  # noqa: BLE001
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Reinicio enviado: RouterOS instala el paquete al arrancar.")
    else:
        log.append("Se instalará en el próximo reinicio del equipo.")


def _airos(conn, img, reload: bool, log: list) -> None:
    """airOS (airMAX M y AC) y airFiber: el método de Ubiquiti es copiar la imagen a /tmp/fwupdate.bin y ejecutar
    «fwupdate -m», que la graba y reinicia el equipo."""
    scp_put(conn, FIRMWARE_DIR / img.filename, "/tmp/fwupdate.bin")
    size = conn.send_command("ls -l /tmp/fwupdate.bin", read_timeout=30)
    log.append(f"Imagen copiada a /tmp/fwupdate.bin: {size.strip()}")
    if str(img.size) not in size:
        raise RuntimeError("La imagen no llegó completa a /tmp/fwupdate.bin; no se graba")
    if not reload:
        log.append("Copiada sin grabar: «fwupdate -m» graba la imagen y reinicia, así que solo se hace si se pide reiniciar.")
        return
    try:
        log.append(conn.send_command_timing("fwupdate -m", read_timeout=300))
    except Exception as e:  # noqa: BLE001
        log.append(f"(sesión cerrada al grabar y reiniciar: {e.__class__.__name__})")
    log.append("Grabando la imagen y reiniciando (unos 3–5 minutos). No apague el equipo.")


def _edgeos(conn, img, reload: bool, log: list) -> None:
    """EdgeRouter: copia la imagen .tar a /tmp, «add system image» la instala como imagen de arranque y se reinicia."""
    remote = f"/tmp/{img.filename}"
    scp_put(conn, FIRMWARE_DIR / img.filename, remote)
    out = _answer_prompts(conn, f"add system image {remote}")
    log.append(out)
    if re.search(r"(?i)\b(fail|error|invalid|not a valid)", out):
        raise RuntimeError("EdgeOS no aceptó la imagen:\n" + out)
    if reload:
        try:
            log.append(_answer_prompts(conn, "reboot"))
        except Exception as e:  # noqa: BLE001
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Reinicio enviado: arranca con la imagen nueva.")


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


def upgrade_device(device_id: int, image_id: int, reload: bool, channel: str = "") -> str:
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        img = db.get(FirmwareImage, image_id) if image_id else None
        prof = get_profile(dev.device_type)
        fw = prof.firmware
        method = (fw or {}).get("method")
        if not supports_upgrade(dev.device_type):
            raise RuntimeError(f"Actualización de firmware no soportada para {prof.label}")
        if img is None and method != "routeros":
            raise RuntimeError("Falta la imagen de firmware")
        if img and img.device_type_filter and not re.search(img.device_type_filter, dev.device_type):
            raise RuntimeError(f"La imagen {img.filename} no es para {dev.device_type}")
    log = [f"Imagen {img.filename} ({img.size} bytes, MD5 {img.md5})" + (f" -> {fw['fs']}" if fw.get("fs") else "")
           if img else f"Actualización en línea desde MikroTik (canal {channel or 'stable'})"]
    if method:
        # respaldo antes de tocar el firmware: si algo sale mal, la configuración está guardada
        pre = services.backup_device(device_id, "push", "firmware")
        if pre == "error":
            raise RuntimeError("No se pudo respaldar el equipo antes de actualizar: no se sigue")
        log.append(f"Respaldo previo: {pre}")
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        dev.maintenance_until = now() + timedelta(minutes=60)
        db.commit()
        try:
            with services.connect(dev) as conn:
                if method == "routeros":
                    _routeros(conn, img, channel or "stable", reload, log)
                elif method == "airos":
                    _airos(conn, img, reload, log)
                elif method == "edgeos":
                    _edgeos(conn, img, reload, log)
                else:
                    _scp_boot(conn, img, fw, reload, log)
        except Exception:
            services.end_maintenance(device_id)
            raise
    # Refrescar inventario/configuración: tras el reinicio esperar 15 min (5 en MikroTik), si no, enseguida
    delay = (300 if method == "routeros" else 900) if reload else 1
    t = threading.Timer(delay, lambda: services.pool.submit(services.backup_device, device_id, "push", "firmware"))
    t.daemon = True
    t.start()
    return "\n".join(str(x) for x in log)


def _scp_boot(conn, img, fw, reload: bool, log: list) -> None:
    """Cisco y similares: imagen por SCP (Netmiko, con MD5 verificado), arranque, guardar y reiniciar."""
    from netmiko import file_transfer
    res = file_transfer(conn, source_file=str(FIRMWARE_DIR / img.filename), dest_file=img.filename,
                        file_system=fw["fs"], direction="put", overwrite_file=False, socket_timeout=60.0)
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


def execute_firmware_request(db, cr):
    ids = json.loads(cr.device_ids)
    devices = list(db.scalars(select(Device).where(Device.id.in_(ids))))
    channel = json.loads(cr.variables or "{}").get("channel", "")
    futs = [(d.name, services.pool.submit(upgrade_device, d.id, cr.firmware_id or 0, cr.reload, channel)) for d in devices]
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
