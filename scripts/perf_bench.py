"""Mide cuánto tardan las páginas con la base de scripts/perf_seed.py (mediana de 7) y lo que pesan por la red. Con
dos servidores (p. ej. la versión anterior en un puerto y la nueva en otro) los compara con la misma carga, alternando
las peticiones. Uso: python scripts/perf_bench.py PUERTO [PUERTO_ANTERIOR] [--json salida.json]"""
import json
import statistics
import sys
import time

import requests

PAGES = ["/", "/devices", "/devices?q=SW-01", "/devices/1000", "/events", "/compliance", "/vulns", "/firmware",
         "/topology", "/reports", "/audit", "/changes", "/search?q=SW-0999", "/ipam", "/analytics",
         "/api/mobile/panel", "/api/mobile/devlist", "/api/mobile/alarms", "/api/mobile/device/1000"]


def sess(port):
    b = f"http://127.0.0.1:{port}"
    s = requests.Session()
    s.post(b + "/login", data={"username": "admin", "password": "Admin12345"})
    tok = requests.post(b + "/api/mobile/login", json={"username": "admin", "password": "Admin12345"}).json().get("token")
    return b, s, {"Authorization": f"Bearer {tok}"}


args = [a for a in sys.argv[1:] if not a.startswith("--")]
out_json = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
if out_json in args:
    args.remove(out_json)
B = sess(int(args[0]))
A = sess(int(args[1])) if len(args) > 1 else None
out = {}
print(f"{'página':28s} {'antes':>8s} {'ahora':>8s}   KB por la red (antes → ahora)" if A else f"{'página':28s} {'ms':>8s}   KB por la red")
for p in PAGES:
    ta, tb, ka, kb = [], [], 0, 0
    for _ in range(7):
        for srv, ts in ((A, ta), (B, tb)):
            if srv is None:
                continue
            base, s, h = srv
            t = time.perf_counter()
            r = s.get(base + p, headers=h if p.startswith("/api/") else None, timeout=300)
            ts.append((time.perf_counter() - t) * 1000)
            kb_ = int(r.headers.get("content-length") or len(r.content)) // 1024
            if srv is A:
                ka = kb_
            else:
                kb = kb_
    b_ms = round(statistics.median(tb))
    if A:
        a_ms = round(statistics.median(ta))
        out[p] = {"antes_ms": a_ms, "ahora_ms": b_ms, "antes_kb": ka, "ahora_kb": kb}
        print(f"{p:28s} {a_ms:7d}ms {b_ms:7d}ms   {ka:5d} → {kb:5d}", flush=True)
    else:
        out[p] = {"ms": b_ms, "kb": kb}
        print(f"{p:28s} {b_ms:7d}ms   {kb:5d}", flush=True)
if out_json:
    json.dump(out, open(out_json, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
