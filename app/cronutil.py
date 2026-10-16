"""Programación de tareas: asistente sin cron, descripción en español y disparadores correctos.

APScheduler interpreta el día de la semana numérico de forma distinta a cron (0 = lunes en lugar de
domingo), así que «0 6 * * 1» se ejecutaba el martes. Aquí los números se convierten a nombres
(mon, tue...) con la semántica de cron estándar (0 y 7 = domingo) antes de crear el disparador.
"""
from __future__ import annotations

import re

from apscheduler.triggers.cron import CronTrigger

DAYS = [("mon", "lunes"), ("tue", "martes"), ("wed", "miércoles"), ("thu", "jueves"), ("fri", "viernes"),
        ("sat", "sábado"), ("sun", "domingo")]
DAY_NAMES = dict(DAYS)
_CRON_NUM = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat", 7: "sun"}
FREQS = [("daily", "Todos los días"), ("weekly", "Algunos días de la semana"), ("monthly", "Una vez al mes"),
         ("hourly", "Cada hora"), ("minutes", "Cada pocos minutos"), ("cron", "Avanzado (expresión cron)")]


def _dow_to_names(field: str) -> str:
    def conv(tok: str) -> str:
        step = ""
        if "/" in tok:
            tok, step = tok.split("/", 1)
            step = "/" + step
        if tok == "*":
            return tok + step
        parts = tok.split("-")
        if all(p.isdigit() for p in parts):
            names = [_CRON_NUM.get(int(p), p) for p in parts]
            if len(names) == 2 and names[1] == "sun" and names[0] != "sun":  # 1-7 / 5-7: el domingo va al final
                return f"{names[0]}-sat,sun" + step if names[0] != "sat" else "sat,sun"
            return "-".join(names) + step
        return tok + step
    return ",".join(conv(t) for t in field.split(","))


def normalize(cron: str) -> str:
    f = cron.split()
    if len(f) != 5:
        raise ValueError("debe tener 5 campos: minuto hora día mes día-de-la-semana")
    f[4] = _dow_to_names(f[4].lower())
    return " ".join(f)


def trigger(cron: str, timezone=None) -> CronTrigger:
    return CronTrigger.from_crontab(normalize(cron), timezone=timezone)


def _hhmm(h: str, m: str) -> str:
    return f"{int(h):02d}:{int(m):02d}"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " y " + words[-1]


def describe(cron: str) -> str:
    """Texto en español para las expresiones habituales."""
    try:
        mi, ho, dom, mon, dow = cron.split()
    except ValueError:
        return cron
    if m := re.fullmatch(r"\*/(\d+)", mi):
        if (ho, dom, mon, dow) == ("*", "*", "*", "*"):
            return f"Cada {m.group(1)} minutos"
    if not mi.isdigit():
        return f"Personalizada: {cron}"
    if ho == "*" and (dom, mon, dow) == ("*", "*", "*"):
        return f"Cada hora, en el minuto {int(mi)}"
    if not ho.isdigit() or mon != "*":
        return f"Personalizada: {cron}"
    at = f"a las {_hhmm(ho, mi)}"
    if dom == "*" and dow == "*":
        return f"Todos los días {at}"
    if dom == "*":
        names = normalize(f"0 0 * * {dow}").split()[4]
        if names in ("mon-fri", "mon,tue,wed,thu,fri"):
            return f"De lunes a viernes {at}"
        if names in ("sat,sun", "sat-sun"):
            return f"Sábados y domingos {at}"
        days = names.split(",")
        if all(d in DAY_NAMES for d in days):
            order = [k for k, _ in DAYS]
            days = sorted(set(days), key=order.index)
            return f"Cada {_join([DAY_NAMES[d] for d in days])} {at}"
        return f"Personalizada: {cron}"
    if dow == "*" and dom.isdigit():
        return f"El día {int(dom)} de cada mes {at}"
    return f"Personalizada: {cron}"


def from_form(form) -> str:
    """Construye la expresión cron a partir del asistente de la página Tareas."""
    freq = form.get("freq") or "cron"
    if freq == "cron":
        cron = " ".join((form.get("cron") or "").split())
    else:
        t = form.get("time") or "02:00"
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            raise ValueError("hora no válida")
        h, m = (int(x) for x in t.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("hora no válida")
        if freq == "daily":
            cron = f"{m} {h} * * *"
        elif freq == "weekly":
            days = [d for d in (form.getlist("days") if hasattr(form, "getlist") else form.get("days", []))
                    if d in DAY_NAMES]
            if not days:
                raise ValueError("elija al menos un día de la semana")
            cron = f"{m} {h} * * {','.join(days)}"
        elif freq == "monthly":
            dom = int(form.get("dom") or 1)
            if not 1 <= dom <= 31:
                raise ValueError("día del mes no válido")
            cron = f"{m} {h} {dom} * *"
        elif freq == "hourly":
            minute = int(form.get("minute") or 0)
            if not 0 <= minute <= 59:
                raise ValueError("minuto no válido")
            cron = f"{minute} * * * *"
        elif freq == "minutes":
            every = int(form.get("every") or 30)
            if every not in (5, 10, 15, 20, 30):
                raise ValueError("intervalo no válido")
            cron = f"*/{every} * * * *"
        else:
            raise ValueError("frecuencia desconocida")
    trigger(cron)  # valida
    return cron
