"""Chat del asistente en todo el sistema (botón «Asistente» de la barra superior).

La IA consulta OpenNCCM con funciones de SOLO LECTURA (equipos, respaldos, alarmas, cambios y versiones, cumplimiento,
ciclo de vida, búsqueda en configuraciones y el manual) mediante la llamada a funciones de cada proveedor, siempre con
el rol y los grupos del usuario y con los secretos ocultos. Responde con la función «responder»: texto corto, equipos
(una línea cada uno), acciones y la fuente.

Las acciones solo navegan o PREPARAN algo. Las que cambian algo (respaldar, crear una tarea, crear una solicitud de
cambio) quedan guardadas en el servidor y solo se hacen cuando el usuario confirma en la tarjeta del chat, con sus
permisos. La conversación se guarda por usuario para retomarla. Cada pregunta queda en Auditoría con las funciones usadas.
"""
from __future__ import annotations

import html
import json
import re
import secrets
import time
from datetime import timedelta

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from . import assistant
from .auth import ROLES, Forbidden, check_device, device_scope, require
from .config import BASE_DIR
from .crypto import try_decrypt
from .db import (AiChat, AiUsage, ChangeRequest, ComplianceResult, ConfigVersion, Device, DeviceGroup, EolEntry, Event,
                 Schedule, SessionLocal, audit, now)
from .drivers import get_profile
from .i18n import _, _f, get_lang

router = APIRouter()
MAX_STEPS = 6
MANUAL = BASE_DIR.parent / "docs" / "src" / "manual-funciones.html"
GO_PATHS = re.compile(r"^/(devices|versions|diff|changes|compliance|events|console|schedules|search|firmware|eol|vulns|topology|"
                      r"ipam|reports|analytics|configlets|account)(/[\w\-./]*)?(\?[\w=&%.\-]*)?(#[\w\-]*)?$")


# --------------------------------------------------------------------------- funciones de solo lectura
def _devs(db, user):
    scope = device_scope(user)
    q = select(Device).order_by(Device.name)
    return [d for d in db.scalars(q) if scope is None or d.id in scope]


def _ago(t):
    if not t:
        return None
    return t.strftime("%Y-%m-%d %H:%M")


def f_equipos(user, estado: str = "todos", texto: str = "") -> dict:
    with SessionLocal() as db:
        out = []
        for d in _devs(db, user):
            if estado == "error" and d.last_status != "error":
                continue
            if estado == "ok" and d.last_status != "ok":
                continue
            t = texto.lower().strip()
            if t and t not in (d.name + " " + d.host + " " + (d.model or "") + " " + d.device_type).lower():
                continue
            out.append({"id": d.id, "nombre": d.name, "ip": d.host, "plataforma": get_profile(d.device_type).label,
                        "modelo": d.model, "version": d.os_version, "grupo": d.group.name if d.group else None,
                        "estado": d.last_status, "ultimo_respaldo": _ago(d.last_backup_at),
                        "error": (d.last_error or "")[:160] or None})
        return {"total": len(out), "equipos": out[:60]}


def f_equipo(user, nombre: str) -> dict:
    with SessionLocal() as db:
        d = next((x for x in _devs(db, user) if x.name.lower() == nombre.lower().strip()), None)
        if d is None:
            return {"error": "no existe o no tiene permiso"}
        vs = db.scalars(select(ConfigVersion).where(ConfigVersion.device_id == d.id).order_by(ConfigVersion.id.desc()).limit(5)).all()
        ev = db.scalars(select(Event).where(Event.device_id == d.id, Event.acknowledged.is_(False))
                        .order_by(Event.id.desc()).limit(8)).all()
        return {"id": d.id, "nombre": d.name, "ip": d.host, "plataforma": get_profile(d.device_type).label, "modelo": d.model,
                "version": d.os_version, "serie": d.serial, "estado": d.last_status, "error": (d.last_error or "")[:300] or None,
                "ultimo_respaldo": _ago(d.last_backup_at), "difiere_linea_base": d.baseline_drift,
                "versiones": [{"id": v.id, "fecha": _ago(v.created_at), "agregadas": v.added, "quitadas": v.removed,
                               "origen": v.trigger} for v in vs],
                "alarmas": [{"severidad": e.severity, "tipo": e.category, "texto": e.message[:160]} for e in ev]}


def f_respaldos_fallidos(user, dias: int = 7) -> dict:
    lim = now() - timedelta(days=max(1, min(int(dias or 7), 90)))
    with SessionLocal() as db:
        ids = {d.id: d for d in _devs(db, user)}
        fail = [d for d in ids.values() if d.last_status == "error"]
        ev = db.scalars(select(Event).where(Event.category == "Respaldo fallido", Event.created_at >= lim)
                        .order_by(Event.id.desc()).limit(80)).all()
        return {"con_error_ahora": [{"id": d.id, "nombre": d.name, "error": (d.last_error or "")[:160]} for d in fail],
                "fallos_en_el_periodo": [{"equipo": ids[e.device_id].name, "fecha": _ago(e.created_at), "texto": e.message[:140]}
                                         for e in ev if e.device_id in ids][:40]}


def f_alarmas(user, limite: int = 20) -> dict:
    with SessionLocal() as db:
        ids = {d.id: d.name for d in _devs(db, user)}
        rows = db.scalars(select(Event).where(Event.acknowledged.is_(False)).order_by(Event.id.desc()).limit(300)).all()
        out = [{"severidad": e.severity, "tipo": e.category, "equipo": ids.get(e.device_id), "equipo_id": e.device_id,
                "fecha": _ago(e.created_at), "texto": e.message[:160]}
               for e in rows if e.device_id is None or e.device_id in ids]
        return {"abiertas": len(out), "alarmas": out[:max(1, min(int(limite or 20), 60))]}


def f_cambios(user, horas: int = 24) -> dict:
    lim = now() - timedelta(hours=max(1, min(int(horas or 24), 24 * 30)))
    with SessionLocal() as db:
        ids = {d.id: d.name for d in _devs(db, user)}
        vs = db.scalars(select(ConfigVersion).where(ConfigVersion.created_at >= lim).order_by(ConfigVersion.id.desc()).limit(200)).all()
        crs = db.scalars(select(ChangeRequest).where(ChangeRequest.created_at >= lim).order_by(ChangeRequest.id.desc()).limit(40)).all()
        pend = db.scalar(select(func.count()).select_from(ChangeRequest).where(ChangeRequest.status == "pending")) or 0
        return {"versiones_nuevas": [{"equipo": ids[v.device_id], "equipo_id": v.device_id, "version_id": v.id, "fecha": _ago(v.created_at),
                                      "agregadas": v.added, "quitadas": v.removed, "origen": v.trigger, "usuario": v.username}
                                     for v in vs if v.device_id in ids and (v.added or v.removed)][:60],
                "solicitudes": [{"id": c.id, "titulo": c.title, "estado": c.status, "pedida_por": c.requested_by,
                                 "fecha": _ago(c.created_at)} for c in crs],
                "solicitudes_pendientes": pend}


def f_diferencias(user, version_id: int) -> dict:
    from . import services
    with SessionLocal() as db:
        v = db.get(ConfigVersion, int(version_id))
        if v is None:
            return {"error": "no existe"}
        check_device(user, v.device_id)
        prev = db.scalar(select(ConfigVersion).where(ConfigVersion.device_id == v.device_id, ConfigVersion.id < v.id,
                                                     ConfigVersion.kind == v.kind).order_by(ConfigVersion.id.desc()).limit(1))
        if prev is None:
            return {"error": "es la primera versión"}
        lines = services.diff_lines(try_decrypt(prev.content_enc) or "", try_decrypt(v.content_enc) or "")
        return {"equipo": v.device.name, "anterior_id": prev.id, "version_id": v.id,
                "diferencias": assistant.redact("\n".join(lines[:200]))}


def f_cumplimiento(user) -> dict:
    with SessionLocal() as db:
        ids = {d.id: d.name for d in _devs(db, user)}
        rows = db.scalars(select(ComplianceResult).where(ComplianceResult.passed.is_(False))).all()
        total = db.scalar(select(func.count()).select_from(ComplianceResult)) or 0
        ok = db.scalar(select(func.count()).select_from(ComplianceResult).where(ComplianceResult.passed.is_(True))) or 0
        return {"porcentaje_cumplido": round(100 * ok / total) if total else None,
                "infracciones": [{"id": r.id, "equipo": ids[r.device_id], "equipo_id": r.device_id, "politica": r.policy.name,
                                  "regla": r.rule.name, "severidad": r.rule.severity, "tiene_correccion": bool(r.rule.remediation)}
                                 for r in rows if r.device_id in ids][:80]}


def f_ciclo_de_vida(user) -> dict:
    from . import eol
    with SessionLocal() as db:
        entries = list(db.scalars(select(EolEntry)))
        out = []
        for d in _devs(db, user):
            e = eol.match_entry(d, entries)
            st = eol.status(e)
            if st not in ("vigente", "desconocido"):
                out.append({"equipo": d.name, "equipo_id": d.id, "modelo": d.model, "estado": _(eol.STATUS_LABEL.get(st, st)),
                            "fin_de_soporte": _ago(e.end_of_support) if e else None, "reemplazo": e.replacement if e else None})
        return {"equipos": out[:80]}


def f_buscar_configs(user, texto: str) -> dict:
    from . import services
    t = (texto or "").strip()
    if len(t) < 2:
        return {"error": "texto demasiado corto"}
    out = []
    with SessionLocal() as db:
        for d in _devs(db, user):
            v = services.latest_version(db, d.id)
            if v is None:
                continue
            hits = [ln for ln in (try_decrypt(v.content_enc) or "").splitlines() if t.lower() in ln.lower()]
            if hits:
                out.append({"equipo": d.name, "equipo_id": d.id, "lineas": assistant.redact("\n".join(hits[:5])).splitlines()})
    return {"equipos_con_coincidencias": len(out), "resultados": out[:40]}


_manual_cache: list[tuple[str, str, str]] = []


def _manual() -> list[tuple[str, str, str]]:
    """(id, título, texto) de cada sección del manual."""
    if _manual_cache:
        return _manual_cache
    try:
        s = MANUAL.read_text(encoding="utf-8")
    except OSError:
        return []
    parts = re.split(r'(<h2[^>]*>.*?</h2>)', s, flags=re.S)
    for i in range(1, len(parts) - 1, 2):
        h = parts[i]
        hid = (re.search(r'id="([^"]+)"', h) or [None, ""])[1]
        title = html.unescape(re.sub(r"<[^>]+>", "", h)).strip()
        body = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", parts[i + 1]))).strip()
        _manual_cache.append((hid, title, body[:2500]))
    return _manual_cache


def f_manual(user, tema: str) -> dict:
    words = [w for w in re.findall(r"\w{4,}", (tema or "").lower())]
    scored = []
    for hid, title, body in _manual():
        low = (title + " " + body).lower()
        sc = sum(low.count(w) + 5 * title.lower().count(w) for w in words)
        if sc:
            scored.append((sc, hid, title, body))
    scored.sort(reverse=True)
    return {"secciones": [{"titulo": t, "texto": b} for _s, _h, t, b in scored[:3]]}


FUNCS = {
    "equipos": (f_equipos, "Lista los equipos que el usuario puede ver, con estado y último respaldo.",
                {"estado": {"type": "string", "enum": ["todos", "error", "ok"]}, "texto": {"type": "string"}}),
    "equipo": (f_equipo, "Ficha de un equipo por su nombre: datos, últimas versiones y alarmas abiertas.",
               {"nombre": {"type": "string"}}),
    "respaldos_fallidos": (f_respaldos_fallidos, "Equipos con el último respaldo en error y fallos de respaldo de los últimos días.",
                           {"dias": {"type": "integer"}}),
    "alarmas": (f_alarmas, "Alarmas abiertas (no reconocidas).", {"limite": {"type": "integer"}}),
    "cambios": (f_cambios, "Configuraciones que cambiaron y solicitudes de cambio en las últimas horas.", {"horas": {"type": "integer"}}),
    "diferencias": (f_diferencias, "Diferencias de una versión con la anterior (secretos ocultos).", {"version_id": {"type": "integer"}}),
    "cumplimiento": (f_cumplimiento, "Porcentaje de reglas cumplidas e infracciones de Cumplimiento.", {}),
    "ciclo_de_vida": (f_ciclo_de_vida, "Equipos sin soporte, sin parches o con fin de venta.", {}),
    "buscar_configs": (f_buscar_configs, "Busca un texto en la última configuración de cada equipo.", {"texto": {"type": "string"}}),
    "manual": (f_manual, "Busca en el Manual de funciones de OpenNCCM cómo se hace algo.", {"tema": {"type": "string"}}),
}
ANSWER = {
    "type": "object",
    "properties": {
        "texto": {"type": "string", "description": "Una o dos frases."},
        "equipos": {"type": "array", "items": {"type": "object", "properties": {
            "nombre": {"type": "string"}, "estado": {"type": "string", "enum": ["ok", "aviso", "error"]},
            "motivo": {"type": "string"}}, "required": ["nombre", "estado", "motivo"]}},
        "acciones": {"type": "array", "items": {"type": "object", "properties": {
            "tipo": {"type": "string", "enum": ["ir", "respaldar", "tarea", "solicitud_cambio"]},
            "etiqueta": {"type": "string"}, "url": {"type": "string"},
            "equipos": {"type": "array", "items": {"type": "string"}},
            "comandos": {"type": "string"}, "accion_tarea": {"type": "string", "enum": ["backup", "compliance", "cve"]},
            "cron": {"type": "string"}, "nombre_tarea": {"type": "string"}}, "required": ["tipo", "etiqueta"]}},
        "fuente": {"type": "string", "description": "De dónde sale la respuesta, en pocas palabras."},
    },
    "required": ["texto"],
}


def _tools() -> list[tuple[str, str, dict]]:
    out = [(n, desc, {"type": "object", "properties": props}) for n, (_f_, desc, props) in FUNCS.items()]
    out.append(("responder", "Respuesta final para el usuario. Llama SIEMPRE a esta función para responder.", ANSWER))
    return out


def _system(lang: str, page: dict) -> str:
    ctx = f"Página que está viendo: {page.get('title') or page.get('path')}"
    if page.get("device"):
        ctx += f" (equipo {page['device']})"
    base = ("Eres el asistente de OpenNCCM. Respondes sobre la red del usuario usando las funciones (son de solo lectura y "
            "ya respetan sus permisos) y sobre cómo usar OpenNCCM con la función manual. Responde SIEMPRE llamando a la "
            "función «responder»: «texto» en una o dos frases cortas; «equipos» con una línea por equipo y un motivo muy "
            "corto; como mucho 4 «acciones», la primera la principal; «fuente» en pocas palabras (por ejemplo «Respaldos "
            "de los últimos 7 días» o «Manual › Tareas programadas»). Acciones: «ir» con una url interna de OpenNCCM "
            "(/devices/ID, /devices/ID/console, /diff?a=ID&b=ID, /changes, /compliance, /events, /schedules, /search?q=…); "
            "«respaldar» con los equipos; «tarea» (accion_tarea, cron de 5 campos, nombre_tarea); «solicitud_cambio» "
            "(equipos y comandos). Nunca digas que hiciste algo: solo lo preparas y el usuario confirma. En los «¿cómo "
            "hago…?» ofrece dejarlo listo y «Ver cómo se hace» (ir a la página). Los secretos aparecen como ******. ")
    if lang == "en":
        base += "Answer in English. "
    else:
        base += "Responde en español. "
    return base + ctx


# --------------------------------------------------------------------------- bucle con cada proveedor
def _run(user, name: str, args: dict, used: list[str]) -> dict:
    used.append(name)
    f = FUNCS.get(name)
    if f is None:
        return {"error": "función desconocida"}
    try:
        res = f[0](user, **{k: v for k, v in (args or {}).items() if k in f[2]})
    except Forbidden:
        res = {"error": "sin permiso"}
    except (TypeError, ValueError) as e:
        res = {"error": str(e)[:100]}
    return json.loads(assistant.redact(json.dumps(res, ensure_ascii=False, default=str)))


def _loop_openai(c, system, history, q, user, used):
    msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": q}]
    tools = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, d, p in _tools()]
    ti = to = 0
    for _i in range(MAX_STEPS):
        r = requests.post(c["base"] + "/chat/completions", timeout=assistant.TIMEOUT,
                          headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"},
                          json={"model": c["model"], "messages": msgs, "tools": tools})
        j = assistant._json(r)
        u = j.get("usage") or {}
        ti, to = ti + int(u.get("prompt_tokens") or 0), to + int(u.get("completion_tokens") or 0)
        m = (j.get("choices") or [{}])[0].get("message") or {}
        calls = m.get("tool_calls") or []
        if not calls:
            return {"texto": m.get("content") or ""}, ti, to
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if fn.get("name") == "responder":
                return args, ti, to
            msgs.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(_run(user, fn.get("name"), args, used), ensure_ascii=False)})
    return {"texto": _("No llegué a una respuesta. Pruebe a preguntarlo de otra forma.")}, ti, to


def _loop_anthropic(c, system, history, q, user, used):
    msgs = history + [{"role": "user", "content": q}]
    tools = [{"name": n, "description": d, "input_schema": p} for n, d, p in _tools()]
    ti = to = 0
    for _i in range(MAX_STEPS):
        r = requests.post(c["base"] + "/messages", timeout=assistant.TIMEOUT,
                          headers={"x-api-key": c["key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": c["model"], "max_tokens": 2000, "system": system, "messages": msgs, "tools": tools})
        j = assistant._json(r)
        u = j.get("usage") or {}
        ti, to = ti + int(u.get("input_tokens") or 0), to + int(u.get("output_tokens") or 0)
        blocks = j.get("content") or []
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if not calls:
            return {"texto": "".join(b.get("text", "") for b in blocks if b.get("type") == "text")}, ti, to
        msgs.append({"role": "assistant", "content": blocks})
        results = []
        for b in calls:
            if b.get("name") == "responder":
                return b.get("input") or {}, ti, to
            results.append({"type": "tool_result", "tool_use_id": b.get("id"),
                            "content": json.dumps(_run(user, b.get("name"), b.get("input") or {}, used), ensure_ascii=False)})
        msgs.append({"role": "user", "content": results})
    return {"texto": _("No llegué a una respuesta. Pruebe a preguntarlo de otra forma.")}, ti, to


def _loop_gemini(c, system, history, q, user, used):
    contents = [{"role": "model" if h["role"] == "assistant" else "user", "parts": [{"text": h["content"]}]} for h in history]
    contents.append({"role": "user", "parts": [{"text": q}]})
    decl = [{"name": n, "description": d, "parameters": p if p.get("properties") else {"type": "object", "properties": {}}}
            for n, d, p in _tools()]
    model = c["model"].removeprefix("models/")
    ti = to = 0
    for _i in range(MAX_STEPS):
        r = requests.post(f"{c['base']}/models/{model}:generateContent", timeout=assistant.TIMEOUT,
                          headers={"x-goog-api-key": c["key"], "Content-Type": "application/json"},
                          json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                                "tools": [{"functionDeclarations": decl}]})
        j = assistant._json(r)
        u = j.get("usageMetadata") or {}
        ti, to = ti + int(u.get("promptTokenCount") or 0), to + int(u.get("candidatesTokenCount") or 0)
        cand = ((j.get("candidates") or [{}])[0].get("content") or {})
        parts = cand.get("parts") or []
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            return {"texto": "".join(p.get("text", "") for p in parts)}, ti, to
        contents.append({"role": "model", "parts": parts})
        resp = []
        for fc in calls:
            if fc.get("name") == "responder":
                return fc.get("args") or {}, ti, to
            resp.append({"functionResponse": {"name": fc.get("name"), "response": {"result": _run(user, fc.get("name"), fc.get("args") or {}, used)}}})
        contents.append({"role": "user", "parts": resp})
    return {"texto": _("No llegué a una respuesta. Pruebe a preguntarlo de otra forma.")}, ti, to


# --------------------------------------------------------------------------- acciones y conversación
def _resolve(user, raw: list) -> list[dict]:
    """Las acciones de la respuesta, comprobadas: «ir» solo a páginas de OpenNCCM; las demás quedan pendientes de
    confirmar, con su resumen hecho aquí (no por la IA)."""
    out = []
    with SessionLocal() as db:
        names = {d.name.lower(): d for d in _devs(db, user)}
        for a in (raw or [])[:6]:
            t, label = a.get("tipo"), str(a.get("etiqueta") or "")[:60]
            if t == "ir":
                url = str(a.get("url") or "")
                if GO_PATHS.match(url):
                    out.append({"kind": "go", "label": label, "url": url})
                continue
            devs = [names[n.lower()] for n in (a.get("equipos") or []) if isinstance(n, str) and n.lower() in names]
            if t == "respaldar" and devs and ROLES.get(user.role, 0) >= ROLES["operator"]:
                out.append({"kind": "backup", "label": label, "ids": [d.id for d in devs][:20],
                            "title": _("Respaldar ahora"), "summary": ", ".join(d.name for d in devs[:20])})
            elif t == "solicitud_cambio" and devs and (a.get("comandos") or "").strip() and ROLES.get(user.role, 0) >= ROLES["operator"]:
                cmds = str(a["comandos"]).strip()[:4000]
                out.append({"kind": "change", "label": label, "ids": [d.id for d in devs][:50], "body": cmds,
                            "title": _("Crear solicitud de cambio"),
                            "summary": _f("{0} · {1} comandos (pasa por aprobación)", ", ".join(d.name for d in devs[:8]),
                                          len(cmds.splitlines()))})
            elif t == "tarea" and user.role == "admin" and a.get("accion_tarea") in ("backup", "compliance", "cve"):
                from . import cronutil
                cron = str(a.get("cron") or "").strip()
                try:
                    desc = cronutil.describe(cron) if len(cron.split()) == 5 else ""
                except Exception:  # noqa: BLE001
                    desc = ""
                if desc:
                    name = str(a.get("nombre_tarea") or label)[:120]
                    out.append({"kind": "schedule", "label": label, "cron": cron, "action": a["accion_tarea"], "name": name,
                                "title": _("Crear la tarea"), "summary": f"{name} · {desc}"})
    for x in out:
        if x["kind"] != "go":
            x["id"] = secrets.token_hex(6)
    return out


def _chat(username: str, create=False) -> AiChat | None:
    with SessionLocal() as db:
        ch = db.scalar(select(AiChat).where(AiChat.username == username).order_by(AiChat.id.desc()).limit(1))
        if ch is None and create:
            ch = AiChat(username=username, messages="[]")
            db.add(ch)
            db.commit()
            db.refresh(ch)
        if ch is not None:
            db.expunge(ch)
        return ch


def _save(chat_id: int, msgs: list):
    with SessionLocal() as db:
        ch = db.get(AiChat, chat_id)
        ch.messages = json.dumps(msgs[-60:], ensure_ascii=False)
        ch.updated_at = now()
        db.commit()


def _public(msgs: list) -> list:
    """Lo que ve el navegador (sin los datos internos de las acciones)."""
    out = []
    for m in msgs:
        if m.get("role") == "assistant":
            m = {**m, "actions": [{k: v for k, v in a.items() if k in ("kind", "label", "url", "id", "title", "summary", "done")}
                                  for a in m.get("actions", [])]}
        out.append(m)
    return out


def chat(user, q: str, page: dict) -> dict:
    c = assistant.cfg()
    if not assistant.allowed(user):
        raise assistant.AiError(_("El asistente no está disponible para su usuario."))
    if c["limit"] and assistant._today_count(user.username) >= c["limit"]:
        raise assistant.AiError(_f("Llegó al límite de {0} preguntas por día.", c["limit"]))
    ch = _chat(user.username, create=True)
    msgs = json.loads(ch.messages or "[]")
    history = []
    for m in msgs[-10:]:
        if m["role"] == "user":
            history.append({"role": "user", "content": m["text"]})
        else:
            history.append({"role": "assistant", "content": m.get("text") or ""})
    used: list[str] = []
    t0 = time.time()
    loop = {"anthropic": _loop_anthropic, "gemini": _loop_gemini}.get(c["provider"], _loop_openai)
    try:
        ans, ti, to = loop(c, _system(get_lang(), page), history, q.strip()[:2000], user, used)
    except requests.RequestException as e:
        raise assistant.AiError(_f("No se pudo hablar con el proveedor: {0}", str(e)[:200])) from e
    items = [{"name": str(i.get("nombre") or "")[:80], "state": i.get("estado") if i.get("estado") in ("ok", "aviso", "error") else "aviso",
              "reason": str(i.get("motivo") or "")[:160]} for i in (ans.get("equipos") or [])[:30] if isinstance(i, dict)]
    reply = {"role": "assistant", "text": str(ans.get("texto") or "")[:1500], "items": items,
             "actions": _resolve(user, ans.get("acciones") or []), "source": str(ans.get("fuente") or "")[:120],
             "ms": int((time.time() - t0) * 1000)}
    msgs += [{"role": "user", "text": q.strip()[:2000]}, reply]
    _save(ch.id, msgs)
    with SessionLocal() as db:
        db.add(AiUsage(username=user.username, provider=c["provider"], model=c["model"], tokens_in=ti, tokens_out=to))
        db.commit()
    audit(user.username, "ai_chat", f"{assistant.provider_name(c['provider'])} · {c['model']} · funciones: "
                                    f"{', '.join(dict.fromkeys(used)) or '-'} · {page.get('path', '')} · {q[:300]}")
    return _public([reply])[0]


def act(user, action_id: str) -> dict:
    """Confirmación de una acción preparada: se hace con los permisos del usuario."""
    ch = _chat(user.username)
    if ch is None:
        raise Forbidden()
    msgs = json.loads(ch.messages or "[]")
    a = next((x for m in msgs if m.get("role") == "assistant" for x in m.get("actions", []) if x.get("id") == action_id), None)
    if a is None or a.get("done"):
        return {"error": _("Esa acción ya no está disponible.")}
    from . import services
    if a["kind"] == "backup":
        if ROLES.get(user.role, 0) < ROLES["operator"]:
            raise Forbidden()
        for i in a["ids"]:
            check_device(user, i)
            services.pool.submit(services.backup_device, i, "manual", user.username)
        res = {"ok": True, "msg": _f("Respaldo en marcha en {0} equipos.", len(a["ids"])), "url": "/devices"}
    elif a["kind"] == "change":
        if ROLES.get(user.role, 0) < ROLES["operator"]:
            raise Forbidden()
        for i in a["ids"]:
            check_device(user, i)
        from .main import _create_cr
        cr = _create_cr(user, "✦ " + a["label"], a["body"], {}, a["ids"])
        res = {"ok": True, "msg": _("Solicitud de cambio creada."), "url": f"/changes/{cr}"}
    elif a["kind"] == "schedule":
        if user.role != "admin":
            raise Forbidden()
        from . import scheduler
        with SessionLocal() as db:
            db.add(Schedule(name=a["name"], action=a["action"], cron=a["cron"]))
            db.commit()
        scheduler.reload_jobs()
        res = {"ok": True, "msg": _f("Tarea «{0}» creada.", a["name"]), "url": "/schedules"}
    else:
        return {"error": _("Esa acción ya no está disponible.")}
    a["done"] = True
    _save(ch.id, msgs)
    audit(user.username, "ai_action", f"{a['kind']} · {a.get('summary', '')}")
    return res


# --------------------------------------------------------------------------- rutas
@router.get("/api/ai/chat")
def api_chat_get(request: Request, path: str = "/"):
    user = require(request)
    if not assistant.allowed(user):
        return JSONResponse({"on": False})
    ch = _chat(user.username)
    with SessionLocal() as db:
        n = len(_devs(db, user))
        ngroups = db.scalar(select(func.count()).select_from(DeviceGroup)) or 0
    return JSONResponse({"on": True, "messages": _public(json.loads(ch.messages)) if ch else [], "devices": n, "suggest": suggestions(path),
                         "groups": ngroups, "who": assistant.provider_name(assistant.cfg()["provider"]),
                         "model": assistant.cfg()["model"]})


@router.post("/api/ai/chat")
async def api_chat(request: Request):
    user = require(request)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    q = str(data.get("q") or "").strip()
    if not q:
        return JSONResponse({"error": _("Escriba la pregunta.")}, 400)
    page = {k: str(data.get(k) or "")[:200] for k in ("path", "title", "device")}
    try:
        return JSONResponse(chat(user, q, page))
    except assistant.AiError as e:
        return JSONResponse({"error": str(e)}, 400)


@router.post("/api/ai/chat/new")
def api_chat_new(request: Request):
    user = require(request)
    with SessionLocal() as db:
        db.add(AiChat(username=user.username, messages="[]"))
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/ai/chat/act")
async def api_chat_act(request: Request):
    user = require(request)
    data = await request.json()
    try:
        return JSONResponse(act(user, str(data.get("id") or "")))
    except Forbidden:
        return JSONResponse({"error": _("Su usuario no tiene permiso para esto.")}, 403)


def suggestions(path: str) -> list[str]:
    if path.startswith("/devices/"):
        return [_("¿Qué cambió en este equipo?"), _("¿Por qué falló su respaldo?"), _("¿Cumple las políticas?")]
    if path.startswith("/compliance"):
        return [_("¿Qué infracciones son críticas?"), _("¿Cómo corrijo Telnet habilitado?"), _("¿Qué equipos cumplen todo?")]
    if path.startswith("/changes"):
        return [_("¿Qué cambios están pendientes?"), _("¿Qué cambió en 24 h?")]
    return [_("¿Qué baja la salud de la red?"), _("¿Qué cambió en 24 h?"), _("¿Qué equipos no tienen soporte?"),
            _("¿Cómo programo un respaldo diario?")]


__all__ = ["router", "chat", "act", "suggestions"]
