from fastapi import Request
from sqlalchemy import select

from .db import Device, SessionLocal, User, UserGroup

ROLES = {"viewer": 0, "operator": 1, "admin": 2}


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    pass


def current_user(request: Request) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    with SessionLocal() as db:
        user = db.get(User, uid)
        if user is None or not user.active:
            request.session.clear()
            return None
        return user


def require(request: Request, role: str = "viewer") -> User:
    user = current_user(request)
    if user is None:
        raise LoginRequired()
    if ROLES.get(user.role, 0) < ROLES[role]:
        raise Forbidden()
    return user


def user_group_ids(user_id: int) -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(UserGroup.group_id).where(UserGroup.user_id == user_id)))


def device_scope(user: User) -> set[int] | None:
    """Equipos visibles para el usuario. None = todos (admin o usuario sin grupos asignados)."""
    if user.role == "admin":
        return None
    gids = user_group_ids(user.id)
    if not gids:
        return None
    with SessionLocal() as db:
        return set(db.scalars(select(Device.id).where(Device.group_id.in_(gids))))


def check_device(user: User, device_id: int) -> None:
    scope = device_scope(user)
    if scope is not None and device_id not in scope:
        raise Forbidden()


def filter_ids(user: User, ids) -> list[int]:
    scope = device_scope(user)
    return [i for i in ids if scope is None or i in scope]
