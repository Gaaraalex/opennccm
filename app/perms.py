"""Permisos por usuario: qué funciones del sistema puede usar cada uno.

Cada usuario tiene un rol (Lector, Operador, Administrador) que sirve de plantilla, y puede tener una lista propia de
funciones (User.perms, JSON). Sin lista propia, tiene las de su rol: exactamente lo de siempre (cada función tiene el
rol mínimo que la usaba antes de 2.39, ver ROLE_MIN). Ver equipos, configuraciones e informes lo puede todo usuario
activo; lo demás es una función que se da o se quita.

Administrador maestro (User.is_master): tiene todas las funciones, nadie lo puede borrar, desactivar ni cambiarle los
permisos, y es el único que da o quita las funciones críticas (CRITICAL) a los demás. Solo él pasa el rol de maestro a
otro usuario; si se pierde el acceso, se recupera en el servidor con «opennccm set-master USUARIO».

Las rutas piden un rol con auth.require(request, "operator"/"admin"); require() traduce la ruta a su función con
ROUTES (la primera expresión que coincide) y comprueba esa función. tests/unit_perms.py comprueba que toda ruta que
pide un rol tiene su función y que, sin lista propia, cada rol puede exactamente lo mismo que antes.
"""
import json
import re

from .i18n import _

# (clave, sección, nombre, crítica, rol mínimo de antes)
CATALOG = [
    ("backup", "Operación", "Respaldar equipos", False, "operator"),
    ("audit", "Operación", "Auditar: cumplimiento, vulnerabilidades, vecinos y firmware disponible", False, "operator"),
    ("versions", "Operación", "Etiquetar versiones y marcar la línea base", False, "operator"),
    ("events", "Operación", "Reconocer alarmas", False, "operator"),
    ("push", "Cambios en los equipos", "Enviar configuraciones a los equipos (solicitudes, recetas, configlets, "
     "multi-equipo, correcciones, apagar puertos)", True, "operator"),
    ("rollback", "Cambios en los equipos", "Volver a una versión anterior del mismo equipo", True, "operator"),
    ("restore", "Cambios en los equipos", "Restaurar una copia (en el mismo equipo o en otro)", True, "admin"),
    ("console_ro", "Cambios en los equipos", "Consola: solo comandos de consulta", False, "console"),
    ("console_rw", "Cambios en los equipos", "Consola: también comandos que cambian el equipo", True, "console"),
    ("firmware_install", "Cambios en los equipos", "Actualizar el firmware de los equipos", True, "operator"),
    ("approve", "Cambios en los equipos", "Aprobar o rechazar solicitudes y pedidos de consola", True, "admin"),
    ("autofix", "Cambios en los equipos", "Corrección automática de cumplimiento: activarla, configurarla y pausarla",
     True, "admin"),
    ("devices", "Administración", "Agregar, editar, importar, retirar y borrar equipos; grupos y mapa", False, "admin"),
    ("credentials", "Administración", "Credenciales: verlas, crearlas y ver sus contraseñas", True, "admin"),
    ("configlets", "Administración", "Configlets, recetas verificadas y políticas de cumplimiento", False, "admin"),
    ("schedules", "Administración", "Tareas programadas", False, "admin"),
    ("firmware_manage", "Administración", "Imágenes de firmware: subir, descargar y borrar", True, "admin"),
    ("kb", "Administración", "Moderar la base de conocimiento", False, "admin"),
    ("users", "Administración", "Usuarios y registro de auditoría", True, "admin"),
    ("settings", "Administración", "Ajustes, sistema, nodos y ciclo de vida", True, "admin"),
]
KEYS = [c[0] for c in CATALOG]
CRITICAL = {c[0] for c in CATALOG if c[3]}
ROLE_MIN = {c[0]: c[4] for c in CATALOG}
_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}

# ruta (sin el método) -> función. La primera que coincide; se comprueban en orden.
ROUTES = [
    (r"^/(api/mobile/)?devices/\d+/backup$|^/api/devices/\d+/backup$|^/devices/\d+/mikrotik-backup$|^/backup/(all|job/(cancel|retry))$", "backup"),
    (r"^/versions/\d+/(label|baseline)$", "versions"),
    (r"^/versions/\d+/rollback$", "rollback"),
    (r"^/versions/\d+/restore|^/restore/", "restore"),
    (r"^/(api/mobile/)?events(/\d+)?/(un)?ack$", "events"),
    (r"^/(api/mobile/)?changes/\d+/review$|^/console/access/\d+/\w+$", "approve"),
    (r"^/configlets/\d+/run$|^/console/(change-request|files/\d+/put|recipes(/\d+/delete)?)$|^/compliance/remediate"
     r"|^/config-reference/request/|^/reports/unused/shutdown$|^/neighbors/\d+/shutdown$", "push"),
    (r"^/compliance/autofix/", "autofix"),
    (r"^/firmware/(upgrade|routeros-online)$", "firmware_install"),
    (r"^/firmware/(new|upload|\d+/delete|updates/\d+/download)$", "firmware_manage"),
    (r"^/devices/\d+/(compliance|cve)$|^/cve/all$|^/compliance/run$|^/api/where/refresh$|^/neighbors"
     r"|^/reports/(unused/(preview|except)|netdoc)|^/firmware/updates/check$|^/config-reference/", "audit"),
    (r"^/console/recipes/verify$|^/configlets/|^/compliance/(policies|rules)", "configlets"),
    (r"^/schedules", "schedules"),
    # base de conocimiento: solo la moderación (escribir casos es de operadores: kb._write, sin función propia)
    (r"^/kb/(pending/\d+/dismiss|\d+/(approve|return|archive|restore)|proposals/\d+/(approve|return)|access)$", "kb"),
    (r"^/credentials", "credentials"),
    (r"^/users|^/audit$", "users"),
    (r"^/devices/(new|import)|^/devices/\d+/(edit|delete|retire|reinstate)$|^/groups|^/api/devices$|^/api/mobile/newdevice$"
     r"|^/topology/plan$|^/api/topology/(sites|plan-pos)$", "devices"),
    (r"^/settings|^/system|^/nodes|^/eol|^/firmware/updates/settings$|^/reports/email$|^/console/access$"
     r"|^/api/mobile/admin$", "settings"),
]
_ROUTES = [(re.compile(p), k) for p, k in ROUTES]


def for_path(path: str) -> str | None:
    for rx, key in _ROUTES:
        if rx.search(path):
            return key
    return None


def _console_default(user) -> set[str]:
    """Consola de un usuario sin lista propia: la que da su rol en Operar › Consola › Acceso (como siempre)."""
    from .console import role_mode
    mode = role_mode(user.role)
    return {"console_ro", "console_rw"} if mode == "rw" else {"console_ro"} if mode == "ro" else set()


def role_defaults(role: str) -> set[str]:
    """Funciones del rol (sin la consola, que depende de los Ajustes)."""
    lvl = _LEVEL.get(role, 0)
    return {k for k, r in ROLE_MIN.items() if r != "console" and _LEVEL[r] <= lvl}


def custom(user) -> set[str] | None:
    """La lista propia del usuario, o None si usa la de su rol."""
    raw = getattr(user, "perms", None)
    if not raw:
        return None
    try:
        return {k for k in json.loads(raw) if k in KEYS}
    except (ValueError, TypeError):
        return None


def of(user) -> set[str]:
    if user is None:
        return set()
    if getattr(user, "is_master", False):
        return set(KEYS)
    own = custom(user)
    return own if own is not None else role_defaults(user.role) | _console_default(user)


def has(user, key: str) -> bool:
    return key in of(user)


def admin_any(user) -> bool:
    """¿Ve el menú Administración? (alguna de sus pestañas)"""
    p = of(user)
    return bool(p & {"users", "credentials", "devices", "settings"})


def console_mode(user) -> str:
    """none | ro | rw para la consola web."""
    p = of(user)
    return "rw" if "console_rw" in p else "ro" if "console_ro" in p else "none"


def encode(keys, role: str) -> str | None:
    """Lo que se guarda: None si es justo lo del rol (así sigue los cambios de la consola en Ajustes)."""
    keys = {k for k in keys if k in KEYS}
    from types import SimpleNamespace
    if keys == role_defaults(role) | _console_default(SimpleNamespace(role=role)):
        return None
    return json.dumps(sorted(keys))


def catalog_view() -> list[dict]:
    """Secciones con sus funciones, para la pantalla de usuarios (traducidas)."""
    out: list[dict] = []
    for key, sec, label, crit, _r in CATALOG:
        if not out or out[-1]["name"] != _(sec):
            out.append({"name": _(sec), "items": []})
        out[-1]["items"].append({"key": key, "label": _(label), "critical": crit})
    return out


def defaults_by_role() -> dict[str, list[str]]:
    from types import SimpleNamespace
    return {r: sorted(role_defaults(r) | _console_default(SimpleNamespace(role=r))) for r in _LEVEL}


def from_form(actor, target, role: str, submitted, is_new: bool) -> str | None:
    """Lo que se guarda en User.perms al crear o editar un usuario desde la pantalla de usuarios.

    El maestro decide todo. Cualquier otro con la función «usuarios» decide las funciones normales, pero no las críticas:
    a un usuario nuevo no le puede dar ninguna y a uno existente se las deja como estaban."""
    want = {k for k in submitted if k in KEYS}
    if not getattr(actor, "is_master", False):
        before = set() if is_new else of(target)
        want = (want - CRITICAL) | (before & CRITICAL)
    return encode(want, role)
