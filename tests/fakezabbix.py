"""Zabbix simulado (API JSON-RPC) para las pruebas: fakezabbix.py PUERTO VERSIÓN

Reproduce las diferencias entre versiones que importan a OpenNCCM: hasta 6.2 la autenticación va en «auth» y desde 6.4
en la cabecera Bearer (7.2 rechaza «auth»); hasta 6.0 los grupos se piden con «selectGroups» y desde 6.2 con
«selectHostGroups» (7.0 rechaza el antiguo). Token válido: «tok-ok»; usuario: Admin / zabbix.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, VERSION = int(sys.argv[1]), sys.argv[2]
V = tuple(int(x) for x in VERSION.split(".")[:2])
SESSIONS = {"tok-ok"}

HOSTS = [
    {"hostid": "10101", "host": "rt-borde", "name": "RT-Borde", "description": "Router de borde, sede central",
     "status": "0", "interfaces": [{"ip": "192.0.2.11", "dns": "", "useip": "1", "main": "1", "type": "2", "port": "161"}],
     "inventory": {"os": "RouterOS 7.16", "model": "CCR2004-1G-12S+2XS", "serialno_a": "HFD09ABC123", "location": "CPD"},
     "parentTemplates": [{"name": "Mikrotik by SNMP"}], "groups": [{"name": "Routers"}]},
    {"hostid": "10102", "host": "sw-core", "name": "SW-Core", "description": "Switch núcleo",
     "status": "0", "interfaces": [{"ip": "192.0.2.12", "dns": "", "useip": "1", "main": "1", "type": "2", "port": "161"}],
     "inventory": [], "parentTemplates": [{"name": "Cisco IOS by SNMP"}], "groups": [{"name": "Switches"}]},
    {"hostid": "10103", "host": "fw-dmz", "name": "FW-DMZ", "description": "",
     "status": "0", "interfaces": [{"ip": "", "dns": "fw-dmz.example.net", "useip": "0", "main": "1", "type": "1", "port": "10050"}],
     "inventory": {"os": "FortiOS v7.4.4"}, "parentTemplates": [], "groups": [{"name": "Firewalls"}, {"name": "DMZ"}]},
    {"hostid": "10104", "host": "srv-web", "name": "SRV-Web", "description": "Servidor web (Windows)",
     "status": "0", "interfaces": [{"ip": "192.0.2.14", "dns": "", "useip": "1", "main": "1", "type": "1", "port": "10050"}],
     "inventory": [], "parentTemplates": [{"name": "Windows by Zabbix agent"}], "groups": [{"name": "Servidores"}]},
    {"hostid": "10105", "host": "sw-viejo", "name": "SW-Viejo", "description": "Retirado",
     "status": "1", "interfaces": [{"ip": "192.0.2.15", "dns": "", "useip": "1", "main": "1", "type": "2", "port": "161"}],
     "inventory": [], "parentTemplates": [{"name": "HP Enterprise Switch by SNMP"}], "groups": [{"name": "Switches"}]},
    {"hostid": "10106", "host": "sensor", "name": "Sensor-Temp", "description": "Sin interfaz",
     "status": "0", "interfaces": [], "inventory": [], "parentTemplates": [], "groups": [{"name": "Sensores"}]},
    {"hostid": "10107", "host": "ya-existe", "name": "YaExiste-ZBX", "description": "Mismo nombre que un equipo de OpenNCCM",
     "status": "0", "interfaces": [{"ip": "192.0.2.17", "dns": "", "useip": "1", "main": "1", "type": "2", "port": "161"}],
     "inventory": [], "parentTemplates": [{"name": "Juniper by SNMP"}], "groups": [{"name": "Routers"}]},
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, rid, result=None, error=None):
        body = {"jsonrpc": "2.0", "id": rid}
        body["error" if error else "result"] = error or result
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/zabbix/api_jsonrpc.php":
            self.send_response(404)
            self.end_headers()
            return
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        m, p, rid = req.get("method"), req.get("params"), req.get("id")
        bad = lambda msg: self._send(rid, error={"code": -32602, "message": "Invalid params.", "data": msg})  # noqa: E731
        if m == "apiinfo.version":
            return bad("Should not contain auth.") if "auth" in req else self._send(rid, VERSION)
        if m == "user.login":
            if "username" not in p:
                return bad('Invalid parameter "/": unexpected parameter "user".')
            if (p["username"], p.get("password")) != ("Admin", "zabbix"):
                return bad("Incorrect user name or password or account is temporarily blocked.")
            SESSIONS.add("sess-1")
            return self._send(rid, "sess-1")
        # autenticación según la versión
        if "auth" in req and V >= (7, 2):
            return bad('Invalid parameter "/": unexpected parameter "auth".')
        hdr = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        tok = hdr if V >= (6, 4) else req.get("auth")
        if tok not in SESSIONS:
            return self._send(rid, error={"code": -32602, "message": "Invalid params.", "data": "Not authorized."})
        if m == "user.logout":
            SESSIONS.discard(tok)
            return self._send(rid, True)
        if m == "host.get":
            if V >= (7, 0) and "selectGroups" in p:
                return bad('Invalid parameter "/": unexpected parameter "selectGroups".')
            if V < (6, 2) and "selectHostGroups" in p:
                return bad('Invalid parameter "/": unexpected parameter "selectHostGroups".')
            out = []
            for h in HOSTS:
                h = dict(h)
                groups = h.pop("groups")
                h["hostgroups" if V >= (6, 2) else "groups"] = groups
                out.append(h)
            if p.get("countOutput"):
                return self._send(rid, str(len(HOSTS)))
            if p.get("hostids"):
                out = [h for h in out if h["hostid"] in p["hostids"]]
            if p.get("search"):
                words = [str(v).lower() for v in p["search"].values()]
                out = [h for h in out if any(w in h["host"].lower() or w in h["name"].lower() for w in words)]
            return self._send(rid, out)
        # correlación (app/zbxcorr.py): interfaces, problemas, métricas e historial de RT-Borde
        if m == "hostinterface.get":
            (field, val), = p["filter"].items()
            return self._send(rid, [{"hostid": h["hostid"]} for h in HOSTS for i in h["interfaces"] if i.get(field) == val])
        if m == "event.get":
            if "eventids" in p:  # eventos de resolución
                return self._send(rid, [{"eventid": e["eventid"], "clock": str(e["clock"])} for e in RECOVERY if e["eventid"] in p["eventids"]])
            for k in ("source", "object", "value", "time_from", "time_till"):
                if k not in p:
                    return bad(f"falta {k}")
            out = [e for e in EVENTS if e["hostid"] in p["hostids"] and p["time_from"] <= e["clock"] <= p["time_till"]]
            return self._send(rid, [_ev(e) for e in out])
        if m == "problem.get":
            return self._send(rid, [_ev(e) for e in EVENTS if e["hostid"] in p["hostids"] and e["r_eventid"] == "0"])
        if m == "item.get":
            vt = [str(x) for x in (p.get("filter") or {}).get("value_type", [0, 1, 2, 3, 4])]
            return self._send(rid, [dict(i) for i in ITEMS if i["hostid"] in p["hostids"] and i["value_type"] in vt])
        if m == "history.get":
            if "history" not in p:
                return bad("falta history")
            pts = HIST.get(p["itemids"][0], [])
            return self._send(rid, [{"clock": str(t), "value": str(v)} for t, v in pts if p["time_from"] <= t <= p["time_till"]])
        if m == "trend.get":
            pts = HIST.get(p["itemids"][0], [])
            return self._send(rid, [{"clock": str(t), "value_avg": str(v), "value_max": str(v)} for t, v in pts[::12]
                                    if p["time_from"] <= t <= p["time_till"]])
        return bad(f"Unknown method {m}")


def _ev(e):
    return {k: (str(v) if k == "clock" else v) for k, v in e.items() if k != "hostid"}


# problemas y métricas de RT-Borde (10101), relativos a cuando arranca el simulado; el resto de hosts no tiene
NOW = int(time.time())
EVENTS = [
    {"eventid": "9001", "hostid": "10101", "clock": NOW - 3 * 3600, "name": "Interface ether1(WAN): Link down",
     "severity": "4", "r_eventid": "9002", "objectid": "500", "acknowledged": "0"},
    {"eventid": "9003", "hostid": "10101", "clock": NOW - 1800, "name": "High CPU utilization (over 90% for 5m)",
     "severity": "3", "r_eventid": "0", "objectid": "501", "acknowledged": "1"},
    {"eventid": "9005", "hostid": "10101", "clock": NOW - 10 * 86400, "name": "Fan 2 failure", "severity": "2",
     "r_eventid": "0", "objectid": "502", "acknowledged": "0"},
]
RECOVERY = [{"eventid": "9002", "clock": NOW - 2 * 3600}]
ITEMS = [
    {"itemid": "700", "hostid": "10101", "key_": "icmppingsec", "name": "ICMP response time", "value_type": "0", "units": "s"},
    {"itemid": "701", "hostid": "10101", "key_": "system.cpu.util[hrProcessorLoad.1]", "name": "CPU utilization",
     "value_type": "3", "units": "%"},
    {"itemid": "702", "hostid": "10101", "key_": "system.descr", "name": "System description", "value_type": "4", "units": ""},
]
HIST = {"700": [(t, 0.002 + (0.05 if NOW - 3 * 3600 <= t <= NOW - 2 * 3600 else 0)) for t in range(NOW - 3 * 86400, NOW, 300)],
        "701": [(t, 95 if t >= NOW - 1800 else 20) for t in range(NOW - 3 * 86400, NOW, 300)]}


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
