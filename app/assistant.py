"""Asistente IA (Administración › Ajustes › Asistente IA): cada instalación conecta el proveedor de su cuenta.

- Desactivado por omisión. Proveedores: Anthropic (Claude), OpenAI, Google (Gemini), Mistral y cualquier servicio
  compatible con la API de OpenAI (OpenRouter, Groq, DeepSeek, Azure…), llamados directamente por HTTPS.
- Antes de enviar se ocultan siempre las contraseñas, comunidades SNMP y claves (no se puede desactivar).
- Sugiere; nunca ejecuta nada. Los comandos que propone llevan su riesgo (solo lectura / cambia la configuración / corta
  el servicio) y se pueden copiar, insertar en la consola o enviar como solicitud de cambio.
- Cada pregunta queda en Auditoría (usuario, equipo, proveedor) y cuenta para el límite diario y el uso del mes.
"""
from __future__ import annotations

import re
import time

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from . import consolecore as cc
from .auth import Forbidden, check_device, require
from .db import AiUsage, ComplianceResult, ConfigVersion, Device, SessionLocal, audit, get_setting, now
from .drivers import get_profile
from .gitexport import mask as _mask
from .crypto import try_decrypt
from .i18n import _, _f, get_lang

router = APIRouter()
TIMEOUT = 90

PROVIDERS = {
    "anthropic": {"label": "Anthropic", "product": "Claude", "base": "https://api.anthropic.com/v1"},
    "openai": {"label": "OpenAI", "product": "GPT", "base": "https://api.openai.com/v1"},
    "gemini": {"label": "Google", "product": "Gemini", "base": "https://generativelanguage.googleapis.com/v1beta"},
    "mistral": {"label": "Mistral AI", "product": "Mistral", "base": "https://api.mistral.ai/v1"},
    "compat": {"label": "Compatible con OpenAI", "product": "", "base": ""},
}
ROLE_KEYS = ("admin", "operator", "viewer")

# Cuadrito de cada proveedor (diseño «D» de Diseño UX): fondo de la marca y su dibujo en 24×24. Son dibujos aproximados,
# no los archivos oficiales de cada marca: se reemplazan aquí cuando estén los SVG oficiales.
LOGOS = {
    "anthropic": ("#D97757", "", '<g stroke="#ffffff" stroke-width="2.6" stroke-linecap="round">'
                                 '<path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4"/></g>'),
    "openai": ("#000000", "#3a3f4b", '<g fill="none" stroke="#ffffff" stroke-width="1.8"><ellipse cx="12" cy="12" rx="9" ry="4"/>'
                                     '<ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(60 12 12)"/>'
                                     '<ellipse cx="12" cy="12" rx="9" ry="4" transform="rotate(120 12 12)"/></g>'),
    "gemini": ("#ffffff", "#e3e6ec", '<defs><linearGradient id="{id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1c7cff"/>'
                                     '<stop offset="1" stop-color="#9b5cff"/></linearGradient></defs>'
                                     '<path d="M12 2C12.6 7.3 16.7 11.4 22 12C16.7 12.6 12.6 16.7 12 22C11.4 16.7 7.3 12.6 2 12C7.3 11.4 11.4 7.3 12 2Z" fill="url(#{id})"/>'),
    "mistral": ("#ffffff", "#e3e6ec", "".join(f'<rect x="{x}" y="{y}" width="4" height="4" fill="{c}"/>' for x, y, c in (
        (3, 4, "#ffd800"), (17, 4, "#ffd800"), (3, 8, "#ffaf00"), (7, 8, "#ffaf00"), (13, 8, "#ffaf00"), (17, 8, "#ffaf00"),
        (3, 12, "#ff8205"), (10, 12, "#ff8205"), (17, 12, "#ff8205"), (3, 16, "#fa500f"), (17, 16, "#fa500f")))),
    "compat": ("#3b4a7a", "", '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4" fill="none" stroke="#ffffff" '
                              'stroke-width="2" stroke-linecap="round"/>'),
}
_logo_n = [0]


def logo(provider: str, size: int = 44) -> str:
    """El cuadrito del proveedor: tamaño en px (44 en Ajustes, 36 en la consola, 20 en la barra superior)."""
    bg, border, svg = LOGOS.get(provider, LOGOS["compat"])
    _logo_n[0] += 1
    inner = round(size * 24 / 44)
    return (f'<span class="ai-tile" aria-hidden="true" style="width:{size}px;height:{size}px;border-radius:{round(size / 4)}px;'
            f'background:{bg};{"box-shadow:inset 0 0 0 1px " + border if border else ""}">'
            f'<svg width="{inner}" height="{inner}" viewBox="0 0 24 24">{svg.replace("{id}", f"aig{_logo_n[0]}")}</svg></span>')


class AiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- ajustes
def cfg() -> dict:
    p = get_setting("ai_provider", "anthropic")
    p = p if p in PROVIDERS else "anthropic"
    lim = get_setting("ai_daily_limit", "50")
    return {"enabled": get_setting("ai_enabled", "0") == "1", "provider": p,
            "base": (get_setting("ai_base_url", "") if p == "compat" else PROVIDERS[p]["base"]).rstrip("/"),
            "key": get_setting(f"ai_key_{p}", ""), "model": get_setting("ai_model", ""),
            "roles": [r for r in get_setting("ai_roles", "admin,operator").split(",") if r in ROLE_KEYS],
            "limit": int(lim) if lim.isdigit() else 50,
            "backup": get_setting("ai_include_backup", "1") == "1", "console": get_setting("ai_include_console", "1") == "1",
            "price_in": _float(get_setting("ai_price_in", "")), "price_out": _float(get_setting("ai_price_out", ""))}


def _float(v: str) -> float | None:
    try:
        return float(v.replace(",", ".")) if v.strip() else None
    except ValueError:
        return None


def provider_name(p: str) -> str:
    return _("Compatible con OpenAI") if p == "compat" else PROVIDERS[p]["label"]


def allowed(user) -> bool:
    c = cfg()
    return bool(user is not None and c["enabled"] and c["key"] and c["model"] and user.role in c["roles"]
                and (c["provider"] != "compat" or c["base"]))


def quick_on(user) -> dict | None:
    """Para la barra superior de cada página (con los ajustes en caché): None si no está disponible."""
    from .auth import cached_setting
    p = cached_setting("ai_provider", "anthropic")
    if user is None or cached_setting("ai_enabled", "0") != "1" or not cached_setting(f"ai_key_{p}", "") or             not cached_setting("ai_model", "") or user.role not in cached_setting("ai_roles", "admin,operator").split(","):
        return None
    return {"t": texts(), "who": provider_name(p) if p in PROVIDERS else p, "model": cached_setting("ai_model", ""), "p": p}


def texts() -> dict:
    return {"title": _("Asistente de red"), "cloud": _("nube"), "backup": _("respaldo"), "console": _("salida de la consola"),
            "secrets": _("secretos ocultos"), "sugg": _("PREGUNTAS SUGERIDAS"), "ph": _("Pregunta al asistente…"),
            "note": _("Sugiere; nunca ejecuta. Antes de enviar se ocultan contraseñas y claves. Cada pregunta queda en Auditoría."),
            "thinking": _("Pensando…"), "copy": _("Copiar"), "insert": _("Insertar"), "cr": _("Enviar como solicitud de cambio"),
            "copied": _("Copiado."), "risk": {"read": _("solo lectura"), "change": _("cambia la configuración"),
                                              "outage": _("corta el servicio")},
            "source": _("Fuente: {0}"), "crdone": _("Solicitud de cambio creada."), "close": _("Cerrar"), "send": _("Enviar"),
            "chat": _("Asistente"), "newc": _("Nueva conversación"), "viewing": _("Viendo: {0} · {1} equipos"),
            "chatph": _("Pregunta sobre tu red o sobre OpenNCCM…"), "more": _("Más acciones"), "cancel": _("Cancelar"),
            "confirm": _("Confirmar"), "see": _("Ver"), "hello": _("Pregúntame sobre tu red o sobre cómo usar OpenNCCM."),
            "chatnote": _("Solo ve lo que tú puedes ver. Prepara acciones, pero nunca las ejecuta sin que confirmes. Cada pregunta queda en Auditoría.")}


def panel_info(user) -> dict:
    """Lo que muestran el botón «Asistente» y los paneles: proveedor activo y modelo (o nada si no está disponible)."""
    if not allowed(user):
        return {"on": False}
    c = cfg()
    return {"on": True, "provider": c["provider"], "who": provider_name(c["provider"]), "model": c["model"],
            "backup": c["backup"], "console": c["console"]}


# --------------------------------------------------------------------------- secretos
_ROS_SECRET_SECTIONS = ("/snmp community", "/user", "/ppp secret", "/interface wireless security-profiles",
                        "/interface wifi security", "/ip ipsec", "/radius", "/interface l2tp-server server",
                        "/interface ovpn-server server", "/certificate")


def redact(text: str) -> str:
    """Oculta secretos con «******»: las reglas de la copia en Git más las comunidades SNMP y nombres secretos de
    RouterOS (que van como «name=» dentro de su sección)."""
    out, section = [], ""
    for line in _mask(text).replace("<oculto>", "******").splitlines():
        s = line.strip()
        if s.startswith("/"):
            words = []
            for w in s.split():
                if "=" in w or w in ("add", "set", "remove", "print", "["):
                    break
                words.append(w)
            section = " ".join(words)
        if section.startswith(_ROS_SECRET_SECTIONS) and section.startswith("/snmp community"):
            line = re.sub(r"\b(name|authentication-password|encryption-password)=(\"[^\"]*\"|\S+)", r"\1=******", line)
        if section.startswith(_ROS_SECRET_SECTIONS):
            line = re.sub(r"\b(secret|password|passphrase|key|private-key|shared-secret)=(\"[^\"]*\"|\S+)", r"\1=******", line)
        line = re.sub(r"(?i)\b(community\s+)(?!\*{6})(\S+)", r"\1******", line) if re.search(r"(?i)snmp.*community\s", line) else line
        out.append(line)
    return "\n".join(out)


def preview() -> str:
    """«Así se envía un respaldo»: unas líneas de un respaldo real con sus secretos ya ocultos."""
    with SessionLocal() as db:
        v = db.scalar(select(ConfigVersion).order_by(ConfigVersion.id.desc()).limit(1))
        text = (try_decrypt(v.content_enc) or "") if v is not None else ""
    if not text:
        text = ("/snmp community\nset [ find default=yes ] name=public\n/user\nadd name=soporte group=full password=Secreta123\n"
                "/interface wireless security-profiles\nset wpa2-pre-shared-key=ClaveWifi\n/ip address\n"
                "add address=192.168.14.1/24 interface=ether2")
    lines = redact(text).splitlines()
    hits = [i for i, x in enumerate(lines) if "******" in x]
    if hits:
        keep = sorted({j for i in hits[:6] for j in (i - 1, i) if j >= 0})
        return "\n".join(lines[j] for j in keep)
    return "\n".join(lines[:10])


# --------------------------------------------------------------------------- proveedores
def _openai_like(c: dict, system: str, user_text: str) -> tuple[str, int, int]:
    r = requests.post(c["base"] + "/chat/completions", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
                      json={"model": c["model"], "messages": [{"role": "system", "content": system},
                                                              {"role": "user", "content": user_text}]})
    j = _json(r)
    try:
        text = j["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AiError(_("El proveedor respondió algo inesperado.")) from e
    u = j.get("usage") or {}
    return text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)


def _anthropic(c: dict, system: str, user_text: str) -> tuple[str, int, int]:
    r = requests.post(c["base"] + "/messages", timeout=TIMEOUT,
                      headers={"x-api-key": c["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": c["model"], "max_tokens": 2000, "system": system,
                            "messages": [{"role": "user", "content": user_text}]})
    j = _json(r)
    text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    u = j.get("usage") or {}
    return text, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)


def _gemini(c: dict, system: str, user_text: str) -> tuple[str, int, int]:
    model = c["model"].removeprefix("models/")
    r = requests.post(f"{c['base']}/models/{model}:generateContent", timeout=TIMEOUT,
                      headers={"x-goog-api-key": c["key"], "Content-Type": "application/json"},
                      json={"systemInstruction": {"parts": [{"text": system}]},
                            "contents": [{"role": "user", "parts": [{"text": user_text}]}]})
    j = _json(r)
    try:
        text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError) as e:
        raise AiError(_("El proveedor respondió algo inesperado.")) from e
    u = j.get("usageMetadata") or {}
    return text, int(u.get("promptTokenCount") or 0), int(u.get("candidatesTokenCount") or 0)


def _json(r: requests.Response) -> dict:
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code >= 400:
        err = j.get("error") if isinstance(j, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or r.text[:200]
        if r.status_code in (401, 403):
            raise AiError(_f("El proveedor rechazó la clave de API ({0}).", r.status_code))
        if r.status_code == 429:
            raise AiError(_("El proveedor dice que se pasó el límite de uso de la cuenta. Pruebe más tarde."))
        raise AiError(_f("El proveedor respondió {0}: {1}", r.status_code, str(msg)[:300]))
    return j


def call(c: dict, system: str, user_text: str) -> tuple[str, int, int]:
    try:
        if c["provider"] == "anthropic":
            return _anthropic(c, system, user_text)
        if c["provider"] == "gemini":
            return _gemini(c, system, user_text)
        return _openai_like(c, system, user_text)
    except requests.RequestException as e:
        raise AiError(_f("No se pudo hablar con el proveedor: {0}", str(e)[:200])) from e


_SKIP_MODEL = re.compile(r"embed|whisper|tts|dall-e|moderation|image|audio|realtime|transcribe|search|aqa|imagen|veo", re.I)


def list_models(c: dict) -> list[str]:
    try:
        if c["provider"] == "anthropic":
            r = requests.get(c["base"] + "/models?limit=100", timeout=30,
                             headers={"x-api-key": c["key"], "anthropic-version": "2023-06-01"})
            return [m["id"] for m in _json(r).get("data", [])]
        if c["provider"] == "gemini":
            r = requests.get(c["base"] + "/models?pageSize=200", timeout=30, headers={"x-goog-api-key": c["key"]})
            return [m["name"].removeprefix("models/") for m in _json(r).get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", []) and not _SKIP_MODEL.search(m["name"])]
        r = requests.get(c["base"] + "/models", timeout=30, headers={"Authorization": f"Bearer {c['key']}"})
        return sorted(m["id"] for m in _json(r).get("data", []) if not _SKIP_MODEL.search(m.get("id", "")))
    except requests.RequestException as e:
        raise AiError(_f("No se pudo hablar con el proveedor: {0}", str(e)[:200])) from e


# --------------------------------------------------------------------------- preguntas
def _system(lang: str) -> str:
    if lang == "en":
        return ("You are the network assistant inside OpenNCCM, a network configuration manager. Answer briefly and "
                "precisely for the device and operating system given. Put every command in its own ``` code block, one "
                "command per line, with the exact syntax of that OS. Never claim you ran anything: the operator decides. "
                "Say clearly when a command changes the configuration or can cut service. Secrets appear as ******; "
                "never ask for them. Use only commands of the platform, model and firmware version given (the "
                "reference list shows that device's own commands); never mix in another vendor's syntax. If a command "
                "changed between versions, give the one for this version; if you are not sure it exists there, say so.")
    return ("Eres el asistente de red de OpenNCCM, un gestor de configuraciones de red. Responde en español, breve y "
            "preciso, para el equipo y el sistema que se indican. Pon cada comando en su propio bloque ``` de código, un "
            "comando por línea, con la sintaxis exacta de ese sistema. Nunca digas que ejecutaste algo: decide el "
            "operador. Avisa claramente cuando un comando cambia la configuración o puede cortar el servicio. Los "
            "secretos aparecen como ******; no los pidas. Usa solo comandos de la plataforma, el modelo y la versión "
            "de firmware indicados (la lista de referencia trae los comandos propios de ese equipo); nunca mezcles la "
            "sintaxis de otro fabricante. Si un comando cambió entre versiones, da el de esta versión; si no estás "
            "seguro de que exista en ella, dilo.")


def _segments(text: str, os_key: str, device_type: str | None = None) -> list[dict]:
    """La respuesta en trozos: texto y bloques de comandos con su riesgo."""
    segs, pos = [], 0
    for m in re.finditer(r"```[\w+-]*\n?(.*?)```", text, re.S):
        if m.start() > pos and text[pos:m.start()].strip():
            segs.append({"k": "text", "t": text[pos:m.start()].strip()})
        block = m.group(1).strip("\n")
        lines = [x for x in block.splitlines() if x.strip()]
        risks = [cc.risk(os_key, x, device_type) for x in lines] or ["read"]
        r = "outage" if "outage" in risks else "change" if "change" in risks else "read"
        segs.append({"k": "cmd", "t": block, "risk": r})
        pos = m.end()
    if text[pos:].strip():
        segs.append({"k": "text", "t": text[pos:].strip()})
    return segs


def _today_count(username: str) -> int:
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(AiUsage).where(AiUsage.username == username,
                                                                          AiUsage.created_at >= start)) or 0


def usage_month() -> dict:
    c = cfg()
    start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        n, ti, to = db.execute(select(func.count(), func.coalesce(func.sum(AiUsage.tokens_in), 0),
                                      func.coalesce(func.sum(AiUsage.tokens_out), 0)).where(AiUsage.created_at >= start)).one()
    cost = None
    if c["price_in"] is not None or c["price_out"] is not None:
        cost = (ti * (c["price_in"] or 0) + to * (c["price_out"] or 0)) / 1_000_000
    return {"questions": int(n), "tokens": int(ti) + int(to), "cost": cost}


def ask(user, question: str, device_id: int | None = None, console: list[str] | None = None, extra: str = "",
        lang: str = "es") -> dict:
    c = cfg()
    if not allowed(user):
        raise AiError(_("El asistente no está disponible para su usuario."))
    if c["limit"] and _today_count(user.username) >= c["limit"]:
        raise AiError(_f("Llegó al límite de {0} preguntas por día.", c["limit"]))
    os_key, ctx, source, dev_name, dev_type = "generic", [], "", "", None
    if device_id:
        check_device(user, device_id)
        with SessionLocal() as db:
            d = db.get(Device, device_id)
            if d is None:
                raise Forbidden()
            prof = get_profile(d.device_type)
            os_key, dev_name, dev_type = cc.os_of(d.device_type), d.name, d.device_type
            from . import cmdref
            ctx.append(f"Equipo: {d.name} · plataforma {prof.label} ({prof.vendor}) · versión de firmware "
                       f"{d.os_version or 'desconocida'} · modelo {d.model or 'desconocido'}")
            ctx.append("Comandos de referencia de este equipo (su plataforma, modelo y versión):\n"
                       + cmdref.reference(d.device_type, d.model or "", d.os_version or ""))
            if c["backup"]:
                v = db.scalar(select(ConfigVersion).where(ConfigVersion.device_id == d.id)
                              .order_by(ConfigVersion.id.desc()).limit(1))
                if v is not None:
                    n = db.scalar(select(func.count()).select_from(ConfigVersion).where(ConfigVersion.device_id == d.id))
                    ctx.append("Último respaldo (secretos ocultos):\n" + redact(try_decrypt(v.content_enc) or "")[:15000])
                    source = _f("respaldo de {0} (v{1})", d.name, n)
            if not source:
                source = _f("referencia de {0}", prof.vendor)
    if console and c["console"]:
        ctx.append("Últimas líneas de la consola (secretos ocultos):\n" + redact("\n".join(console[-200:]))[:12000])
    if extra:
        ctx.append(redact(extra)[:15000])
    t0 = time.time()
    text, ti, to = call(c, _system(lang), "\n\n".join(ctx + ["Pregunta: " + question.strip()[:2000]]))
    with SessionLocal() as db:
        db.add(AiUsage(username=user.username, provider=c["provider"], model=c["model"], tokens_in=ti, tokens_out=to))
        db.commit()
    audit(user.username, "ai_ask", f"{provider_name(c['provider'])} · {c['model']} · {dev_name or '-'} · {question[:300]}")
    return {"segments": _segments(text, os_key, dev_type), "source": source, "ms": int((time.time() - t0) * 1000),
            "model": c["model"], "who": provider_name(c["provider"])}


def suggestions(dev: Device | None) -> list[str]:
    if dev is None:
        return [_("¿Qué equipos respaldaron con error hoy?"), _("¿Cómo armo una regla de cumplimiento para exigir SSH?")]
    os_key = cc.os_of(dev.device_type)
    label = " ".join(x for x in (get_profile(dev.device_type).label, dev.model or "") if x)
    base = [_f("¿Qué comandos de {0} uso para revisar un puerto?", label), _("¿Cómo veo los errores de un puerto?"),
            _("Explica la última salida de la consola")]
    if os_key == "ros":
        return [_("¿Cómo bloqueo una IP en el firewall?"), _("¿Cómo limito Winbox y SSH a la red de gestión?")] + base
    if os_key in ("ios", "nxos", "eos", "fastpath"):
        return [_("¿Cómo dejo solo SSH en las líneas vty?"), _("¿Cómo creo una VLAN y la asigno a un puerto?")] + base
    from . import topology
    if topology.DTYPE_ROLE.get(dev.device_type) == "firewall":
        return [_("¿Cómo veo el estado de los túneles VPN?"), _("¿Cómo veo qué política deja pasar o bloquea una conexión?")] + base
    return base


# --------------------------------------------------------------------------- rutas
@router.post("/api/ai/ask")
async def api_ask(request: Request):
    user = require(request)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    q = str(data.get("q") or "").strip()
    if not q:
        return JSONResponse({"error": _("Escriba la pregunta.")}, 400)
    extra = ""
    kind = data.get("kind")
    try:
        if kind == "diff":
            extra = _diff_context(user, int(data.get("a") or 0), int(data.get("b") or 0))
        elif kind == "compliance":
            extra = _compliance_context(user, int(data.get("id") or 0))
        elif kind == "multi":
            extra = "Respuestas de varios equipos al mismo comando:\n" + str(data.get("text") or "")[:15000]
        elif kind == "page":
            extra = "Página de OpenNCCM: " + str(data.get("title") or "")[:200]
        dev = int(data.get("device_id") or 0) or None
        if kind == "diff" and not dev:
            dev = _version_device(int(data.get("b") or 0))
        if kind == "compliance" and not dev:
            with SessionLocal() as db:
                r = db.get(ComplianceResult, int(data.get("id") or 0))
                dev = r.device_id if r else None
        res = ask(user, q, dev, [str(x) for x in (data.get("console") or [])][:400], extra, get_lang())
    except AiError as e:
        return JSONResponse({"error": str(e)}, 400)
    except Forbidden:
        return JSONResponse({"error": _("Sin permiso para ese equipo.")}, 403)
    return JSONResponse(res)


def _version_device(vid: int) -> int | None:
    with SessionLocal() as db:
        v = db.get(ConfigVersion, vid)
        return v.device_id if v else None


def _diff_context(user, a: int, b: int) -> str:
    from . import services
    with SessionLocal() as db:
        va, vb = db.get(ConfigVersion, a), db.get(ConfigVersion, b)
        if va is None or vb is None:
            return ""
        check_device(user, va.device_id)
        check_device(user, vb.device_id)
        lines = services.diff_lines(try_decrypt(va.content_enc) or "", try_decrypt(vb.content_enc) or "")
    return "Diferencias entre dos versiones de la configuración (- antes, + después):\n" + "\n".join(lines[:600])


def _compliance_context(user, rid: int) -> str:
    with SessionLocal() as db:
        r = db.get(ComplianceResult, rid)
        if r is None:
            return ""
        check_device(user, r.device_id)
        rule = r.rule
        kind = {"must_contain": "debe contener", "must_not_contain": "no debe contener"}.get(rule.kind, rule.kind) if rule else ""
        return (f"Infracción de cumplimiento en {r.device.name if r.device else '?'}: regla «{rule.name if rule else '?'}» "
                f"(la configuración {kind} la expresión regular: {rule.pattern if rule else ''})."
                + (f" Corrección prevista por la política:\n{rule.remediation}" if rule and rule.remediation else ""))


@router.get("/api/ai/info")
def api_info(request: Request, device_id: int = 0):
    user = require(request)
    info = panel_info(user)
    if info["on"]:
        dev = None
        if device_id:
            check_device(user, device_id)
            with SessionLocal() as db:
                dev = db.get(Device, device_id)
                info["suggest"] = suggestions(dev)
        else:
            info["suggest"] = suggestions(None)
    return JSONResponse(info)


def _form_cfg(form) -> dict:
    p = form.get("ai_provider") if form.get("ai_provider") in PROVIDERS else "anthropic"
    c = cfg()
    c["provider"] = p
    c["base"] = ((form.get("ai_base_url") or "").strip() if p == "compat" else PROVIDERS[p]["base"]).rstrip("/")
    c["key"] = (form.get("ai_key") or "").strip() or get_setting(f"ai_key_{p}", "")
    c["model"] = (form.get("ai_model") or "").strip()
    return c


@router.post("/settings/ai/models")
async def settings_models(request: Request):
    require(request, "admin")
    c = _form_cfg(await request.form())
    if not c["key"]:
        return JSONResponse({"ok": False, "msg": _("Escriba la clave de API.")})
    if c["provider"] == "compat" and not c["base"]:
        return JSONResponse({"ok": False, "msg": _("Escriba la dirección del servicio.")})
    try:
        return JSONResponse({"ok": True, "models": list_models(c)})
    except AiError as e:
        return JSONResponse({"ok": False, "msg": str(e)})


@router.post("/settings/ai/test")
async def settings_test(request: Request):
    user = require(request, "admin")
    c = _form_cfg(await request.form())
    if not (c["key"] and c["model"]):
        return JSONResponse({"ok": False, "msg": _("Elija el proveedor, la clave y el modelo.")})
    t0 = time.time()
    try:
        call(c, "Reply with the single word OK.", "ping")
    except AiError as e:
        return JSONResponse({"ok": False, "msg": str(e)})
    ms = int((time.time() - t0) * 1000)
    audit(user.username, "ai_test", f"{provider_name(c['provider'])} · {c['model']} · {ms} ms")
    return JSONResponse({"ok": True, "msg": _f("Conexión correcta con {0}: respondió en {1} ms.", c["model"], ms)})


def settings_values() -> dict:
    """Para la sección Asistente IA de Ajustes."""
    c = cfg()
    return {"cfg": c, "keys_set": {p: bool(get_setting(f"ai_key_{p}", "")) for p in PROVIDERS},
            "usage": usage_month(), "preview": preview(), "providers": PROVIDERS}


def save_settings(form) -> None:
    from .db import set_setting
    p = form.get("ai_provider") if form.get("ai_provider") in PROVIDERS else "anthropic"
    set_setting("ai_enabled", "1" if form.get("ai_enabled") == "on" else "0")
    set_setting("ai_provider", p)
    set_setting("ai_base_url", (form.get("ai_base_url") or "").strip())
    set_setting("ai_model", (form.get("ai_model") or "").strip())
    roles = [r for r in ROLE_KEYS if form.get(f"ai_role_{r}") == "on"]
    set_setting("ai_roles", ",".join(roles or ["admin", "operator"]))  # activarlo sin nadie que pueda usarlo no sirve
    lim = (form.get("ai_daily_limit") or "50").strip()
    set_setting("ai_daily_limit", str(min(int(lim), 10000)) if lim.isdigit() else "50")
    set_setting("ai_include_backup", "1" if form.get("ai_include_backup") == "on" else "0")
    set_setting("ai_include_console", "1" if form.get("ai_include_console") == "on" else "0")
    for k in ("ai_price_in", "ai_price_out"):
        v = (form.get(k) or "").strip()
        set_setting(k, v if _float(v) is not None else "")
    if (form.get("ai_key") or "").strip():
        set_setting(f"ai_key_{p}", form["ai_key"].strip())


__all__ = ["router", "panel_info", "allowed", "ask", "redact"]
