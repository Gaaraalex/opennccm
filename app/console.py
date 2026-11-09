"""Consola SSH web: terminal en el navegador conectada al equipo por WebSocket.

Dos modos de hablar con el navegador:
  - v=2 (la consola de OpenNCCM): el navegador manda líneas de comando y recibe la salida en mensajes JSON; el servidor
    aplica las reglas (solo lectura, comandos prohibidos del grupo, aviso de comandos que cortan el servicio), graba la
    sesión, la comparte en solo lectura y, si un comando cambia la configuración, pone un respaldo en cola;
  - sin v (terminal clásico xterm.js, compatibilidad): pulsaciones y texto tal cual.

Seguridad:
  - quién puede abrirla y cómo (sin consola, solo lectura, lectura y escritura) se decide por rol y por grupo de equipos
    en Operar › Consola › Acceso; un grupo puede exigir aprobación previa (se pide con motivo y plazo);
  - verificación de origen para que otro sitio no pueda abrirla;
  - sin «inicio automático» se piden usuario y contraseña del equipo; con él se usa la credencial asignada;
  - cierre por inactividad (15 min por omisión) y duración máxima (4 h); cada sesión queda en Auditoría.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import urlparse

import paramiko
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from . import consolecore as cc
from . import legacy_ssh
from .auth import ROLES, Forbidden, check_device, current_user, device_scope, require
from .config import DATA_DIR
from .crypto import decrypt
from .db import ConfigVersion, ConsoleAccessRequest, ConsoleSession, Device, SessionLocal, audit, now
from .drivers import FASTPATH, IOS, PROFILE_MAP, get_profile
from .i18n import _, _f
from .web import flash, go, render

log = logging.getLogger("opennccm")
router = APIRouter()
MAX_SECONDS = 4 * 3600
REC_DIR = DATA_DIR / "console" / "rec"
MODES = ("none", "ro", "rw")


# --------------------------------------------------------------------------- acceso
def _setting(key: str, default: str) -> str:
    from .auth import cached_setting
    return cached_setting(key, default)


def console_mode() -> str:
    """Ajuste antiguo (2.13 y anteriores): off | admin | operator. Sirve de valor por omisión de los ajustes por rol."""
    return _setting("console_access", "admin")


def role_mode(role: str) -> str:
    """none | ro | rw para un rol (Operar › Consola › Acceso)."""
    v = _setting(f"console_role_{role}", "")
    if v in MODES:
        return v
    old = console_mode()
    if old == "off":
        return "none"
    if role == "admin":
        return "rw"
    if role == "operator" and old == "operator":
        return "rw"
    return "none"


def group_rules() -> dict:
    try:
        return json.loads(_setting("console_groups", "") or "{}")
    except ValueError:
        return {}


def idle_seconds() -> int:
    v = _setting("console_idle_minutes", "15")
    return max(1, min(int(v), 240)) * 60 if v.isdigit() else 15 * 60


def access_for(user, dev: Device | None) -> dict:
    """Cómo puede usar este usuario la consola de este equipo: mode (none/ro/rw), si necesita aprobación (y hasta
    cuándo la tiene), y los comandos prohibidos de su grupo."""
    mode = role_mode(user.role) if user is not None else "none"
    res = {"mode": mode, "approval": False, "until": None, "deny": ""}
    if dev is None or mode == "none":
        return res
    rule = group_rules().get(str(dev.group_id or ""), {})
    if _setting("console_enforce_deny", "1") == "1":
        res["deny"] = rule.get("deny", "")
    if rule.get("mode") == "approval" and user.role != "admin":
        res["approval"] = True
        with SessionLocal() as db:
            ok = db.scalar(select(ConsoleAccessRequest.until).where(
                ConsoleAccessRequest.username == user.username, ConsoleAccessRequest.device_id == dev.id,
                ConsoleAccessRequest.status == "approved", ConsoleAccessRequest.until > now())
                .order_by(ConsoleAccessRequest.until.desc()))
        res["until"] = ok
    return res


def console_allowed(user, device_id: int | None = None) -> bool:
    """¿Puede este usuario abrir la consola (de este equipo)? (Sin contar la aprobación previa de algunos grupos.)"""
    if user is None or role_mode(user.role) == "none":
        return False
    if device_id is not None:
        scope = device_scope(user)
        if scope is not None and device_id not in scope:
            return False
    return True


def _open_allowed(user, dev: Device) -> tuple[bool, dict]:
    acc = access_for(user, dev)
    return acc["mode"] != "none" and (not acc["approval"] or acc["until"] is not None), acc


# --------------------------------------------------------------------------- página
def _dev_info(dev: Device) -> dict:
    prof = PROFILE_MAP.get(dev.device_type)
    return {"id": dev.id, "name": dev.name, "host": dev.host, "port": dev.port, "protocol": dev.protocol,
            "vend": dev.device_type, "vl": __import__("app.vendorlogos", fromlist=["x"]).key(dev.device_type), "has_cred": dev.credential_id is not None,
            "model": dev.model or "", "os": (prof.label if prof else dev.device_type) + (f" {dev.os_version}" if dev.os_version else ""),
            "cred": dev.credential.name if dev.credential else ""}


@router.get("/devices/{dev_id}/console")
def console_page(request: Request, dev_id: int, embed: int = 0):  # embed=2: panel de Operar › Consola (sin cabecera)
    """Consola SSH. embed=1: solo el terminal, dentro de un diálogo o de un panel de Operar › Consola."""
    user = require(request)
    check_device(user, dev_id)
    if not console_allowed(user, dev_id):
        raise Forbidden()
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev is None:
            raise Forbidden()
        ok, acc = _open_allowed(user, dev)
        os_key = cc.os_of(dev.device_type)
        info = _dev_info(dev)
        dtype = dev.device_type
    from . import assistant, favorites
    resp = render(request, "console.html", user, dev=info, compact=True, embed=bool(embed), bare=embed == 2,
                  favs=favorites.for_console(user, dtype),
                  autologin=_setting("console_autologin", "0") == "1", access=acc, can_open=ok,
                  profile=cc.client_profile(os_key, _, dev), idle_min=idle_seconds() // 60,
                  recording=_setting("console_record", "1") == "1", ai=assistant.panel_info(user))
    if embed:  # solo esta página puede mostrarse dentro de otra, y únicamente de OpenNCCM mismo
        from .main import CSP
        resp.headers["Content-Security-Policy"] = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@router.post("/devices/{dev_id}/console/request")
async def console_request(request: Request, dev_id: int):
    """Pedir consola en un equipo cuyo grupo exige aprobación: motivo y plazo (lo aprueba un administrador)."""
    user = require(request)
    check_device(user, dev_id)
    form = await request.form()
    reason = (form.get("reason") or "").strip()[:500]
    hours = int(form.get("hours") or 1) if str(form.get("hours") or "1").isdigit() else 1
    if not reason:
        flash(request, _("Escriba el motivo."), "error")
        return go(f"/devices/{dev_id}/console")
    with SessionLocal() as db:
        db.add(ConsoleAccessRequest(username=user.username, device_id=dev_id, reason=reason, hours=min(max(hours, 1), 24)))
        db.commit()
    audit(user.username, "console_access_request", f"equipo {dev_id}: {reason}")
    flash(request, _("Pedido enviado. Un administrador lo verá en Operar › Consola › Acceso."))
    return go(f"/devices/{dev_id}/console")


# --------------------------------------------------------------------------- sesiones en vivo (compartir y grabar)
class Live:
    """Una sesión abierta: a quién se le manda la salida (el dueño y quienes miran con el enlace de solo lectura)."""

    def __init__(self, sid: int, owner: str, dev_id: int, dev_name: str, loop):
        self.sid, self.owner, self.dev_id, self.dev_name, self.loop = sid, owner, dev_id, dev_name, loop
        self.token: str | None = None
        self.watchers: dict = {}           # WebSocket -> usuario
        self.buf: deque[str] = deque(maxlen=400)  # lo último que se vio, para quien entra a mirar
        self.owner_ws: WebSocket | None = None

    def emit(self, msg: dict):
        """Desde cualquier hilo: al dueño y a quienes miran."""
        text = json.dumps(msg)
        for w in [self.owner_ws, *self.watchers]:
            if w is not None:
                asyncio.run_coroutine_threadsafe(_safe_send(w, text), self.loop)

    def who(self):
        if self.owner_ws is not None:
            asyncio.run_coroutine_threadsafe(_safe_send(self.owner_ws, json.dumps(
                {"t": "w", "who": sorted(set(self.watchers.values()))})), self.loop)


LIVE: dict[int, Live] = {}
_live_lock = threading.Lock()


async def _safe_send(ws: WebSocket, text: str):
    try:
        await ws.send_text(text)
    except Exception:  # noqa: BLE001 - el navegador ya se fue
        pass


class Recorder:
    """Grabación de una sesión: una línea JSON por evento [segundos desde el inicio, tipo, texto]
    (i = comando escrito, o = salida del equipo, n = aviso de OpenNCCM)."""

    def __init__(self, sid: int, enabled: bool):
        self.t0 = time.time()
        self.f = None
        if enabled:
            REC_DIR.mkdir(parents=True, exist_ok=True)
            self.f = open(REC_DIR / f"{sid}.jsonl", "a", encoding="utf-8")
        self.lock = threading.Lock()

    def add(self, kind: str, text: str):
        if self.f is None or not text:
            return
        with self.lock:
            self.f.write(json.dumps([round(time.time() - self.t0, 2), kind, text], ensure_ascii=False) + "\n")
            self.f.flush()

    def close(self):
        if self.f is not None:
            self.f.close()


ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>78]|\x07|\x1b\][^\x07]*\x07")


def plain(text: str) -> str:
    """Salida sin secuencias de control (lo que se graba y se busca)."""
    return ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "")


# --------------------------------------------------------------------------- WebSocket
class _Bridge:
    """Puente entre el canal SSH (bloqueante, en un hilo) y el WebSocket (asíncrono)."""

    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop):
        self.ws, self.loop, self.closed = ws, loop, False

    def send(self, text: str):
        if not self.closed:
            asyncio.run_coroutine_threadsafe(_safe_send(self.ws, text), self.loop)


async def _read_line(ws: WebSocket, prompt: str, secret: bool, v2: bool) -> str | None:
    """Pide un dato dentro del terminal (usuario o contraseña) antes de conectar."""
    out = (lambda s: json.dumps({"t": "o", "d": s})) if v2 else (lambda s: s)
    await ws.send_text(out(prompt) if not v2 else json.dumps({"t": "p", "d": prompt, "secret": secret}))
    buf = ""
    while True:
        msg = json.loads(await ws.receive_text())
        if v2 and msg.get("t") == "c":  # la consola nueva manda la línea entera
            await ws.send_text(out(("" if secret else msg.get("d", "")) + "\r\n"))
            return msg.get("d", "")
        if msg.get("t") != "d":
            continue
        for ch in msg.get("d", ""):
            if ch in "\r\n":
                await ws.send_text(out("\r\n"))
                return buf
            if ch in "\x7f\b":
                if buf:
                    buf = buf[:-1]
                    if not secret:
                        await ws.send_text(out("\b \b"))
            elif ch == "\x03":  # Ctrl+C
                return None
            elif ch.isprintable():
                buf += ch
                if not secret:
                    await ws.send_text(out(ch))


async def _refuse(ws: WebSocket, why: str, log_detail: str):
    """Rechaza la consola explicando el motivo en el propio terminal. Cerrar antes de aceptar la conexión hace que el
    navegador solo vea un corte genérico («[Desconectado]») sin saber por qué."""
    log.warning("Consola rechazada: %s", log_detail)
    await ws.accept()
    if ws.query_params.get("v") == "2":
        await ws.send_text(json.dumps({"t": "n", "k": "bad", "d": why}))
    else:
        await ws.send_text("\r\n\x1b[31m" + why + "\x1b[0m\r\n")
    await ws.close(code=1008)


def fireware_logout(chan, tries: int = 4) -> int:
    """Fireware no cierra la sesión de administración al cortarse el SSH (queda en «who» hasta que vence su tiempo de
    inactividad, y si quedó en «configure», con el modo de edición tomado para los demás). Al cerrar la consola se sale
    con «exit» hasta que el Firebox corta (de «configure» al modo principal y de ahí se cierra la sesión)."""
    sent = 0
    for _i in range(tries):
        try:
            if chan.closed or chan.exit_status_ready():
                break
            chan.send("exit\n")
            sent += 1
            time.sleep(0.6)
            while chan.recv_ready():
                chan.recv(4096)
        except Exception:  # noqa: BLE001 - el canal ya se cerró
            break
    return sent


def _next_version(dev_id: int) -> int:
    with SessionLocal() as db:
        return (db.scalar(select(func.count()).select_from(ConfigVersion).where(ConfigVersion.device_id == dev_id)) or 0) + 1


@router.websocket("/devices/{dev_id}/console/ws")
async def console_ws(ws: WebSocket, dev_id: int):
    v2 = ws.query_params.get("v") == "2"
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.headers.get("host"):
        # otro sitio intentando abrir la consola, o un proxy que no reenvía la cabecera Host original
        return await _refuse(ws, _f("No se pudo abrir la consola: la página se abrió como {0} pero el servidor recibió {1}. Si hay un proxy delante de OpenNCCM, debe reenviar la cabecera Host original.", urlparse(origin).netloc, ws.headers.get("host") or "?"),
                             f"origen {origin!r} distinto de Host {ws.headers.get('host')!r}")
    user = current_user(ws)  # type: ignore[arg-type]  (misma sesión que las páginas)
    if user is None:
        return await _refuse(ws, _("No se pudo abrir la consola: la sesión caducó. Vuelva a iniciar sesión y abra la consola otra vez."),
                             "sin sesión")
    if not console_allowed(user, dev_id):
        return await _refuse(ws, _("No se pudo abrir la consola: su usuario no tiene permiso (Operar › Consola › Acceso)."),
                             f"{user.username} sin permiso para el equipo {dev_id}")
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev is None:
            return await _refuse(ws, _("El equipo ya no existe."), f"equipo {dev_id} inexistente")
        ok, acc = _open_allowed(user, dev)
        if not ok:
            return await _refuse(ws, _("Este grupo de equipos exige aprobación para abrir la consola. Pídala con su motivo y plazo."),
                                 f"{user.username} sin aprobación para el equipo {dev_id}")
        name, host, port, protocol = dev.name, dev.host, dev.port, dev.protocol
        cred = dev.credential
        family = get_profile(dev.device_type).family
        os_key, dev_type = cc.os_of(dev.device_type), dev.device_type
        cred_user = cred.username if cred else None
        cred_pass = decrypt(cred.password_enc) if cred and cred.password_enc else None
        cred_secret = decrypt(cred.secret_enc) if cred and cred.secret_enc else None
    await ws.accept()

    def out(text: str) -> str:
        return json.dumps({"t": "o", "d": text}) if v2 else text

    def note(text: str, kind: str = "info") -> str:
        return json.dumps({"t": "n", "k": kind, "d": text}) if v2 else f"\r\n\x1b[33m{text}\x1b[0m\r\n"

    addr = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
    if protocol == "telnet":
        await ws.send_text(note(_("La consola web solo funciona por SSH; este equipo está configurado con Telnet."), "bad"))
        await ws.close()
        return
    try:
        cols = max(40, min(int(ws.query_params.get("cols", 120)), 400))
        rows = max(10, min(int(ws.query_params.get("rows", 32)), 200))
    except ValueError:
        cols, rows = 120, 32
    autologin = _setting("console_autologin", "0") == "1" and cred_user is not None
    try:
        if autologin:
            username, password = cred_user, cred_pass or ""
            await ws.send_text(out(_f("Conectando a {0} ({1}:{2}) como {3}…", name, host, port, username) + "\r\n"))
        else:
            await ws.send_text(out(_f("Consola de {0} ({1}:{2}). Ctrl+C cancela.", name, host, port) + "\r\n"))
            username = await _read_line(ws, _("Usuario:") + " ", secret=False, v2=v2)
            password = await _read_line(ws, _("Contraseña:") + " ", secret=True, v2=v2) if username else None
            if not username or password is None:
                await ws.close()
                return
            await ws.send_text(out(_("Conectando…") + "\r\n"))
    except (WebSocketDisconnect, RuntimeError):
        return
    login_name = username
    if v2 and os_key == "ros" and "+" not in username:
        login_name = username + "+cte"  # RouterOS: sin colores, sin detectar el terminal, terminal «tonto» (líneas)

    loop = asyncio.get_running_loop()
    legacy_ssh.enable()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _connect():
        client.connect(host, port=port, username=login_name, password=password, timeout=15, banner_timeout=30,
                       auth_timeout=30, look_for_keys=False, allow_agent=False)
        ch = client.invoke_shell(term="dumb" if v2 else "xterm-256color", width=cols, height=rows)
        ch.settimeout(0.5)
        return ch

    try:
        chan = await asyncio.to_thread(_connect)
    except paramiko.AuthenticationException:
        await ws.send_text(note(_("Usuario o contraseña incorrectos."), "bad"))
        audit(user.username, "console_failed", f"{name}: autenticación rechazada", addr)
        await ws.close()
        return
    except Exception as e:  # noqa: BLE001
        await ws.send_text(note(_f("No se pudo conectar: {0}", e), "bad"))
        audit(user.username, "console_failed", f"{name}: {e}", addr)
        await ws.close()
        return

    started = time.time()
    record = _setting("console_record", "1") == "1"
    with SessionLocal() as db:
        cs = ConsoleSession(username=user.username, device_id=dev_id, device_name=name, ip=addr, recorded=record)
        db.add(cs)
        db.commit()
        sid = cs.id
    rec = Recorder(sid, record)
    live = Live(sid, user.username, dev_id, name, loop)
    live.owner_ws = ws if v2 else None
    with _live_lock:
        LIVE[sid] = live
    audit(user.username, "console_open", f"{name} ({host}) como {username}" + (" [inicio automático]" if autologin else "")
          + f" · sesión {sid}", addr)
    if v2:
        await ws.send_text(json.dumps({"t": "s", "s": "connected", "sid": sid, "mode": acc["mode"]}))
    bridge = _Bridge(ws, loop)
    last_input = [time.time()]
    stats = {"cmds": 0, "danger": False}
    idle = idle_seconds()
    paging = cc.PAGING_OFF.get(os_key) if v2 else None
    quiet = [False]       # se está apagando el paginado: no se muestra su respuesta
    backup_timer: list[threading.Timer | None] = [None]
    pending_note: list[str | None] = [None]  # aviso de cambio: se muestra cuando el equipo vuelve a dar el prompt

    def show(text: str):
        if v2:
            live.buf.append(text)
            live.emit({"t": "o", "d": text})
        else:
            bridge.send(text)
        # «\r» al principio = se reescribe la línea (el prompt tras apagar el paginado): en la grabación, línea nueva
        rec.add("o", plain("\n" + text[1:] if text.startswith("\r") and not text.startswith("\r\n") else text))

    def tell(text: str, kind: str = "info"):
        msg = {"t": "n", "k": kind, "d": text}
        if v2:
            live.buf.append("\n" + text + "\n")
            live.emit(msg)
        else:
            bridge.send(note(text, kind))
        rec.add("n", text)

    def changed():
        """Un comando cambió la configuración: aviso en verde y respaldo en cola (se agrupan los de 20 s)."""
        from . import services
        if backup_timer[0] is None:
            pending_note[0] = _f("# OpenNCCM: cambio detectado en {0} · respaldo v{1} en cola · se puede revertir", name,
                                 _next_version(dev_id))
        else:
            backup_timer[0].cancel()

        def fire():
            backup_timer[0] = None
            services.pool.submit(services.backup_device, dev_id, "console", user.username)
        t = threading.Timer(20, fire)
        t.daemon = True
        backup_timer[0] = t
        t.start()

    def _pump():
        """Del equipo al navegador; con inicio automático entra también al modo privilegiado si hace falta."""
        tail, enable_sent, secret_sent, paging_sent = "", False, False, False
        quiet_since = 0.0
        try:
            while not bridge.closed and not chan.closed:
                try:
                    data = chan.recv(4096)
                except TimeoutError:
                    data = None
                except OSError:
                    break
                if data == b"":
                    break
                if data:
                    text = data.decode("utf-8", "replace")
                    tail = (tail + text)[-200:]
                    if v2 and cc.MORE.search(plain(tail)):  # el equipo pagina igual: se sigue solo
                        chan.send(" ")
                        text = cc.MORE.sub("", plain(text))
                        tail = ""
                    if quiet[0]:
                        if re.search(r"[#>$\]]\s*$", plain(tail)) or time.time() - quiet_since > 6:
                            quiet[0] = False
                            text = "\r" + plain(tail).rsplit("\n", 1)[-1]  # solo el prompt, en la línea del anterior
                        else:
                            text = ""
                    if text and pending_note[0] and re.search(r"[#>$\]]\s*$", plain(tail)) and \
                            not cc.in_config(os_key, plain(tail).rsplit("\n", 1)[-1]):
                        cut = text.rfind("\n") + 1  # la respuesta del comando, el aviso y después el prompt
                        if cut:
                            show(text[:cut])
                        tell(pending_note[0], "ok")
                        pending_note[0] = None
                        text = text[cut:]
                    if text:
                        show(text)
                    if autologin and cred_secret and family in (IOS, FASTPATH):
                        if not enable_sent and tail.rstrip().endswith(">"):
                            chan.send("enable\n")
                            enable_sent = True
                        elif enable_sent and not secret_sent and "assword" in tail[-30:]:
                            chan.send(cred_secret + "\n")
                            secret_sent = True
                    if paging and not paging_sent and re.search(r"[#>\]]\s*$", plain(tail)) and \
                            not (autologin and cred_secret and family in (IOS, FASTPATH) and not secret_sent and
                                 tail.rstrip().endswith(">")):
                        paging_sent = True
                        quiet[0], quiet_since = True, time.time()
                        tail = ""
                        chan.send(paging + "\n")
                if time.time() - last_input[0] > idle or time.time() - started > MAX_SECONDS:
                    tell(_("[Sesión cerrada por inactividad o duración máxima]"), "warn")
                    break
        finally:
            bridge.closed = True
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except RuntimeError:
                pass

    def run_line(cmd: str, force: bool, ro: bool) -> None:
        """Una línea de la consola nueva: reglas, aviso de peligro, grabación y envío al equipo."""
        c = cmd.strip()
        ro = ro or acc["mode"] == "ro"
        if c and ro and cc.is_change(os_key, c, dev_type):
            rec.add("i", c)
            tell(_("Bloqueado: la consola está en modo solo lectura. Este comando cambia la configuración."), "warn")
            chan.send("\n")
            return
        bad = cc.denied(c, acc["deny"]) if c else None
        if bad:
            rec.add("i", c)
            tell(_f("Bloqueado: «{0}» está prohibido en este grupo de equipos.", bad), "bad")
            audit(user.username, "console_denied", f"{name}: {c}", addr)
            chan.send("\n")
            return
        if c and cc.is_danger(os_key, c, dev_type) and not force:
            asyncio.run_coroutine_threadsafe(_safe_send(ws, json.dumps(
                {"t": "g", "cmd": c, "why": _(cc.DANGER_WHY.get(os_key, cc.DANGER_WHY_DEFAULT))})), loop)
            return
        if c:
            stats["cmds"] += 1
            rec.add("i", c)
            if cc.is_danger(os_key, c, dev_type):
                stats["danger"] = True
                audit(user.username, "console_danger", f"{name}: {c}", addr)
        chan.send(cmd + "\n")
        if c and cc.is_change(os_key, c, dev_type) and not cc.is_danger(os_key, c, dev_type):
            changed()

    threading.Thread(target=_pump, daemon=True, name=f"console-{dev_id}").start()
    try:
        while not bridge.closed:
            msg = json.loads(await ws.receive_text())
            t = msg.get("t")
            if t == "d":
                d = msg.get("d", "")
                # consola nueva: por aquí solo van teclas sueltas (Ctrl+C, Enter, «y» a una confirmación); las órdenes
                # van por «c», que es donde se aplican las reglas
                if v2 and len(d) > 3:
                    continue
                last_input[0] = time.time()
                chan.send(d)
            elif t == "c" and v2:
                last_input[0] = time.time()
                await asyncio.to_thread(run_line, str(msg.get("d", ""))[:2000], bool(msg.get("force")), bool(msg.get("ro")))
            elif t == "r":
                chan.resize_pty(width=max(40, min(int(msg.get("c", cols)), 400)),
                                height=max(10, min(int(msg.get("r", rows)), 200)))
    except (WebSocketDisconnect, RuntimeError, ValueError, OSError):
        pass
    finally:
        bridge.closed = True
        with _live_lock:
            LIVE.pop(sid, None)
        for w in list(live.watchers):
            asyncio.run_coroutine_threadsafe(_safe_send(w, json.dumps({"t": "n", "k": "warn", "d": _("La sesión terminó.")})), loop)
        if dev_type == "watchguard_fireware":
            await asyncio.to_thread(fireware_logout, chan)
        try:
            chan.close()
            client.close()
        except Exception:  # noqa: BLE001
            pass
        rec.close()
        with SessionLocal() as db:
            cs = db.get(ConsoleSession, sid)
            if cs:
                cs.ended_at, cs.commands, cs.danger = now(), stats["cmds"], stats["danger"]
                db.commit()
        from . import kb
        kb.on_console_closed(sid)
        audit(user.username, "console_close", f"{name}: {int(time.time() - started)} s · {stats['cmds']} comandos", addr)


# --------------------------------------------------------------------------- compartir (solo lectura)
@router.post("/console/sessions/{sid}/share")
def console_share(request: Request, sid: int):
    """Enlace de solo lectura de una sesión abierta (solo el dueño). Quien lo abre necesita usuario en OpenNCCM."""
    from fastapi.responses import JSONResponse
    user = require(request)
    live = LIVE.get(sid)
    if live is None or live.owner != user.username:
        return JSONResponse({"error": _("La sesión ya no está abierta.")}, 404)
    if live.token is None:
        live.token = secrets.token_urlsafe(18)
    audit(user.username, "console_share", f"{live.dev_name} · sesión {sid}")
    return JSONResponse({"url": f"/console/watch/{live.token}"})


def _live_by_token(token: str) -> Live | None:
    return next((x for x in list(LIVE.values()) if x.token and secrets.compare_digest(x.token, token)), None)


@router.get("/console/watch/{token}")
def console_watch_page(request: Request, token: str):
    user = require(request)
    live = _live_by_token(token)
    if live is None:
        flash(request, _("Esa sesión ya terminó o el enlace no es válido."), "error")
        return go("/console")
    with SessionLocal() as db:
        dev = db.get(Device, live.dev_id)
        if dev is None:
            return go("/console")
        info, os_key = _dev_info(dev), cc.os_of(dev.device_type)
    return render(request, "console.html", user, dev=info, compact=True, embed=False, autologin=True,
                  access={"mode": "ro"}, can_open=True, profile=cc.client_profile(os_key, _, dev), idle_min=idle_seconds() // 60,
                  recording=False, ai={"on": False}, watch={"token": token, "owner": live.owner})


@router.websocket("/console/watch/{token}/ws")
async def console_watch_ws(ws: WebSocket, token: str):
    origin = ws.headers.get("origin")
    user = current_user(ws)  # type: ignore[arg-type]
    live = _live_by_token(token)
    if (origin and urlparse(origin).netloc != ws.headers.get("host")) or user is None or live is None:
        await ws.accept()
        await ws.send_text(json.dumps({"t": "n", "k": "bad", "d": _("Esa sesión ya terminó o el enlace no es válido.")}))
        await ws.close(code=1008)
        return
    await ws.accept()
    await ws.send_text(json.dumps({"t": "o", "d": "".join(live.buf)}))
    live.watchers[ws] = user.username
    live.who()
    audit(user.username, "console_watch", f"{live.dev_name} · sesión {live.sid} de {live.owner}")
    try:
        while True:
            await ws.receive_text()  # quien mira no escribe: solo se espera a que se vaya
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        live.watchers.pop(ws, None)
        live.who()


def close_orphans() -> int:
    """Al arrancar no hay ninguna consola abierta: las que quedaron «en curso» se cierran con la hora de su último evento."""
    n = 0
    with SessionLocal() as db:
        for cs in db.scalars(select(ConsoleSession).where(ConsoleSession.ended_at.is_(None))):
            end = cs.started_at
            try:
                with open(REC_DIR / f"{cs.id}.jsonl", encoding="utf-8") as f:
                    last = None
                    for last in f:
                        pass
                if last:
                    end = cs.started_at + timedelta(seconds=float(json.loads(last)[0]))
            except (OSError, ValueError, IndexError):
                pass
            cs.ended_at = end
            n += 1
        db.commit()
    return n


def cleanup_recordings(days: int | None = None) -> int:
    """Borra las grabaciones más viejas que el plazo (Operar › Consola › Acceso; 180 días por omisión)."""
    if days is None:
        v = _setting("console_retention_days", "180")
        days = int(v) if v.isdigit() else 180
    limit = now() - timedelta(days=max(days, 1))
    n = 0
    with SessionLocal() as db:
        for cs in db.scalars(select(ConsoleSession).where(ConsoleSession.started_at < limit)):
            (REC_DIR / f"{cs.id}.jsonl").unlink(missing_ok=True)
            db.delete(cs)
            n += 1
        db.commit()
    return n


__all__ = ["router", "console_allowed", "access_for", "role_mode", "LIVE", "ROLES"]
