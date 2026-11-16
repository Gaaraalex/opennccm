"""Prueba de carga y capacidad: OpenNCCM con cientos o miles de equipos simulados, por escalones.

Uso:  python tests/load_test.py [--steps 100,500] [--history 12] [--out carpeta] [--limit 5]
      (en la CI: --steps 100,1000,2500,5000; en un PC basta con 100,500, que tarda unos minutos)

Todo ocurre en una carpeta temporal que se borra al terminar: una base de datos nueva, OpenNCCM en 127.0.0.1:8300 y
los equipos simulados (tests/loadsim.py) en 127.0.0.1:2300. No toca la instalación ni los datos de este equipo.

En cada escalón se agregan equipos hasta llegar a N (90 % con ~250 líneas de configuración, 9 % con ~3.000 y 1 % con
~40.000, como una OLT), cada uno con su historial (--history versiones en los últimos 180 días), y se mide:
  * «Respaldar todos» de verdad, por SSH: duración, equipos por minuto, fallos, CPU y memoria durante el respaldo;
  * cuánto tarda cada página (la primera vez y la mediana de 3), con varios usuarios a la vez (peticiones por segundo);
  * el tamaño de la base de datos y la memoria de OpenNCCM.
El resultado queda en --out (capacidad.json y capacidad.md). Termina con error si una página tarda más de --limit
segundos (mediana) o si algún respaldo falla: así la CI avisa de un cuello de botella antes que un usuario."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser()
ap.add_argument("--steps", default="100,500")
ap.add_argument("--history", type=int, default=12, help="versiones guardadas por equipo pequeño")
ap.add_argument("--out", default=os.path.join(ROOT, "carga-resultados"))
ap.add_argument("--limit", type=float, default=5.0, help="segundos máximos (mediana) por página")
ap.add_argument("--concurrency", type=int, default=16, help="respaldos a la vez (backup_concurrency)")
ap.add_argument("--app-port", type=int, default=8300)
ap.add_argument("--sim-port", type=int, default=2300)
ap.add_argument("--users", type=int, default=8, help="usuarios a la vez en la prueba de concurrencia")
ap.add_argument("--keep", action="store_true", help="no borrar la carpeta temporal (para analizar después)")
ap.add_argument("--no-backup", action="store_true", help="no hacer «Respaldar todos» (solo páginas)")
ap.add_argument("--backup-steps", default="", help="escalones con «Respaldar todos» (por omisión, todos)")
A = ap.parse_args()
STEPS = [int(x) for x in A.steps.split(",") if x.strip()]
BK_STEPS = {int(x) for x in A.backup_steps.split(",") if x.strip()} or set(STEPS)

WORK = tempfile.mkdtemp(prefix="ncm-carga-")
os.environ.update({"NCM_DATA_DIR": os.path.join(WORK, "data"), "NCM_KEY_FILE": os.path.join(WORK, "data", "secret.key"),
                   "NCM_SYSLOG_PORT": "5597", "NCM_FW_CHECK_NEW": "0", "NCM_USO_URL": "http://127.0.0.1:9/api/uso/aviso", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
os.makedirs(os.environ["NCM_DATA_DIR"])
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import psutil  # noqa: E402
import requests  # noqa: E402
from sqlalchemy import func, insert, select  # noqa: E402

import loadsim  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.db import ConfigVersion, Credential, Device, DeviceGroup, SessionLocal, init_db, set_setting  # noqa: E402

B = f"http://127.0.0.1:{A.app_port}"
CLASSES = {"s": ("carga", "pequeño"), "m": ("carga-m", "mediano"), "l": ("carga-l", "grande (OLT)")}
procs: list[subprocess.Popen] = []


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def klass(i: int) -> str:
    return "l" if i % 100 == 99 else "m" if i % 10 == 9 else "s"


def dir_size(path) -> int:
    return sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(path) for f in fs)


def start():
    py = sys.executable
    subprocess.run([py, "-c", "import sys; sys.path.insert(0,'.'); from app.cli import main; "
                    "sys.argv=['x','create-admin','admin','Carga-Admin-2026']; main(); sys.argv=['x','seed']; main()"],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    init_db()
    set_setting("backup_concurrency", str(A.concurrency))
    set_setting("update_check", "0")
    procs.append(subprocess.Popen([py, os.path.join(HERE, "loadsim.py"), str(A.sim_port), "--change=0.2"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    app_log = open(os.path.join(WORK, "app.log"), "w")
    procs.append(subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(A.app_port)],
                                  cwd=ROOT, stdout=app_log, stderr=subprocess.STDOUT))
    for _ in range(60):
        try:
            if requests.get(B + "/healthz", timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise SystemExit("OpenNCCM no arrancó (ver app.log)")


from requests.adapters import HTTPAdapter  # noqa: E402
from urllib3.exceptions import ProtocolError, TimeoutError as U3Timeout  # noqa: E402
from urllib3.util.retry import Retry  # noqa: E402


class _Reintento(Retry):
    """Reintenta solo una conexión cortada o cerrada por el servidor (ProtocolError) y cuenta cuántas veces pasó."""
    veces = 0

    def increment(self, method=None, url=None, response=None, error=None, _pool=None, _stacktrace=None):
        if error is None:  # una respuesta (5xx incluido): status=0, así que no se reintenta
            return super().increment(method, url, response, error, _pool, _stacktrace)
        if isinstance(error, U3Timeout) or not isinstance(error, ProtocolError) or (method or "").upper() not in self.allowed_methods:
            raise error  # tiempo agotado, otro error o un POST: falla como siempre
        _Reintento.veces += 1
        log(f"  (reintento {_Reintento.veces}: {method} {url} · {str(error)[:80]})")
        return super().increment(method, url, response, error, _pool, _stacktrace)


def session() -> requests.Session:
    s = requests.Session()
    # uvicorn cierra la conexión inactiva a los 5 s y el progreso se consulta cada 5 s: con la máquina cargada, el GET a veces
    # sale por una conexión que el servidor acaba de cerrar («Connection reset by peer»). Un navegador reintenta; aquí también
    # (solo los GET, que no cambian nada, y solo por conexión cortada o cerrada: un 5xx o un tiempo agotado siguen fallando)
    s.mount("http://", HTTPAdapter(max_retries=_Reintento(total=3, connect=0, read=3, status=0, other=0, backoff_factor=0.2,
                                                          allowed_methods=frozenset({"GET"}))))
    s.post(B + "/login", data={"username": "admin", "password": "Carga-Admin-2026"})
    return s


def setup_static():
    """Credenciales (una por tamaño) y 20 sedes."""
    with SessionLocal() as db:
        creds = {}
        for k, (user, _l) in CLASSES.items():
            c = Credential(name=f"Carga {user}", username=user, password_enc=encrypt("carga"))
            db.add(c)
            db.flush()
            creds[k] = c.id
        groups = []
        for i in range(20):
            g = DeviceGroup(name=f"Sede {i + 1:02d}")
            db.add(g)
            db.flush()
            groups.append(g.id)
        db.commit()
    return creds, groups


POOL: dict[str, list[tuple[str, int, str]]] = {}


def pool(k: str) -> list[tuple[str, int, str]]:
    """Configuraciones ya cifradas para el historial (se reutilizan: generarlas y cifrarlas una a una sería lento)."""
    if k not in POOL:
        user = CLASSES[k][0]
        POOL[k] = []
        for _ in range(8):
            txt = loadsim.config(user, True)
            POOL[k].append((encrypt(txt), len(txt), f"{hash(txt) & (2 ** 64 - 1):064x}"))
    return POOL[k]


def grow(n_to: int, creds, groups):
    """Agrega equipos hasta n_to, con su historial."""
    with SessionLocal() as db:
        have = db.scalar(select(func.count(Device.id))) or 0
    if have >= n_to:
        return
    t = time.time()
    now = datetime.now().replace(microsecond=0)
    for start_i in range(have, n_to, 500):
        chunk = range(start_i, min(start_i + 500, n_to))
        with SessionLocal() as db:
            rows = [{"name": f"CARGA-{i + 1:05d}", "host": "127.0.0.1", "port": A.sim_port, "protocol": "ssh",
                     "device_type": "cisco_ios", "credential_id": creds[klass(i)], "group_id": groups[i % len(groups)],
                     "enabled": True, "auto_rollback": False, "autofix_allowed": True, "web_enabled": True,
                     "last_status": "ok", "last_backup_at": now - timedelta(hours=1), "model": "WS-C2960X-48FPD-L",
                     "os_version": "15.2(7)E8", "location": f"Sede {i % len(groups) + 1:02d}"} for i in chunk]
            db.execute(insert(Device), rows)
            ids = {d.name: d.id for d in db.scalars(select(Device).where(Device.name.in_([r["name"] for r in rows])))}
            vers = []
            for i in chunk:
                k = klass(i)
                n_hist = {"s": A.history, "m": max(2, A.history // 2), "l": max(2, A.history // 4)}[k]
                for j in range(n_hist):
                    enc, size, sha = pool(k)[(i + j) % 8]
                    vers.append({"device_id": ids[f"CARGA-{i + 1:05d}"], "kind": "running", "content_enc": enc,
                                 "sha256": sha, "size": size, "added": 0 if j == 0 else 2, "removed": 0 if j == 0 else 2,
                                 "trigger": "scheduled" if j % 3 else "manual", "username": None if j % 3 else "admin",
                                 "created_at": now - timedelta(days=180) + timedelta(days=180 * j / n_hist, minutes=i % 1440)})
            for c in range(0, len(vers), 1000):
                db.execute(insert(ConfigVersion), vers[c:c + 1000])
            db.commit()
    log(f"  {n_to - have} equipos nuevos con su historial en {time.time() - t:.0f} s")


def app_proc() -> psutil.Process:
    return psutil.Process(procs[1].pid)


def rss_mb() -> float:
    p = app_proc()
    return round(sum(x.memory_info().rss for x in [p, *p.children(recursive=True)]) / 2 ** 20, 1)


def backup_all(s: requests.Session) -> dict:
    """«Respaldar todos» por SSH, con su progreso; CPU y memoria muestreadas cada segundo."""
    r = s.post(B + "/backup/all", headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise SystemExit(f"No se pudo lanzar el respaldo: {r.status_code} {r.text[:200]}")
    t0, cpu, mem, job, polls = time.time(), [], [], {}, []
    p = app_proc()
    p.cpu_percent(None)
    while True:
        time.sleep(1)
        cpu.append(p.cpu_percent(None) + sum(c.cpu_percent(None) for c in p.children(recursive=True)))
        mem.append(rss_mb())
        if int(time.time() - t0) % 5 == 0:
            tp = time.time()
            job = s.get(B + "/backup/job").json().get("job") or {}
            s.get(B + "/backup/job?ids=1,2,3")  # lo que pide cada pestaña de Dispositivos cada 2 s
            polls.append(time.time() - tp)
            if job.get("ended"):
                break
    secs = time.time() - t0
    s.post(B + "/backup/job/close")
    n = job.get("n", {})
    errs = [f"{d['name']}: {d.get('why', '')}" for d in job.get("devs", []) if d.get("st") == "err"][:5]
    return {"secs": round(secs, 1), "ok": n.get("ok", 0), "err": n.get("err", 0), "changed": job.get("changed", 0),
            "per_min": round((n.get("ok", 0) + n.get("err", 0)) / secs * 60, 1), "cpu_avg": round(statistics.mean(cpu or [0]), 1),
            "cpu_max": round(max(cpu or [0]), 1), "rss_max_mb": max(mem or [0]), "errors": errs,
            "cpus": psutil.cpu_count() or 1, "poll_max": round(max(polls or [0]), 3)}


def pages(s: requests.Session) -> list[tuple[str, str]]:
    with SessionLocal() as db:
        small = db.scalar(select(Device.id).where(Device.name == "CARGA-00001"))
        big = db.scalar(select(Device.id).where(Device.name == "CARGA-00100"))
        big_v = db.scalar(select(ConfigVersion.id).where(ConfigVersion.device_id == big).order_by(ConfigVersion.id.desc()))
    return [("Panel", "/"), ("Dispositivos", "/devices"), ("Dispositivos: buscar uno", "/devices?q=CARGA-00042"),
            ("Ficha de un equipo", f"/devices/{small}"), ("Ficha de una OLT grande", f"/devices/{big}"),
            ("Versión grande (ver)", f"/versions/{big_v}"), ("Buscar en configs", "/search?q=storm-control"),
            ("Cumplimiento", "/compliance"), ("Análisis", "/analytics"), ("Mapa de la red", "/topology"),
            ("Alarmas", "/events"), ("Vulnerabilidades", "/vulns"), ("Firmware y ciclo de vida", "/eol"),
            ("Informes", "/reports"), ("Auditoría", "/audit"), ("Solicitudes de cambio", "/changes"),
            ("Respaldo en curso (sondeo)", "/backup/job"), ("Sistema", "/system"), ("Capacidad", "/capacity")]


def time_pages(s: requests.Session) -> list[dict]:
    out = []
    for label, url in pages(s):
        times, code = [], 0
        for _ in range(4):
            t = time.time()
            r = s.get(B + url, timeout=600)
            times.append(time.time() - t)
            code = r.status_code
        out.append({"page": label, "url": url, "status": code, "first": round(times[0], 3),
                    "median": round(statistics.median(times[1:]), 3), "kb": round(len(r.content) / 1024)})
    return out


def concurrent(seconds: int = 20) -> dict:
    """Varios usuarios a la vez navegando (panel, lista, ficha, búsqueda) durante unos segundos."""
    urls = ["/", "/devices", "/devices?q=CARGA-0", "/devices/1", "/compliance"]
    sessions = [session() for _ in range(A.users)]  # el inicio de sesión (lento a propósito) no cuenta
    lat, per, stop = [], {}, time.time() + seconds

    def user(k):
        s = sessions[k]
        i = k
        while time.time() < stop:
            t = time.time()
            u = urls[i % len(urls)]
            s.get(B + u, timeout=600)
            lat.append(time.time() - t)
            per.setdefault(u, []).append(time.time() - t)
            i += 1
    with ThreadPoolExecutor(A.users) as ex:
        list(ex.map(user, range(A.users)))
    lat.sort()
    return {"users": A.users, "req_s": round(len(lat) / seconds, 1), "by_url": {u: round(statistics.median(v), 3) for u, v in per.items()},
            "p50": round(lat[len(lat) // 2], 3) if lat else None, "p95": round(lat[int(len(lat) * 0.95)], 3) if lat else None}


def db_info() -> dict:
    data = os.environ["NCM_DATA_DIR"]
    with SessionLocal() as db:
        nv = db.scalar(select(func.count(ConfigVersion.id)))
    db_files = [f for f in os.listdir(data) if f.startswith("opennccm.db")]
    return {"versions": nv, "db_mb": round(sum(os.path.getsize(os.path.join(data, f)) for f in db_files) / 2 ** 20, 1),
            "data_mb": round(dir_size(data) / 2 ** 20, 1)}


def md(results: list[dict], meta: dict) -> str:
    lines = [f"# Prueba de carga de OpenNCCM {meta['version']}", "",
             f"Servidor: {meta['cpus']} núcleos, {meta['ram_gb']} GB de RAM, {meta['os']}, Python {meta['python']}, "
             f"base de datos SQLite. Respaldos a la vez: {meta['concurrency']}.", ""]
    lines += ["| Equipos | Respaldar todos | Equipos/min | Fallos | CPU media | Memoria máx. | Base de datos | Versiones |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        b = r["backup"]
        if not b["secs"]:
            lines.append(f"| {r['devices']} | — | — | — | — | {r['rss_mb']:.0f} MB | {r['db']['db_mb']:.0f} MB | {r['db']['versions']} |")
            continue
        lines.append(f"| {r['devices']} | {b['secs'] / 60:.1f} min | {b['per_min']} | {b['err']} | {b['cpu_avg']} % | "
                     f"{b['rss_max_mb']:.0f} MB | {r['db']['db_mb']:.0f} MB | {r['db']['versions']} |")
    lines += ["", "Páginas (mediana en segundos; entre paréntesis, la primera vez):", ""]
    lines += ["| Página | " + " | ".join(str(r["devices"]) for r in results) + " |",
              "|---|" + "---:|" * len(results)]
    for i, p in enumerate(results[0]["pages"]):
        lines.append(f"| {p['page']} | " + " | ".join(
            f"{r['pages'][i]['median']} ({r['pages'][i]['first']})" if r["pages"][i]["status"] == 200
            else f"HTTP {r['pages'][i]['status']}" for r in results) + " |")
    lines += ["", "Varios usuarios a la vez:", ""]
    for r in results:
        c = r["concurrent"]
        lines.append(f"- {r['devices']} equipos: {c['users']} usuarios, {c['req_s']} páginas/s, p50 {c['p50']} s, p95 {c['p95']} s")
    return "\n".join(lines) + "\n"


def main():
    from app import __version__
    os.makedirs(A.out, exist_ok=True)
    meta = {"version": __version__, "date": datetime.now().isoformat(timespec="seconds"), "cpus": psutil.cpu_count() or 1,
            "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1), "os": platform.platform(terse=True),
            "python": platform.python_version(), "concurrency": A.concurrency, "history": A.history, "db": "sqlite"}
    log(f"Carpeta temporal {WORK}")
    start()
    creds, groups = setup_static()
    s = session()
    results, problems = [], []
    for n in STEPS:
        log(f"== {n} equipos")
        grow(n, creds, groups)
        b = backup_all(s) if not A.no_backup and n in BK_STEPS else {"secs": 0, "ok": 0, "err": 0, "changed": 0, "per_min": 0, "cpu_avg": 0, "cpu_max": 0, "rss_max_mb": rss_mb(), "errors": [], "cpus": psutil.cpu_count() or 1}
        log(f"  Respaldar todos: {b['secs']:.0f} s, {b['per_min']} equipos/min, {b['ok']} bien, {b['err']} con error")
        log(f"  Reintentos de GET por conexión cerrada (acumulado): {_Reintento.veces}")
        pg = time_pages(s)
        for p in pg:
            flag = " ← LENTA" if p["median"] > A.limit else ""
            log(f"  {p['page']:<28} {p['median']:>7.3f} s (1.ª {p['first']:.3f} s, {p['kb']} KB, HTTP {p['status']}){flag}")
            if p["median"] > A.limit:
                problems.append(f"{n} equipos: «{p['page']}» tarda {p['median']} s")
            if p["status"] >= 500:
                problems.append(f"{n} equipos: «{p['page']}» da HTTP {p['status']}")
        cc = concurrent()
        log(f"  {cc['users']} usuarios a la vez: {cc['req_s']} páginas/s, p95 {cc['p95']} s; mediana por página: {cc['by_url']}")
        if b["err"]:
            problems.append(f"{n} equipos: {b['err']} respaldos fallaron: {b['errors']}")
        results.append({"devices": n, "backup": b, "pages": pg, "concurrent": cc, "db": db_info(), "rss_mb": rss_mb()})
        with open(os.path.join(A.out, "capacidad.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "steps": results, "problems": problems}, f, ensure_ascii=False, indent=1)
        with open(os.path.join(A.out, "capacidad.md"), "w", encoding="utf-8") as f:
            f.write(md(results, meta))
    log("Resultados en " + A.out)
    if problems:
        log("CUELLOS DE BOTELLA:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        for p in procs:
            try:
                for c in psutil.Process(p.pid).children(recursive=True):
                    c.kill()
            except psutil.Error:
                pass
            p.kill()
        time.sleep(1)
        log_path = os.path.join(WORK, "app.log")
        if code and os.path.exists(log_path):
            shutil.copy(log_path, os.path.join(A.out, "app.log"))
        if not A.keep:
            shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(code)
