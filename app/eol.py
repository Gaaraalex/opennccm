"""Fin de venta / fin de soporte (EOL/EOS): tabla propia, importación CSV y API Cisco EoX (opcional)."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta

import requests
from sqlalchemy import select

from .db import Device, EolEntry, SessionLocal, get_setting
from .drivers import get_profile

STATUS_LABEL = {"sin_soporte": "Sin soporte", "fin_mantenimiento": "Sin parches de software",
                "fin_venta": "Fin de venta", "proximo": "Fin de soporte < 12 meses", "vigente": "Vigente",
                "desconocido": "Sin datos"}
STATUS_SEV = {"sin_soporte": "critical", "fin_mantenimiento": "major", "fin_venta": "warning",
              "proximo": "warning", "vigente": "ok", "desconocido": "info"}


def _date(v: str | None):
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    raise ValueError(f"Fecha no válida '{v}' (use AAAA-MM-DD)")


def status(entry: EolEntry | None, today: datetime | None = None) -> str:
    if entry is None:
        return "desconocido"
    today = today or datetime.now()
    if entry.end_of_support and entry.end_of_support <= today:
        return "sin_soporte"
    if entry.end_of_sw_maintenance and entry.end_of_sw_maintenance <= today:
        return "fin_mantenimiento"
    if entry.end_of_support and entry.end_of_support <= today + timedelta(days=365):
        return "proximo"
    if entry.end_of_sale and entry.end_of_sale <= today:
        return "fin_venta"
    return "vigente"


def match_entry(dev: Device, entries: list[EolEntry]) -> EolEntry | None:
    if not dev.model:
        return None
    vendor = get_profile(dev.device_type).vendor.lower()
    for e in entries:
        if e.vendor and e.vendor.lower() not in vendor and vendor not in e.vendor.lower():
            continue
        try:
            if re.search(e.model_pattern, dev.model, re.IGNORECASE):
                return e
        except re.error:
            continue
    return None


def report(device_ids: set[int] | None = None) -> list[tuple[Device, EolEntry | None, str]]:
    with SessionLocal() as db:
        entries = list(db.scalars(select(EolEntry).order_by(EolEntry.id)))
        q = select(Device).order_by(Device.name)
        rows = []
        for d in db.scalars(q):
            if device_ids is not None and d.id not in device_ids:
                continue
            e = match_entry(d, entries)
            rows.append((d, e, status(e)))
        return rows


def import_csv(text: str) -> tuple[int, list[str]]:
    """Columnas: vendor,model_pattern,end_of_sale,end_of_sw_maintenance,end_of_support,replacement,url"""
    created, errors = 0, []
    with SessionLocal() as db:
        for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
            try:
                pattern = (row.get("model_pattern") or "").strip()
                if not pattern:
                    raise ValueError("model_pattern vacío")
                re.compile(pattern)
                db.add(EolEntry(vendor=(row.get("vendor") or "").strip() or None, model_pattern=pattern,
                                end_of_sale=_date(row.get("end_of_sale")),
                                end_of_sw_maintenance=_date(row.get("end_of_sw_maintenance")),
                                end_of_support=_date(row.get("end_of_support")),
                                replacement=(row.get("replacement") or "").strip() or None,
                                url=(row.get("url") or "").strip() or None, source="csv"))
                db.commit()
                created += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                errors.append(f"línea {i}: {e}")
    return created, errors


def cisco_eox_sync() -> tuple[int, list[str]]:
    """Consulta la API Cisco EoX (requiere client_id/secret de https://apiconsole.cisco.com)."""
    cid, secret = get_setting("cisco_api_client_id"), get_setting("cisco_api_secret")
    if not cid or not secret:
        raise RuntimeError("Configure el Client ID y Secret de la API de Cisco en Ajustes")
    tok = requests.post("https://id.cisco.com/oauth2/default/v1/token",
                        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
                        timeout=30)
    tok.raise_for_status()
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}", "Accept": "application/json"}
    with SessionLocal() as db:
        models = sorted({d.model.strip() for d in db.scalars(select(Device)) if d.model
                         and get_profile(d.device_type).vendor == "Cisco"})
    created, errors = 0, []
    for i in range(0, len(models), 20):  # la API acepta hasta 20 PID por consulta
        chunk = models[i:i + 20]
        r = requests.get("https://apix.cisco.com/supporttools/eox/rest/5/EOXByProductID/1/"
                         + ",".join(chunk), headers=headers, timeout=60)
        if r.status_code != 200:
            errors.append(f"HTTP {r.status_code} para {chunk}")
            continue
        with SessionLocal() as db:
            for rec in r.json().get("EOXRecord", []):
                pid = rec.get("EOLProductID") or rec.get("EOXInputValue")
                if not pid or rec.get("EOXError"):
                    continue
                pattern = f"^{re.escape(pid)}$"
                e = db.scalars(select(EolEntry).where(EolEntry.model_pattern == pattern)).first() or \
                    EolEntry(model_pattern=pattern)

                def val(k):
                    v = (rec.get(k) or {}).get("value")
                    return _date(v) if v else None
                e.vendor, e.source = "Cisco", "cisco_api"
                e.end_of_sale = val("EndOfSaleDate")
                e.end_of_sw_maintenance = val("EndOfSWMaintenanceReleases")
                e.end_of_support = val("LastDateOfSupport")
                e.replacement = (rec.get("EOXMigrationDetails") or {}).get("MigrationProductId") or None
                e.url = rec.get("LinkToProductBulletinURL") or None
                db.add(e)
                created += 1
            db.commit()
    return created, errors
