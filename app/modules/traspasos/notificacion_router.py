"""Endpoints de notificaciones — CU29 Notificar estado del traspaso."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlmodel import func, select

from app.core.deps import AuthenticatedUser, SessionDep
from app.modules.identidad.models import RolNombre, Usuario
from app.modules.traspasos.models import Notificacion

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])


@router.get("", response_model=dict)
def listar_notificaciones(
    session: SessionDep,
    user: AuthenticatedUser,
    leidas: bool | None = Query(default=None, description="Filtrar solo leídas (True) o no leídas (False)"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Devuelve las notificaciones del usuario autenticado.

    Ordenadas por fecha descendente (las más recientes primero).
    Paginadas con `page` y `size`.
    Respuesta: { items: [...], total: N }.

    Acceso: Cualquier usuario autenticado (Cliente, Encargado, Administrador).
    """
    query = select(Notificacion).where(Notificacion.usuario_id == user.id)

    if leidas is not None:
        query = query.where(Notificacion.leida == leidas)

    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    offset = (page - 1) * size

    notificaciones = session.exec(
        query.order_by(Notificacion.fecha.desc())
        .offset(offset)
        .limit(size)
    ).all()

    items = [
        {
            "id": n.id,
            "usuario_id": n.usuario_id,
            "mensaje": n.mensaje,
            "tipo": n.tipo,
            "leida": n.leida,
            "codigo_traspaso": n.codigo_traspaso,
            "fecha": n.fecha.isoformat() if n.fecha else None,
        }
        for n in notificaciones
    ]

    return {"items": items, "total": total, "page": page, "size": size}


@router.patch("/leer-todas")
def marcar_todas_leidas(
    session: SessionDep,
    user: AuthenticatedUser,
):
    """Marca todas las notificaciones del usuario autenticado como leídas.

    Acceso: Cualquier usuario autenticado.
    """
    notificaciones = session.exec(
        select(Notificacion).where(
            Notificacion.usuario_id == user.id,
            Notificacion.leida == False,  # noqa: E712
        )
    ).all()

    for notif in notificaciones:
        notif.leida = True
        session.add(notif)

    session.commit()

    return {"marcadas": len(notificaciones), "mensaje": "Todas las notificaciones han sido marcadas como leídas"}


@router.patch("/{notificacion_id}/leer")
def marcar_leida(
    notificacion_id: int,
    session: SessionDep,
    user: AuthenticatedUser,
):
    """Marca una notificación específica como leída.

    Verifica que la notificación pertenezca al usuario autenticado.
    Si no pertenece, devuelve 403 Forbidden.

    Acceso: Cualquier usuario autenticado.
    """
    notificacion = session.exec(
        select(Notificacion).where(
            Notificacion.id == notificacion_id,
            Notificacion.usuario_id == user.id,
        )
    ).first()

    if notificacion is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación no encontrada o no pertenece a este usuario",
        )

    notificacion.leida = True
    session.add(notificacion)
    session.commit()
    session.refresh(notificacion)

    return {
        "id": notificacion.id,
        "leida": notificacion.leida,
        "mensaje": "Notificación marcada como leída",
    }