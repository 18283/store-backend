"""Endpoints del centro de notificaciones — CU35 (consulta + difusión masiva).

Ya existían de CU29: GET /notificaciones, PATCH /notificaciones/leer-todas
y PATCH /notificaciones/{id}/leer. CU35 agrega:
- campo `modulo` en la respuesta (traspasos, reservas, cambios,
  sincronizacion, suscripcion, masiva),
- filtro opcional `modulo`,
- POST /notificaciones/masiva solo Administrador -> {enviados: N}.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import func, select

from app.core.deps import AuthenticatedUser, SessionDep, require_roles
from app.modules.identidad.models import RolNombre, Usuario
from app.modules.traspasos.models import ModuloNotificacion, Notificacion
from app.modules.traspasos.notificacion_service import (
    crear_notificacion_para_roles,
    normalizar_rol,
)

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])

AdminUser = Annotated[Usuario, Depends(require_roles(RolNombre.ADMINISTRADOR))]

TIPOS_NOTIFICACION = ("info", "success", "warning", "promo")


class NotificacionMasivaIn(BaseModel):
    mensaje: str = Field(min_length=1, max_length=500, description="Texto del aviso de difusión")
    tipo: str = Field(default="info", max_length=20, description="Tipo visual: info|success|warning|promo")
    roles_destino: list[str] = Field(
        min_length=1,
        description="Roles destinatarios, ej.: ['CLIENTE', 'ENCARGADO'] o ['Cliente']",
    )


@router.get("", response_model=dict)
def listar_notificaciones(
    session: SessionDep,
    user: AuthenticatedUser,
    leidas: bool | None = Query(default=None, description="Filtrar solo leídas (True) o no leídas (False)"),
    modulo: str | None = Query(default=None, description="Filtrar por módulo: traspasos|reservas|cambios|sincronizacion|suscripcion|masiva"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Devuelve las notificaciones del usuario autenticado.

    Ordenadas por fecha descendente (las más recientes primero).
    Paginadas con `page` y `size`.
    Respuesta: { items: [...], total: N }.
    Acceso: Cualquier usuario autenticado (Cliente, Encargado, Administrador, Cajero).
    """
    query = select(Notificacion).where(Notificacion.usuario_id == user.id)

    if leidas is not None:
        query = query.where(Notificacion.leida == leidas)
    if modulo is not None:
        query = query.where(Notificacion.modulo == modulo.strip())

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
            "modulo": n.modulo,
            "fecha": n.fecha.isoformat() if n.fecha else None,
        }
        for n in notificaciones
    ]

    return {"items": items, "total": total, "page": page, "size": size}


@router.post("/masiva", status_code=status.HTTP_201_CREATED)
def enviar_masiva(
    data: NotificacionMasivaIn,
    session: SessionDep,
    admin: AdminUser,
):
    """CU35 — Difusión del Administrador a roles de su empresa.

    Recibe { mensaje, tipo, roles_destino } y crea una Notificacion por
    cada usuario activo con esos roles dentro del empresa_id del Admin.
    Devuelve { enviados: N } (A7: N=0 si no hay destinatarios).
    """
    mensaje = data.mensaje.strip()
    if not mensaje:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El mensaje no puede estar vacío",
        )
    if data.tipo not in TIPOS_NOTIFICACION:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Tipo inválido: {data.tipo!r}. Use uno de: {list(TIPOS_NOTIFICACION)}",
        )
    if not data.roles_destino:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="roles_destino no puede estar vacío",
        )
    # Valida cada rol (422 si alguno es inválido) antes de crear nada.
    roles_validados = [normalizar_rol(r) for r in data.roles_destino]
    if admin.empresa_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El usuario no está asociado a una empresa",
        )

    notificaciones = crear_notificacion_para_roles(
        session=session,
        roles=roles_validados,
        mensaje=mensaje,
        tipo=data.tipo,
        empresa_id=admin.empresa_id,
        modulo=ModuloNotificacion.MASIVA,
    )
    return {"enviados": len(notificaciones), "mensaje": "Difusión enviada"}


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
    Si no pertenece, devuelve 404 Not Found.

    Acceso: Cualquier usuario autenticado.
    """
    notificacion = session.exec(
        select(Notificacion).where(
            Notificacion.id == notificacion_id,
            Notificacion.usuario_id == user.id,
        )
    ).first()

    if notificacion is None:
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
