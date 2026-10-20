"""Prepara Keycloak para la prueba: dominio «empresa», cliente confidencial «opennccm» con el grupo en el token,
el grupo NCCM-Admins y dos usuarios (uno en el grupo y otro sin grupo)."""
import sys

import requests

K = "http://127.0.0.1:8080"
tok = requests.post(K + "/realms/master/protocol/openid-connect/token",
                    data={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "admin"},
                    timeout=30).json()["access_token"]
H = {"Authorization": "Bearer " + tok}


def call(method, path, **kw):
    r = requests.request(method, K + "/admin/realms" + path, headers=H, timeout=30, **kw)
    if r.status_code >= 300:
        print("Keycloak", method, path, r.status_code, r.text[:300])
        sys.exit(1)
    return r


requests.post(K + "/admin/realms", headers=H, json={"realm": "empresa", "enabled": True}, timeout=30).raise_for_status()
call("POST", "/empresa/clients", json={
    "clientId": "opennccm", "enabled": True, "publicClient": False, "secret": "Secreto-Keycloak-2026",
    "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
    "redirectUris": ["http://127.0.0.1:8000/login/oidc/callback"], "webOrigins": ["http://127.0.0.1:8000"],
    "attributes": {"pkce.code.challenge.method": "S256"},
    "protocolMappers": [{"name": "groups", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
                         "config": {"claim.name": "groups", "full.path": "false", "id.token.claim": "true",
                                    "access.token.claim": "true", "userinfo.token.claim": "true"}}]})
call("POST", "/empresa/groups", json={"name": "NCCM-Admins"})
gid = call("GET", "/empresa/groups", params={"search": "NCCM-Admins"}).json()[0]["id"]
for user, mail, pw, in_group in [("carla", "carla@empresa.local", "Clave-Carla-2026", True),
                                 ("pablo", "pablo@empresa.local", "Clave-Pablo-2026", False)]:
    call("POST", "/empresa/users", json={"username": user, "email": mail, "emailVerified": True, "enabled": True,
                                        "firstName": user.title(), "lastName": "Prueba",
                                        "credentials": [{"type": "password", "value": pw, "temporary": False}]})
    uid = call("GET", "/empresa/users", params={"username": user, "exact": "true"}).json()[0]["id"]
    if in_group:
        call("PUT", f"/empresa/users/{uid}/groups/{gid}")
print("OK Keycloak con dominio, cliente, grupo y usuarios")
