"""Análisis de impacto (app/impact.py) con una red de ejemplo armada con vecinos LLDP/CDP, sin base de datos ni red.

CORE-01 y CORE-02 enlazados; DIST-CENTRO y DIST-SUR con un uplink a cada núcleo; DIST-NORTE solo a CORE-01 y, detrás,
ACC-S-P2 y ACC-S-P3; R-SUC-ESTE (router de la sucursal) a CORE-01 y R-SUC-OESTE detrás; el firewall a los dos núcleos.
SW-SIN-DATOS no informa vecinos."""
import json
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import impact  # noqa: E402
from app.db import now  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


LINKS = [("CORE-01", "Gi1/0/1", "CORE-02", "Gi1/0/1"), ("CORE-01", "Gi1/0/2", "DIST-CENTRO", "Gi1/0/49"),
         ("CORE-02", "Gi1/0/2", "DIST-CENTRO", "Gi1/0/50"), ("CORE-01", "Gi1/0/3", "DIST-SUR", "Gi1/0/49"),
         ("CORE-02", "Gi1/0/3", "DIST-SUR", "Gi1/0/50"), ("CORE-01", "Gi1/0/4", "DIST-NORTE", "Gi1/0/49"),
         ("DIST-NORTE", "Gi1/0/1", "ACC-S-P2", "Gi1/0/49"), ("DIST-NORTE", "Gi1/0/2", "ACC-S-P3", "Gi1/0/49"),
         ("CORE-01", "Gi1/0/5", "R-SUC-ESTE", "Gi0/0/0"), ("R-SUC-ESTE", "Gi0/0/1", "R-SUC-OESTE", "Gi0/0/0"),
         ("CORE-01", "Gi1/0/9", "FW-CORE", "eth1"), ("CORE-02", "Gi1/0/9", "FW-CORE", "eth2")]
MODELS = {"R-SUC-ESTE": "ISR1111", "R-SUC-OESTE": "ISR1111", "FW-CORE": "Firebox T40"}
TYPES = {"FW-CORE": "watchguard_fireware"}
names = sorted({x for a, _p, b, _q in LINKS for x in (a, b)} | {"SW-SIN-DATOS"})
nb = {n: [] for n in names}
for a, pa, b, pb in LINKS:
    nb[a].append({"remote_name": b, "local_port": pa, "remote_port": pb, "platform": MODELS.get(b, "cisco WS-C9300")})
    nb[b].append({"remote_name": a, "local_port": pb, "remote_port": pa, "platform": MODELS.get(a, "cisco WS-C9300")})
devs = [SimpleNamespace(id=i + 1, name=n, host=f"10.0.0.{i + 1}", device_type=TYPES.get(n, "cisco_xe"),
                        model=MODELS.get(n, "C9300-48P"), location="Sucursal Este" if n.startswith("R-SUC") else "Sede",
                        neighbors=None if n == "SW-SIN-DATOS" else json.dumps(nb[n]),
                        last_backup_at=now() - timedelta(minutes=12)) for i, n in enumerate(names)]
ID = {d.name: d.id for d in devs}
g = impact.Graph(devs)

r = impact.analyze(g, [ID["CORE-01"]])
red = sorted(x["name"] for x in r["isolated"])
amber = sorted(x["name"] for x in r["branches"])
check("reiniciar CORE-01: quedan aislados DIST-NORTE, ACC-S-P2 y ACC-S-P3", red == ["ACC-S-P2", "ACC-S-P3", "DIST-NORTE"], red)
check("…y se cortan los enlaces a la sucursal (R-SUC-ESTE y R-SUC-OESTE)", amber == ["R-SUC-ESTE", "R-SUC-OESTE"], amber)
check("…los que tienen dos uplinks siguen por otro camino", {"CORE-02", "DIST-CENTRO", "DIST-SUR"} <= set(r["kept"]), r["kept"])
check("…impacto alto con la frase de la solicitud", r["level"] == "high" and r["sentence"] ==
      "Si reinicias CORE-01 quedan aislados 3 equipos y se cortan 2 enlaces a routers o sucursales", r["sentence"])
check("…de quién cuelga cada uno («detrás de DIST-NORTE»)",
      {x["name"]: x["via"] for x in r["isolated"]}["ACC-S-P2"] == "DIST-NORTE", r["isolated"])
check("…se dice lo que no se sabe: el equipo que no informa vecinos y la hora de los datos",
      r["nodata"] == ["SW-SIN-DATOS"] and r["age"] == "hace 12 min", (r["nodata"], r["age"]))
check("…mini-mapa con CORE-01 en rojo y los que siguen en verde", "<svg" in r["svg"] and "SE REINICIA" in r["svg"]
      and 'class="in green"' in r["svg"] and 'class="in amber"' in r["svg"] and 'class="in sub"' in r["svg"])
r = impact.analyze(g, [ID["DIST-SUR"]])
check("reiniciar DIST-SUR (dos uplinks y nada detrás): sin impacto", r["level"] == "none" and not r["isolated"], r["sentence"])
r = impact.analyze(g, [ID["DIST-NORTE"]])
check("reiniciar DIST-NORTE: aísla a sus 2 switches (impacto medio)", r["level"] == "medium"
      and sorted(x["name"] for x in r["isolated"]) == ["ACC-S-P2", "ACC-S-P3"], r)
r = impact.analyze(g, [ID["SW-SIN-DATOS"]])
check("un equipo sin vecinos conocidos: impacto desconocido, no «sin impacto»", r["level"] == "nodata", r["sentence"])
r = impact.analyze(g, [], {ID["CORE-01"]: ["GigabitEthernet1/0/4"]})
check("apagar el puerto de CORE-01 hacia DIST-NORTE: aísla DIST-NORTE y sus switches",
      sorted(x["name"] for x in r["isolated"]) == ["ACC-S-P2", "ACC-S-P3", "DIST-NORTE"] and r["sentence"].startswith("Si apagas este puerto"), r)
r = impact.analyze(g, [], {ID["CORE-01"]: ["Gi1/0/20", "Gi1/0/21"]})
check("apagar puertos sin enlace: sin impacto", r["level"] == "none", r)
ws = impact.waves(g, [ID["CORE-02"], ID["DIST-SUR"], ID["CORE-01"], ID["DIST-NORTE"]])
flat = [x for w in ws for x in w]
check("orden de firmware: primero los que no aíslan a nadie, CORE-01 y DIST-NORTE nunca juntos",
      flat.index(ID["CORE-01"]) > flat.index(ID["DIST-SUR"]) and flat.index(ID["CORE-01"]) > flat.index(ID["CORE-02"])
      and not any(ID["CORE-01"] in w and ID["DIST-NORTE"] in w for w in ws), [[g.nodes[f"d{x}"]["name"] for x in w] for w in ws])
r = impact.analyze(g, [ID["CORE-02"]])
check("reiniciar CORE-02: sin impacto (todo tiene otro camino por CORE-01)", r["level"] == "none", r["sentence"])
check("CORE-01 y CORE-02 nunca juntos (juntos se cae todo)", not any(ID["CORE-01"] in w and ID["CORE-02"] in w for w in ws), ws)
check("órdenes que reinician", all(impact.REBOOT.search(c) for c in ("reload in 1", "/system reboot", "request system reboot"))
      and not impact.REBOOT.search("interface Gi1/0/1\n shutdown"))
check("puertos que se apagan en comandos tipo Cisco",
      impact.interfaces_shut("interface range Gi1/0/12, Gi1/0/17\n description SIN-USO\n shutdown\ninterface Gi1/0/2\n no shutdown")
      == ["Gi1/0/12", "Gi1/0/17"])
print("\nRESULTADO:", "TODO OK" if not fails else f"{len(fails)} FALLOS")
sys.exit(1 if fails else 0)
