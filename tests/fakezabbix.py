"""Zabbix simulado (API JSON-RPC) para las pruebas: fakezabbix.py PUERTO VERSIÓN

Reproduce las diferencias entre versiones que importan a OpenNCCM: hasta 6.2 la autenticación va en «auth» y desde 6.4
en la cabecera Bearer (7.2 rechaza «auth»); hasta 6.0 los grupos se piden con «selectGroups» y desde 6.2 con
«selectHostGroups» (7.0 rechaza el antiguo). Token válido: «tok-ok»; usuario: Admin / zabbix.
"""
import json
import sys
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
            return self._send(rid, out)
        return bad(f"Unknown method {m}")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
