"""Consola SSH web: terminal en el navegador (xterm.js) conectada al equipo por WebSocket.

Seguridad:
  - solo quien tenga permiso según Ajustes (desactivada, solo administradores, o administradores y operadores
    limitados a sus equipos); verificación de origen para que otro sitio no pueda abrirla;
  - sin «inicio automático» se piden usuario y contraseña del equipo; con él se usa la credencial asignada;
  - cierre por inactividad (15 min) y duración máxima (4 h); cada sesión queda en Auditoría.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from urllib.parse import urlparse

import paramiko
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from . import legacy_ssh
from .auth import ROLES, Forbidden, check_device, current_user, device_scope, require
from .crypto import decrypt
from .db import Device, SessionLocal, audit
from .drivers import FASTPATH, IOS, get_profile
from .web import render

log = logging.getLogger("opennccm")
router = APIRouter()
IDLE_SECONDS = 15 * 60
MAX_SECONDS = 4 * 3600


def console_mode() -> str:
    from .auth import cached_setting
    return cached_setting("console_access", "admin")


def console_allowed(user, device_id: int | None = None) -> bool:
    """¿Puede este usuario abrir la consola (de este equipo)?"""
    mode = console_mode()
    if user is None or mode == "off":
        return False
    if mode == "admin" and user.role != "admin":
        return False
    if mode == "operator" and ROLES.get(user.role, 0) < ROLES["operator"]:
        return False
    if device_id is not None:
        scope = device_scope(user)
        if scope is not None and device_id not in scope:
            return False
    return True


@router.get("/devices/{dev_id}/console")
def console_page(request: Request, dev_id: int):
    user = require(request)
    check_device(user, dev_id)
    if not console_allowed(user, dev_id):
        raise Forbidden()
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev is None:
            raise Forbidden()
        info = {"id": dev.id, "name": dev.name, "host": dev.host, "port": dev.port, "protocol": dev.protocol}
    from .auth import cached_setting
    return render(request, "console.html", user, dev=info, autologin=cached_setting("console_autologin", "0") == "1")


class _Bridge:
    """Puente entre el canal SSH (bloqueante, en un hilo) y el WebSocket (asíncrono)."""

    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop):
        self.ws, self.loop, self.closed = ws, loop, False

    def send(self, text: str):
        if not self.closed:
            asyncio.run_coroutine_threadsafe(self.ws.send_text(text), self.loop)


async def _read_line(ws: WebSocket, prompt: str, secret: bool) -> str | None:
    """Pide un dato dentro del terminal (usuario o contraseña) antes de conectar."""
    await ws.send_text(prompt)
    buf = ""
    while True:
        msg = json.loads(await ws.receive_text())
        if msg.get("t") != "d":
            continue
        for ch in msg.get("d", ""):
            if ch in "\r\n":
                await ws.send_text("\r\n")
                return buf
            if ch in "\x7f\b":
                if buf:
                    buf = buf[:-1]
                    if not secret:
                        await ws.send_text("\b \b")
            elif ch == "\x03":  # Ctrl+C
                return None
            elif ch.isprintable():
                buf += ch
                if not secret:
                    await ws.send_text(ch)


@router.websocket("/devices/{dev_id}/console/ws")
async def console_ws(ws: WebSocket, dev_id: int):
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.headers.get("host"):
        await ws.close(code=1008)  # otro sitio intentando abrir la consola
        return
    user = current_user(ws)  # type: ignore[arg-type]  (misma sesión que las páginas)
    if user is None or not console_allowed(user, dev_id):
        await ws.close(code=1008)
        return
    await ws.accept()
    addr = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
    with SessionLocal() as db:
        dev = db.get(Device, dev_id)
        if dev is None:
            await ws.close(code=1008)
            return
        name, host, port, protocol = dev.name, dev.host, dev.port, dev.protocol
        cred = dev.credential
        family = get_profile(dev.device_type).family
        cred_user = cred.username if cred else None
        cred_pass = decrypt(cred.password_enc) if cred and cred.password_enc else None
        cred_secret = decrypt(cred.secret_enc) if cred and cred.secret_enc else None
    if protocol == "telnet":
        await ws.send_text("\r\nLa consola web solo funciona por SSH; este equipo está configurado con Telnet.\r\n")
        await ws.close()
        return
    try:
        cols = max(40, min(int(ws.query_params.get("cols", 120)), 400))
        rows = max(10, min(int(ws.query_params.get("rows", 32)), 200))
    except ValueError:
        cols, rows = 120, 32
    from .auth import cached_setting
    autologin = cached_setting("console_autologin", "0") == "1" and cred_user is not None
    try:
        if autologin:
            username, password = cred_user, cred_pass or ""
            await ws.send_text(f"Conectando a {name} ({host}:{port}) como {username}…\r\n")
        else:
            await ws.send_text(f"Consola de {name} ({host}:{port}). Ctrl+C cancela.\r\n")
            username = await _read_line(ws, "Usuario: ", secret=False)
            password = await _read_line(ws, "Contraseña: ", secret=True) if username else None
            if not username or password is None:
                await ws.close()
                return
            await ws.send_text("Conectando…\r\n")
    except (WebSocketDisconnect, RuntimeError):
        return

    loop = asyncio.get_running_loop()
    legacy_ssh.enable()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _connect():
        client.connect(host, port=port, username=username, password=password, timeout=15, banner_timeout=30,
                       auth_timeout=30, look_for_keys=False, allow_agent=False)
        ch = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
        ch.settimeout(0.5)
        return ch

    try:
        chan = await asyncio.to_thread(_connect)
    except paramiko.AuthenticationException:
        await ws.send_text("\r\n\x1b[31mUsuario o contraseña incorrectos.\x1b[0m\r\n")
        audit(user.username, "console_failed", f"{name}: autenticación rechazada", addr)
        await ws.close()
        return
    except Exception as e:  # noqa: BLE001
        await ws.send_text(f"\r\n\x1b[31mNo se pudo conectar: {e}\x1b[0m\r\n")
        audit(user.username, "console_failed", f"{name}: {e}", addr)
        await ws.close()
        return

    started = time.time()
    audit(user.username, "console_open", f"{name} ({host}) como {username}" + (" [inicio automático]" if autologin else ""),
          addr)
    bridge = _Bridge(ws, loop)
    last_input = [time.time()]

    def _pump():
        """Del equipo al navegador; con inicio automático entra también al modo privilegiado si hace falta."""
        tail, enable_sent, secret_sent = "", False, False
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
                    bridge.send(text)
                    tail = (tail + text)[-200:]
                    if autologin and cred_secret and family in (IOS, FASTPATH):
                        if not enable_sent and tail.rstrip().endswith(">"):
                            chan.send("enable\n")
                            enable_sent = True
                        elif enable_sent and not secret_sent and "assword" in tail[-30:]:
                            chan.send(cred_secret + "\n")
                            secret_sent = True
                if time.time() - last_input[0] > IDLE_SECONDS or time.time() - started > MAX_SECONDS:
                    bridge.send("\r\n\x1b[33m[Sesión cerrada por inactividad o duración máxima]\x1b[0m\r\n")
                    break
        finally:
            bridge.closed = True
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            except RuntimeError:
                pass

    threading.Thread(target=_pump, daemon=True, name=f"console-{dev_id}").start()
    try:
        while not bridge.closed:
            msg = json.loads(await ws.receive_text())
            if msg.get("t") == "d":
                last_input[0] = time.time()
                chan.send(msg.get("d", ""))
            elif msg.get("t") == "r":
                chan.resize_pty(width=max(40, min(int(msg.get("c", cols)), 400)),
                                height=max(10, min(int(msg.get("r", rows)), 200)))
    except (WebSocketDisconnect, RuntimeError, ValueError, OSError):
        pass
    finally:
        bridge.closed = True
        try:
            chan.close()
            client.close()
        except Exception:  # noqa: BLE001
            pass
        audit(user.username, "console_close", f"{name}: {int(time.time() - started)} s", addr)
