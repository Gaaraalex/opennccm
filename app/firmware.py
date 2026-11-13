"""Actualización remota de firmware: repositorio de imágenes, transferencia SCP, arranque y reinicio.

Para muchos equipos a la vez (una solicitud con varios):
- antes de copiar se mira el espacio libre donde va la imagen («dir flash:», «free-hdd-space», «df -k /tmp») y, si no
  alcanza, ese equipo no se toca;
- después de copiar se compara la suma calculada en el propio equipo (MD5 de Cisco/Junos con Netmiko; SHA-256 o MD5 en
  airOS y EdgeOS) con la de la imagen; si no coincide no se instala. RouterOS no calcula sumas: se compara el tamaño;
- los equipos van en tandas («de a N»); con reinicio, la tanda siguiente espera a que los de la anterior vuelvan con la
  versión nueva, y si uno falla las siguientes no empiezan (salvo que se pida seguir);
- cada equipo deja su avance (paso, porcentaje de la copia y resultado) en firmware_jobs, que la solicitud muestra al vivo;
- «Comprobar los equipos» hace esas mismas comprobaciones (conexión, versión, espacio) sin copiar nada.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select

from . import services
from .config import DATA_DIR, WORKERS
from .db import Device, FirmwareImage, FirmwareJob, SessionLocal, now
from .drivers import get_profile, scp_supported
from .i18n import _

FIRMWARE_DIR = DATA_DIR / "firmware"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]{1,200}$")
FINAL = ("done", "failed", "skipped", "stopped")
BATCH_SIZES = (1, 2, 5, 10, 20)


class Progress:
    """Escribe el avance de un equipo en firmware_jobs. Nunca hace fallar la actualización; la copia escribe como mucho
    cada 1,5 s."""

    def __init__(self, job_id: int | None):
        self.id, self._t = job_id, 0.0

    def __call__(self, stage: str | None = None, pct: int | None = None, msg: str | None = None):
        if not self.id:
            return
        if stage is None and pct is not None and pct < 100 and time.monotonic() - self._t < 1.5:
            return
        self._t = time.monotonic()
        try:
            with SessionLocal() as db:
                j = db.get(FirmwareJob, self.id)
                if j is None:
                    return
                if stage:
                    j.stage, j.pct = stage, pct
                elif pct is not None:
                    j.pct = pct
                if msg is not None:
                    j.message = msg[:4000]
                if j.started_at is None and (stage or "queued") != "queued":
                    j.started_at = now()
                if stage in FINAL:
                    j.finished_at = now()
                db.commit()
        except Exception:  # noqa: BLE001 - el avance nunca hace fallar la actualización
            pass


def _noop(*_a, **_k):
    return None


def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def need_bytes(size: int) -> int:
    """Lo que tiene que haber libre: la imagen y un margen (1 MB o el 2 %)."""
    return size + max(1 << 20, size // 50)


_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20,
          "g": 1 << 30, "gb": 1 << 30, "gib": 1 << 30}


def _df_free(out: str) -> int | None:
    """Lo disponible (en bytes) de «df -k»: el menor de las filas que lo digan (la línea puede venir partida)."""
    toks = (out or "").split()
    best = None
    for i in range(len(toks) - 3):
        a, b, c, d = toks[i:i + 4]
        if a.isdigit() and b.isdigit() and c.isdigit() and re.match(r"^\d+%$", d):
            v = int(c) * 1024
            best = v if best is None else min(best, v)
    return best


def free_space(conn, method: str | None, fw: dict, img) -> tuple[int | None, str]:
    """(bytes libres donde va la imagen, de dónde salió). (None, motivo) si el equipo no lo dice."""
    try:
        if method == "routeros":
            out = conn.send_command("/system resource print", read_timeout=30)
            m = re.search(r"free-hdd-space:\s*([\d.]+)\s*([KMG]i?B)?", out)
            return (int(float(m.group(1)) * _UNITS[(m.group(2) or "").lower()]), "free-hdd-space") if m else (None, "")
        if method in ("airos", "edgeos"):
            out = conn.send_command("df -k /tmp /root.dev" if method == "edgeos" else "df -k /tmp", read_timeout=30)
            return _df_free(out), "df -k"
        from netmiko import FileTransfer
        ft = FileTransfer(conn, source_file=str(FIRMWARE_DIR / img.filename), dest_file=img.filename,
                          file_system=fw["fs"], direction="put", hash_supported=False)
        return ft.remote_space_available(), f"dir {fw['fs']}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:200]


def device_hash(conn, path: str, img) -> tuple[str, bool | None]:
    """Suma del archivo calculada en el propio equipo (Linux: airOS, EdgeOS): («SHA-256», coincide) o («», None) si el
    equipo no tiene sha256sum ni md5sum."""
    for cmd, want, label in (("sha256sum", img.sha256, "SHA-256"), ("md5sum", img.md5, "MD5")):
        if not want:
            continue
        try:
            out = conn.send_command(f"{cmd} {path}", read_timeout=300)
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"\b([0-9a-fA-F]{%d})\b" % len(want), out or "")
        if m:
            return label, m.group(1).lower() == want.lower()
    return "", None


def ensure_sha256(image_id: int) -> None:
    """Las imágenes cargadas antes de 2.41 no tienen SHA-256: se calcula la primera vez que se usan."""
    with SessionLocal() as db:
        img = db.get(FirmwareImage, image_id)
        if img is None or img.sha256:
            return
        path = FIRMWARE_DIR / img.filename
        if not path.exists():
            return
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        img.sha256 = h.hexdigest()
        db.commit()


def supports_upgrade(device_type: str) -> bool:
    fw = get_profile(device_type).firmware
    return bool(fw) and (bool(fw.get("method")) or scp_supported(device_type))


def scp_put(conn, local: Path, remote: str, progress=_noop) -> None:
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
    sent = 0
    with open(local, "rb") as f:
        while chunk := f.read(65536):
            ch.sendall(chunk)
            sent += len(chunk)
            progress(None, int(sent * 100 / size) if size else 100)
    ch.sendall(b"\0")
    ok()
    ch.close()


def _space(free: int | None, src: str, img, log: list, progress) -> None:
    """Se para antes de copiar si no hay lugar; si el equipo no dice cuánto tiene, se sigue y la copia lo comprueba."""
    progress("space")
    if free is None:
        log.append("Espacio libre: el equipo no lo informó" + (f" ({src})" if src else "") +
                   "; se sigue y se comprueba la copia.")
        return
    need = need_bytes(img.size)
    log.append(f"Espacio libre: {human(free)} ({src}); la imagen necesita {human(need)}.")
    if free < need:
        raise RuntimeError(f"No hay espacio para la imagen: libre {human(free)}, necesita {human(need)}. "
                           "Borre imágenes viejas del equipo y vuelva a intentarlo; no se copió nada.")


def _routeros(conn, img, channel: str, reload: bool, log: list, progress=_noop) -> None:
    """MikroTik: «en línea» (/system package update, descarga de MikroTik) o con el .npk cargado (por SFTP a la raíz;
    RouterOS lo instala al reiniciar)."""
    if img is None:
        log.append(conn.send_command(f"/system package update set channel={channel}", read_timeout=30))
        out = ""
        for _i in range(24):  # la comprobación termina en unos segundos
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
    free, src = free_space(conn, "routeros", {}, img)
    _space(free, src, img, log, progress)
    import paramiko
    progress("copy", 0)
    sftp = paramiko.SFTPClient.from_transport(conn.remote_conn.get_transport())
    try:
        sftp.put(str(FIRMWARE_DIR / img.filename), img.filename,
                 callback=lambda done, total: progress(None, int(done * 100 / total) if total else 100))
        size = sftp.stat(img.filename).st_size
    finally:
        sftp.close()
    if size != img.size:
        raise RuntimeError(f"El archivo llegó incompleto al equipo ({size} de {img.size} bytes); no se reinicia")
    log.append(f"Paquete {img.filename} copiado al equipo ({size} bytes, completo). RouterOS no calcula sumas de "
               "archivos: se comprobó el tamaño.")
    progress("verify", 100)
    if reload:
        progress("reboot")
        try:
            log.append(_answer_prompts(conn, "/system reboot"))
        except Exception as e:  # noqa: BLE001
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Reinicio enviado: RouterOS instala el paquete al arrancar.")
    else:
        log.append("Se instalará en el próximo reinicio del equipo.")


def _airos(conn, img, reload: bool, log: list, progress=_noop) -> None:
    """airOS (airMAX M y AC) y airFiber: el método de Ubiquiti es copiar la imagen a /tmp/fwupdate.bin y ejecutar
    «fwupdate -m», que la graba y reinicia el equipo."""
    free, src = free_space(conn, "airos", {}, img)
    _space(free, src, img, log, progress)
    progress("copy", 0)
    scp_put(conn, FIRMWARE_DIR / img.filename, "/tmp/fwupdate.bin", progress)
    size = conn.send_command("ls -l /tmp/fwupdate.bin", read_timeout=30)
    log.append(f"Imagen copiada a /tmp/fwupdate.bin: {size.strip()}")
    if str(img.size) not in size:
        raise RuntimeError("La imagen no llegó completa a /tmp/fwupdate.bin; no se graba")
    _check_hash(conn, "/tmp/fwupdate.bin", img, log, progress)
    if not reload:
        log.append("Copiada sin grabar: «fwupdate -m» graba la imagen y reinicia, así que solo se hace si se pide reiniciar.")
        return
    progress("install")
    try:
        log.append(conn.send_command_timing("fwupdate -m", read_timeout=300))
    except Exception as e:  # noqa: BLE001
        log.append(f"(sesión cerrada al grabar y reiniciar: {e.__class__.__name__})")
    log.append("Grabando la imagen y reiniciando (unos 3–5 minutos). No apague el equipo.")


def _check_hash(conn, path: str, img, log: list, progress) -> None:
    progress("verify", 100)
    label, ok = device_hash(conn, path, img)
    if ok is None:
        log.append("El equipo no calcula sumas (sin sha256sum ni md5sum): se comprobó el tamaño.")
    elif not ok:
        raise RuntimeError(f"La suma {label} calculada en el equipo no coincide con la de la imagen: llegó dañada. "
                           "No se instala.")
    else:
        log.append(f"Suma {label} calculada en el equipo: coincide con la imagen.")


def _edgeos(conn, img, reload: bool, log: list, progress=_noop) -> None:
    """EdgeRouter: copia la imagen .tar a /tmp, «add system image» la instala como imagen de arranque y se reinicia."""
    remote = f"/tmp/{img.filename}"
    free, src = free_space(conn, "edgeos", {}, img)
    _space(free, src, img, log, progress)
    progress("copy", 0)
    scp_put(conn, FIRMWARE_DIR / img.filename, remote, progress)
    _check_hash(conn, remote, img, log, progress)
    progress("install")
    out = _answer_prompts(conn, f"add system image {remote}")
    log.append(out)
    if re.search(r"(?i)\b(fail|error|invalid|not a valid)", out):
        raise RuntimeError("EdgeOS no aceptó la imagen:\n" + out)
    if reload:
        progress("reboot")
        try:
            log.append(_answer_prompts(conn, "reboot"))
        except Exception as e:  # noqa: BLE001
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Reinicio enviado: arranca con la imagen nueva.")


def save_image(filename: str, stream, username: str, version: str | None, flt: str | None,
               vendor_sum: str | None = None) -> FirmwareImage:
    """Guarda la imagen y calcula su MD5 y su SHA-256. Con la suma que publica el fabricante (MD5 o SHA-256), la compara
    y, si no coincide, no la guarda (ni reemplaza la que hubiera con ese nombre)."""
    if not SAFE_NAME.match(filename):
        raise ValueError("Nombre de archivo no válido (use letras, números, punto, guion)")
    want = re.sub(r"\s+", "", vendor_sum or "").lower() or None
    if want and not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{64}", want):
        raise ValueError("La suma del fabricante debe ser un MD5 (32 caracteres) o un SHA-256 (64)")
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIRMWARE_DIR / filename
    tmp = FIRMWARE_DIR / f".{filename}.subiendo"
    md5, sha, size = hashlib.md5(), hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as f:
            while chunk := stream.read(1024 * 1024):
                f.write(chunk)
                md5.update(chunk)
                sha.update(chunk)
                size += len(chunk)
        if want and want not in (md5.hexdigest(), sha.hexdigest()):
            raise ValueError("El archivo no coincide con la suma publicada por el fabricante: está dañado o no es el "
                             "original. No se guardó.")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    with SessionLocal() as db:
        img = db.scalars(select(FirmwareImage).where(FirmwareImage.filename == filename)).first() \
            or FirmwareImage(filename=filename)
        img.size, img.md5, img.sha256, img.version = size, md5.hexdigest(), sha.hexdigest(), version or None
        img.vendor_sum = want
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
    for _i in range(5):
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


def _target_check(dev, img, method):
    """(motivo para no tocar el equipo, o None): incompatible o ya con esa versión."""
    if not supports_upgrade(dev.device_type):
        return f"Actualización de firmware no soportada para {get_profile(dev.device_type).label}"
    if img is None and method != "routeros":
        return "Falta la imagen de firmware"
    if img and img.device_type_filter and not re.search(img.device_type_filter, dev.device_type):
        return f"La imagen {img.filename} no es para {dev.device_type}"
    return None


class Skipped(Exception):
    """El equipo ya tiene la versión de la imagen: no se toca (no es un error)."""


def upgrade_device(device_id: int, image_id: int, reload: bool, channel: str = "", cr_id: int | None = None,
                   progress=None, wait: bool = False) -> str:
    """Actualiza un equipo. Con wait=True (tandas con reinicio) espera a que vuelva y comprueba la versión antes de
    devolver; si no volvió o no quedó en la nueva, falla. Si no, lo comprueba en segundo plano (app/fwverify.py)."""
    progress = progress or _noop
    from .fwverify import same_version
    if image_id:
        ensure_sha256(image_id)
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        before = dev.os_version
        img = db.get(FirmwareImage, image_id) if image_id else None
        target = img.version if img else None
        prof = get_profile(dev.device_type)
        fw = prof.firmware
        method = (fw or {}).get("method")
        why = _target_check(dev, img, method)
        if why:
            raise RuntimeError(why)
        if target and before and same_version(before, target):
            progress("skipped", None, f"Ya tiene la versión {before}: no se toca.")
            raise Skipped(f"Ya tiene la versión {before}: no se toca.")
        db.expunge(img) if img else None
    log = [f"Imagen {img.filename} ({img.size} bytes, MD5 {img.md5})" + (f" -> {fw['fs']}" if fw.get("fs") else "")
           if img else f"Actualización en línea desde MikroTik (canal {channel or 'stable'})"]
    if method:
        # respaldo antes de tocar el firmware: si algo sale mal, la configuración está guardada
        progress("backup")
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
                    _routeros(conn, img, channel or "stable", reload, log, progress)
                elif method == "airos":
                    _airos(conn, img, reload, log, progress)
                elif method == "edgeos":
                    _edgeos(conn, img, reload, log, progress)
                else:
                    _scp_boot(conn, img, fw, reload, log, progress)
        except Exception:
            services.end_maintenance(device_id)
            raise
    # Comprobar la versión: cuando el equipo vuelve (si reinicia) se lee su versión, se respalda y se dice si quedó en la
    # nueva; si no, el final de su registro va a Eventos y a la solicitud de cambio (app/fwverify.py)
    from . import fwverify
    if wait:
        log.append("Esperando a que el equipo vuelva para comprobar la versión antes de seguir con la tanda siguiente.")
        res = fwverify.verify_after_upgrade(device_id, before, target, reload, cr_id, progress=progress)
        if not res.get("ok") or (reload and not res.get("applied")):
            raise RuntimeError("\n".join(str(x) for x in log) + "\n" +
                               ("No volvió a responder después de actualizar." if not res.get("ok")
                                else f"No quedó en la versión nueva (tiene {res.get('version') or '?'})."))
        log.append(f"Volvió con la versión {res.get('version')}.")
        return "\n".join(str(x) for x in log)
    fwverify.start_verify(device_id, before, target, reload, cr_id, progress=progress)
    log.append("OpenNCCM comprobará la versión instalada cuando el equipo responda" + (" después del reinicio." if reload else "."))
    return "\n".join(str(x) for x in log)


def _already_there(conn, fw, img) -> bool:
    """La imagen ya está en el equipo con su tamaño (un intento anterior): no necesita espacio nuevo."""
    fs = fw.get("fs") or ""
    try:
        out = conn.send_command(f"dir {fs}{'' if fs.endswith((':', '/')) else '/'}{img.filename}", read_timeout=30)
    except Exception:  # noqa: BLE001
        return False
    return bool(re.search(rf"\s{re.escape(str(img.size))}\s.*{re.escape(img.filename)}", out or ""))


def _scp_boot(conn, img, fw, reload: bool, log: list, progress=_noop) -> None:
    """Cisco y similares: imagen por SCP (Netmiko, con MD5 verificado), arranque, guardar y reiniciar."""
    from netmiko import file_transfer
    free, src = free_space(conn, None, fw, img)
    if _already_there(conn, fw, img):
        progress("space")
        log.append(f"La imagen ya está en {fw['fs']}: no hace falta espacio nuevo.")
    else:
        _space(free, src, img, log, progress)
    progress("copy", 0)
    res = file_transfer(conn, source_file=str(FIRMWARE_DIR / img.filename), dest_file=img.filename,
                        file_system=fw["fs"], direction="put", overwrite_file=False, socket_timeout=60.0,
                        progress=lambda _f, size, sent: progress(None, int(sent * 100 / size) if size else 100))
    progress("verify", 100)
    log.append(f"Transferencia: ya existía={res['file_exists']}, transferida={res['file_transferred']}, "
               f"MD5 verificado={res['file_verified']}")
    if not res["file_verified"]:
        raise RuntimeError("La verificación MD5 en el equipo falló; no se cambia el arranque")
    log.append("Suma MD5 calculada en el equipo: coincide con la imagen.")
    boot = [c.format(file=img.filename) for c in fw.get("boot", [])]
    if boot or fw.get("exec"):
        progress("install")
    if boot:
        out = conn.send_config_set(boot, cmd_verify=False)
        if re.search(r"(?im)^\s*(% ?Invalid|error)", out):
            raise RuntimeError("El equipo rechazó los comandos de arranque:\n" + out)
        log.append(out)
        # (el primer «write memory» tras cambiar de imagen pide confirmar: services.save_config lo contesta)
        from .services import save_config
        log.append(save_config(conn, get_profile(getattr(conn, "device_type", "") or "")))
    for c in fw.get("exec", []):
        log.append(conn.send_command(c.format(file=img.filename), read_timeout=3600))
    if reload:
        progress("reboot")
        try:
            log.append(_answer_prompts(conn, fw["reload"]))
        except Exception as e:  # noqa: BLE001 - la sesión se corta al reiniciar
            log.append(f"(sesión cerrada por el reinicio: {e.__class__.__name__})")
        log.append("Reinicio enviado.")


def plan_batches(cr, device_ids: list[int]) -> list[list[int]]:
    """Tandas: con reinicio, el orden sugerido por el mapa (app/impact.py: nunca dos que juntos aíslan más); cada grupo de
    ese orden se parte en tandas del tamaño pedido («batch»; 0 = todos los del grupo a la vez)."""
    opts = json.loads(cr.variables or "{}")
    size = int(opts.get("batch") or 0)
    waves = [list(device_ids)]
    if cr.reload and len(device_ids) > 1:
        from . import impact
        try:
            waves = impact.waves(impact._graph(), list(device_ids))
        except Exception:  # noqa: BLE001 - sin mapa: de a uno, que es lo más prudente
            waves = [[i] for i in device_ids]
    out = []
    for w in waves:
        step = size if size > 0 else len(w)
        out += [w[i:i + step] for i in range(0, len(w), step)]
    return [b for b in out if b]


def execute_firmware_request(db, cr):
    ids = json.loads(cr.device_ids)
    devices = list(db.scalars(select(Device).where(Device.id.in_(ids))))
    opts = json.loads(cr.variables or "{}")
    channel = opts.get("channel", "")
    stop = opts.get("stop", True)
    by_id = {d.id: d for d in devices}
    batches = plan_batches(cr, [i for i in ids if i in by_id])
    db.execute(delete(FirmwareJob).where(FirmwareJob.cr_id == cr.id, FirmwareJob.kind == "run"))
    job = {}
    for n, b in enumerate(batches, 1):
        for i in b:
            j = FirmwareJob(cr_id=cr.id, kind="run", device_id=i, device_name=by_id[i].name, batch=n, stage="queued")
            db.add(j)
            db.flush()
            job[i] = j.id
    db.commit()
    report, ok, fail, skipped, halted = [], 0, 0, 0, False
    for n, batch in enumerate(batches, 1):
        tag = f"{n}/{len(batches)} · " if len(batches) > 1 else ""
        if halted:
            for i in batch:
                Progress(job[i])("stopped", None, "No se hizo: una tanda anterior falló.")
                report.append(f"===== {tag}{by_id[i].name}: NO SE HIZO\nUna tanda anterior falló.")
            continue
        # con reinicio y más tandas detrás, se espera a que estos equipos vuelvan con la versión nueva
        wait = cr.reload and n < len(batches)
        with ThreadPoolExecutor(max_workers=max(1, min(len(batch), WORKERS)), thread_name_prefix="ncm-fw") as ex:
            futs = [(i, ex.submit(upgrade_device, i, cr.firmware_id or 0, cr.reload, channel, cr.id, Progress(job[i]), wait))
                    for i in batch]
            bad = 0
            for i, f in futs:
                name = by_id[i].name
                try:
                    report.append(f"===== {tag}{name}: OK\n{f.result()}")
                    ok += 1
                    if wait:
                        Progress(job[i])("done", None, "Actualizado y comprobado.")
                except Skipped as e:
                    report.append(f"===== {tag}{name}: SIN CAMBIOS\n{e}")
                    skipped += 1
                except Exception as e:  # noqa: BLE001
                    Progress(job[i])("failed", None, str(e) or type(e).__name__)
                    report.append(f"===== {tag}{name}: ERROR\n{e}")
                    fail += 1
                    bad += 1
        if bad and stop and n < len(batches):
            halted = True
            report.append(f"Se detuvo después de la tanda {n}: {bad} equipo(s) fallaron y las tandas siguientes no empezaron.")
    cr.status = "done" if fail == 0 and not halted else ("failed" if ok == 0 and skipped == 0 else "partial")
    cr.result, cr.executed_at = "\n\n".join(report), now()
    db.commit()
    extra = (f", {skipped} ya tenían la versión" if skipped else "") + (", detenida por un error" if halted else "")
    services.add_event("info" if fail == 0 else "major", "Actualización de firmware",
                       f"#{cr.id} '{cr.title}': {ok} OK, {fail} con error{extra}.")


# --------------------------------------------------------------------------- comprobación previa (sin tocar nada)
def check_device(device_id: int, image_id: int, progress=None) -> tuple[bool, str]:
    """Se conecta y mira, sin copiar ni cambiar nada: si responde, su versión y si hay espacio para la imagen."""
    progress = progress or _noop
    from .fwverify import same_version
    with SessionLocal() as db:
        dev = db.get(Device, device_id)
        img = db.get(FirmwareImage, image_id) if image_id else None
        fw = get_profile(dev.device_type).firmware or {}
        method = fw.get("method")
        why = _target_check(dev, img, method)
        if why:
            return False, why
        if img and dev.os_version and img.version and same_version(dev.os_version, img.version):
            return True, f"Ya tiene la versión {dev.os_version}: no se tocará."
        if img:
            db.expunge(img)
        progress("space")
        try:
            with services.connect(dev) as conn:
                if img is None:  # RouterOS en línea: no hay imagen que copiar
                    return True, f"Responde (versión {dev.os_version or '?'}). Descarga de MikroTik: el espacio lo mira RouterOS."
                free, src = free_space(conn, method, fw, img)
                if not method and _already_there(conn, fw, img):
                    return True, f"Versión {dev.os_version or '?'} → {img.version or img.filename}. La imagen ya está en el equipo."
        except Exception as e:  # noqa: BLE001
            return False, f"No se pudo conectar: {e or type(e).__name__}"
    head = f"Versión {dev.os_version or '?'} → {img.version or img.filename}. "
    need = need_bytes(img.size)
    if free is None:
        return True, head + "No informa su espacio libre: se comprobará al copiar."
    if free < need:
        return False, head + f"Sin espacio: libre {human(free)}, necesita {human(need)}."
    return True, head + f"Espacio libre {human(free)} (necesita {human(need)})."


def check_request(cr_id: int) -> None:
    """«Comprobar los equipos» de una solicitud de firmware: una fila por equipo (kind="check"), de a WORKERS a la vez."""
    with SessionLocal() as db:
        from .db import ChangeRequest
        cr = db.get(ChangeRequest, cr_id)
        ids = json.loads(cr.device_ids)
        image_id = cr.firmware_id or 0
        devs = {d.id: d.name for d in db.scalars(select(Device).where(Device.id.in_(ids)))}
        db.execute(delete(FirmwareJob).where(FirmwareJob.cr_id == cr_id, FirmwareJob.kind == "check"))
        job = {}
        for i in ids:
            if i in devs:
                j = FirmwareJob(cr_id=cr_id, kind="check", device_id=i, device_name=devs[i], stage="queued")
                db.add(j)
                db.flush()
                job[i] = j.id
        db.commit()
    if image_id:
        ensure_sha256(image_id)

    def one(i):
        p = Progress(job[i])
        try:
            good, msg = check_device(i, image_id, p)
        except Exception as e:  # noqa: BLE001
            good, msg = False, str(e) or type(e).__name__
        p("done" if good else "failed", None, msg)

    with ThreadPoolExecutor(max_workers=max(1, min(len(job), WORKERS)), thread_name_prefix="ncm-fwchk") as ex:
        list(ex.map(one, list(job)))


def jobs_of(cr_id: int, kind: str = "run") -> list[dict]:
    """Avance de una solicitud para la página (y su JSON)."""
    with SessionLocal() as db:
        rows = db.scalars(select(FirmwareJob).where(FirmwareJob.cr_id == cr_id, FirmwareJob.kind == kind)
                          .order_by(FirmwareJob.batch, FirmwareJob.id)).all()
        return [{"id": j.id, "device_id": j.device_id, "name": j.device_name, "batch": j.batch, "stage": j.stage,
                 "pct": j.pct, "msg": j.message or "", "final": j.stage in FINAL,
                 "secs": int(((j.finished_at or now()) - j.started_at).total_seconds()) if j.started_at else None}
                for j in rows]


def busy(status: str, run: list[dict], check: list[dict]) -> bool:
    """¿Sigue algo en curso? (una fila sin terminar de hace más de 2 h es de un reinicio del servidor: no cuenta)."""
    return status in ("approved", "running") or any(not j["final"] and j["secs"] is not None and j["secs"] < 7200
                                                    for j in run + check)


def page_info(cr) -> dict:
    """Lo que muestra la solicitud de firmware: tandas pedidas, avance y comprobación previa."""
    opts = json.loads(cr.variables or "{}")
    n = len(json.loads(cr.device_ids or "[]"))
    if n <= 1:
        plan = ""
    elif opts.get("batch"):
        plan = _("De a {0} equipos").format(opts["batch"])
    else:
        plan = _("Todos a la vez") if "batch" in opts else ""
    if plan and cr.reload:
        plan += " · " + _("cada tanda espera a que la anterior vuelva con la versión nueva")
    if plan and opts.get("stop", True):
        plan += " · " + _("si uno falla, las siguientes no empiezan")
    run, chk = jobs_of(cr.id, "run"), jobs_of(cr.id, "check")
    return {"plan": plan, "run": run, "check": chk, "busy": busy(cr.status, run, chk),
            "checking": busy("", [], chk)}


def list_images() -> list[FirmwareImage]:
    with SessionLocal() as db:
        return list(db.scalars(select(FirmwareImage).order_by(FirmwareImage.uploaded_at.desc())))
