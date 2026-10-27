"""Importar equipos desde Zabbix (API JSON-RPC, versiones 6.0 a 7.x).

Se lee la lista de hosts (nombre visible, dirección de la interfaz principal, descripción, grupos, plantillas e
inventario) para crearlos como dispositivos. Las credenciales SSH no están en Zabbix: el administrador las asigna después.
Diferencias entre versiones que se tienen en cuenta:
  * Autenticación: desde 6.4 va en la cabecera «Authorization: Bearer»; antes, en el campo «auth» (que 7.2 ya no admite).
  * Grupos del host: «selectHostGroups»/«hostgroups» desde 6.2; antes «selectGroups»/«groups».
"""
from __future__ import annotations

import re

import requests

from .i18n import _, _f

TIMEOUT = 30


class ZabbixError(Exception):
    pass


def api_url(url: str) -> str:
    """Admite la dirección de la interfaz web («https://zbx/zabbix/», «…/zabbix.php») o la de la API."""
    url = (url or "").strip()
    if not re.match(r"(?i)^https?://", url):
        url = "https://" + url
    url = re.sub(r"(?i)/(index|zabbix)\.php.*$", "", url).rstrip("/")
    return url if url.lower().endswith("/api_jsonrpc.php") else url + "/api_jsonrpc.php"


def _version_tuple(v: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


class Client:
    def __init__(self, url: str, verify: bool = True):
        self.url, self.verify = api_url(url), verify
        self.auth: str | None = None
        self.session_login = False  # True si se entró con usuario y contraseña (hay que cerrar la sesión)
        self.version = ""
        self._id = 0

    def call(self, method: str, params, auth: bool = True):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self.auth:
            if _version_tuple(self.version) >= (6, 4):
                headers["Authorization"] = f"Bearer {self.auth}"
            else:
                body["auth"] = self.auth
        try:
            r = requests.post(self.url, json=body, headers=headers, timeout=TIMEOUT, verify=self.verify)
        except requests.exceptions.SSLError as e:
            raise ZabbixError(_("El certificado HTTPS de Zabbix no es de confianza. Desmarque «Verificar el certificado» si es autofirmado.")) from e
        except requests.RequestException as e:
            raise ZabbixError(_f("No se pudo conectar con {0}: {1}", self.url, str(e).splitlines()[0][:200])) from e
        if r.status_code != 200:
            raise ZabbixError(_f("Zabbix respondió HTTP {0} en {1}. ¿Es la dirección de la interfaz web de Zabbix?", r.status_code, self.url))
        try:
            data = r.json()
        except ValueError as e:
            raise ZabbixError(_f("La respuesta de {0} no es de la API de Zabbix.", self.url)) from e
        if "error" in data:
            err = data["error"]
            raise ZabbixError(f"{err.get('message', 'Error')}: {err.get('data', '')}"[:300])
        return data.get("result")

    def connect(self, token: str = "", username: str = "", password: str = "") -> None:
        self.version = str(self.call("apiinfo.version", {}, auth=False) or "")
        if _version_tuple(self.version) < (5, 4):
            raise ZabbixError(_f("Zabbix {0} no es compatible: se necesita la 5.4 o posterior.", self.version))
        if token:
            self.auth = token.strip()
        elif username:
            self.auth = self.call("user.login", {"username": username, "password": password}, auth=False)
            self.session_login = True
        else:
            raise ZabbixError(_("Falta el token de API o el usuario y la contraseña de Zabbix."))

    def close(self) -> None:
        if self.session_login and self.auth:
            try:
                self.call("user.logout", [])
            except ZabbixError:
                pass
        self.auth = None

    def hosts(self) -> list[dict]:
        new_groups = _version_tuple(self.version) >= (6, 2)
        params = {
            "output": ["hostid", "host", "name", "description", "status"],
            "selectInterfaces": ["ip", "dns", "useip", "main", "type", "port"],
            "selectInventory": ["os", "os_full", "os_short", "hardware", "hardware_full", "model", "vendor", "type",
                                "serialno_a", "location"],
            "selectParentTemplates": ["name"],
            ("selectHostGroups" if new_groups else "selectGroups"): ["name"],
            "sortfield": "name",
        }
        out = self.call("host.get", params) or []
        for h in out:
            h["groups"] = h.pop("hostgroups", None) or h.get("groups") or []
        return out


# plataforma probable a partir de las plantillas, el inventario y la descripción de Zabbix (el primero que coincide)
GUESS = [
    (r"mikrotik|routeros", "mikrotik_routeros"),
    (r"fortigate|fortinet|fortios", "fortinet"),
    (r"junos|juniper", "juniper_junos"),
    (r"nx-?os|nexus", "cisco_nxos"),
    (r"ios[- ]?xr", "cisco_xr"),
    (r"ios[- ]?xe|catalyst 9\d{3}", "cisco_xe"),
    (r"\basa\b|adaptive security", "cisco_asa"),
    (r"cisco.*\b(sg|sf|cbs)\s?-?\d{3}|small business", "cisco_s300"),
    (r"cisco", "cisco_ios"),
    (r"edgeswitch", "ubiquiti_edgeswitch"),
    (r"edgerouter|edgeos", "ubiquiti_edgerouter"),
    (r"unifi.*switch|usw-", "ubiquiti_unifiswitch"),
    (r"aos-?cx|arubaos-cx", "aruba_aoscx"),
    (r"procurve|hp enterprise switch|hpe? .*switch|aruba.*(2530|2540|2930|3810|5400)", "hp_procurve"),
    (r"comware|h3c|hh3c", "hp_comware"),
    (r"huawei", "huawei"),
    (r"\bexos\b|extremexos", "extreme_exos"),
    (r"\bvoss\b|fabric engine|\bvsp\b", "extreme_vsp"),
    (r"pfsense", "pfsense"),
    (r"opnsense", "opnsense"),
    (r"watchguard|fireware", "watchguard_fireware"),
    (r"palo ?alto|pan-?os", "paloalto_panos"),
    (r"arista", "arista_eos"),
    (r"vyos", "vyos"),
    (r"timetra|timos|nokia sr", "nokia_sros"),
    (r"dell.*os10", "dell_os10"),
    (r"force10|ftos|dell force", "dell_force10"),
    (r"tp-?link", "tplink_jetstream"),
    (r"d-?link|des[-_ ]?dgs", "dlink_ds"),
    (r"zyxel", "zyxel_os"),
    (r"sophos", "sophos_sfos"),
    (r"check ?point|gaia", "checkpoint_gaia"),
    (r"allied", "allied_telesis_awplus"),
    (r"ruckus|\bicx\b|fastiron", "ruckus_fastiron"),
    (r"netgear", "netgear_prosafe"),
    (r"ubuntu|debian", "ubuntu_server"),
    (r"alpine linux", "alpine_linux"),
    (r"linux", "linux"),
]


def guess_type(h: dict) -> str | None:
    inv = h.get("inventory") if isinstance(h.get("inventory"), dict) else {}
    text = " ".join([*(t.get("name", "") for t in h.get("parentTemplates") or []), *(str(v) for v in inv.values()),
                     h.get("description") or ""]).lower()
    return next((key for pat, key in GUESS if re.search(pat, text)), None)


def address(h: dict) -> str:
    """Dirección de la interfaz principal (agente, luego SNMP, luego cualquiera): IP o nombre DNS según «useip»."""
    ifs = h.get("interfaces") or []
    order = {"1": 0, "2": 1}  # 1 agente, 2 SNMP, 3 IPMI, 4 JMX
    ifs = sorted(ifs, key=lambda i: (str(i.get("main")) != "1", order.get(str(i.get("type")), 2)))
    for i in ifs:
        addr = i.get("ip") if str(i.get("useip", "1")) == "1" else i.get("dns")
        addr = (addr or i.get("ip") or i.get("dns") or "").strip()
        if addr and addr not in ("127.0.0.1", "::1"):
            return addr
    return ""


def rows(hosts: list[dict]) -> list[dict]:
    """Hosts de Zabbix como filas para la vista previa e importación."""
    out = []
    for h in hosts:
        inv = h.get("inventory") if isinstance(h.get("inventory"), dict) else {}
        out.append({
            "zid": str(h.get("hostid", "")),
            "name": (h.get("name") or h.get("host") or "").strip()[:128],
            "host": address(h)[:255],
            "description": (h.get("description") or "").strip()[:2000],
            "groups": [g.get("name", "") for g in h.get("groups") or []],
            "type": guess_type(h),
            "enabled": str(h.get("status", "0")) == "0",
            "model": (inv.get("model") or inv.get("hardware") or "").strip()[:128] or None,
            "serial": (inv.get("serialno_a") or "").strip()[:128] or None,
            "location": (inv.get("location") or "").strip()[:255] or None,
        })
    return out
