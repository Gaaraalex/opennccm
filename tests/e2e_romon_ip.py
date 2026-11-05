"""Mapa: los MikroTik encontrados por RoMON (que no informa IP) toman la IP que anuncian por MNDP/LLDP a otro equipo o la de
las tablas ARP guardadas; si no hay ninguna, queda la MAC y se explica por qué."""
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.db import Device, MacSeen, SessionLocal, now  # noqa: E402

B = "http://127.0.0.1:8000"
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def rm(name, mac, hops=2, via=""):
    return {"local_port": "", "remote_name": name, "remote_port": "", "remote_ip": "", "mac": mac, "platform": "MikroTik RB760iGS",
            "romon": True, "hops": hops, "version": "7.20", "via": via, "proto": "romon"}


with SessionLocal() as db:
    agent = Device(name="R-AGENTE-RM", host="192.0.2.31", device_type="mikrotik_routeros")
    other = Device(name="R-OTRO-RM", host="192.0.2.32", device_type="mikrotik_routeros")
    db.add_all([agent, other])
    db.flush()
    agent.neighbors = json.dumps([rm("RM-MNDP", "04:F4:1C:00:00:01", 1), rm("RM-ARP", "04:F4:1C:00:00:02", 1),
                                  rm("RM-NADA", "04:F4:1C:00:00:03", 1)])
    other.neighbors = json.dumps([{"local_port": "ether3", "remote_name": "RM-MNDP", "remote_port": "ether1", "remote_ip": "10.77.0.11",
                                   "mac": "04:F4:1C:00:00:01", "platform": "MikroTik", "proto": "mndp"}])
    other.tables_at = now()
    db.add(MacSeen(device_id=other.id, kind="arp", mac="04F41C000002", ip="10.77.0.22", port="bridge", first_seen=now(),
                   last_seen=now()))
    db.commit()
    ids = (agent.id, other.id)

s = requests.Session()
s.post(B + "/login", data={"username": "admin", "password": "Admin12345"})
t = s.get(B + "/topology").text
m = re.search(r"const NODES = (\[.*?\]), LINKS", t, re.S)
nodes = {n["label"]: n for n in json.loads(m.group(1))} if m else {}
check("RoMON: la IP que anuncia por MNDP a otro equipo", nodes.get("RM-MNDP", {}).get("ip") == "10.77.0.11", nodes.get("RM-MNDP"))
check("RoMON: la IP de la tabla ARP guardada", nodes.get("RM-ARP", {}).get("ip") == "10.77.0.22"
      and nodes["RM-ARP"]["sub"] == "10.77.0.22" and "tabla ARP de R-OTRO-RM" in nodes["RM-ARP"].get("how", ""), nodes.get("RM-ARP"))
check("RoMON sin IP en ningún lado: queda la MAC y se explica", nodes.get("RM-NADA", {}).get("ip") == ""
      and nodes["RM-NADA"]["sub"] == "04:F4:1C:00:00:03" and "RoMON no informa la IP" in nodes["RM-NADA"].get("how", ""), nodes.get("RM-NADA"))
with SessionLocal() as db:
    for i in ids:
        db.delete(db.get(Device, i))
    db.commit()
print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
