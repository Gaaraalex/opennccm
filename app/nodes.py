"""Nodos de la red (Administración › Nodos): se crean aquí o al dar de alta o editar un equipo, se asignan a los equipos
como las credenciales y filtran el Mapa de la red («Nodo: Todos» o uno)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from .auth import require
from .db import Device, NetNode, SessionLocal
from .i18n import _f
from .web import flash, go, log_action, render

router = APIRouter()


@router.get("/nodes")
def page(request: Request):
    user = require(request, "admin")
    with SessionLocal() as db:
        nodes = db.scalars(select(NetNode).order_by(NetNode.name)).all()
        counts = dict(db.execute(select(Device.node_id, func.count(Device.id)).group_by(Device.node_id)).all())
    return render(request, "nodes.html", user, nodes=nodes, counts=counts)


def _clean(form) -> tuple[str, str]:
    return (form.get("name") or "").strip()[:100], (form.get("description") or "").strip()[:255]


@router.post("/nodes")
async def create(request: Request):
    user = require(request, "admin")
    name, desc = _clean(await request.form())
    if not name:
        return go("/nodes")
    with SessionLocal() as db:
        if any(n.name.lower() == name.lower() for n in db.scalars(select(NetNode))):
            flash(request, _f("Ya hay un nodo «{0}».", name), "error")
            return go("/nodes")
        db.add(NetNode(name=name, description=desc or None))
        db.commit()
    log_action(request, user, "node_create", name)
    flash(request, _f("Nodo «{0}» creado.", name))
    return go("/nodes")


@router.post("/nodes/{nid}/edit")
async def edit(request: Request, nid: int):
    user = require(request, "admin")
    name, desc = _clean(await request.form())
    with SessionLocal() as db:
        n = db.get(NetNode, nid)
        if n is None or not name:
            return go("/nodes")
        if any(x.id != nid and x.name.lower() == name.lower() for x in db.scalars(select(NetNode))):
            flash(request, _f("Ya hay un nodo «{0}».", name), "error")
            return go("/nodes")
        old, n.name, n.description = n.name, name, desc or None
        db.commit()
    log_action(request, user, "node_edit", f"{old} → {name}")
    flash(request, _f("Nodo «{0}» guardado.", name))
    return go("/nodes")


@router.post("/nodes/{nid}/delete")
def delete(request: Request, nid: int):
    user = require(request, "admin")
    with SessionLocal() as db:
        n = db.get(NetNode, nid)
        if n is None:
            return go("/nodes")
        name = n.name
        for d in db.scalars(select(Device).where(Device.node_id == nid)):
            d.node_id = None
        db.delete(n)
        db.commit()
    log_action(request, user, "node_delete", name)
    flash(request, _f("Nodo «{0}» eliminado: sus equipos quedaron sin nodo.", name))
    return go("/nodes")
