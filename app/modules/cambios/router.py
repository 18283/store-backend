"""Endpoints del módulo Cambios y Devoluciones — CU17-CU23."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import Rol, RolNombre, Usuario

from . import service
from .schemas import (
    NotaCambioOut,
    ResolverSolicitudIn,
    ResolverSolicitudOut,
    SolicitudCrearIn,
    SolicitudOut,
    SolicitudPage,
)

router = APIRouter(prefix="/cambios", tags=["cambios"])

ClienteUser = Annotated[Usuario, Depends(require_roles(RolNombre.CLIENTE))]
StaffUser = Annotated[
    Usuario,
    Depends(
        require_roles(
            RolNombre.ADMINISTRADOR, RolNombre.ENCARGADO, RolNombre.CAJERO
        )
    ),
]


def _sucursal_permitida(session: SessionDep, user: Usuario) -> int | None:
    """None = administrador, ve toda la empresa. Un id = Cajero/Encargado,
    forzado a la suya. Mismo patrón que inventario/reservas/ventas."""
    rol = session.get(Rol, user.rol_id)
    if rol is not None and rol.nombre in (RolNombre.ENCARGADO, RolNombre.CAJERO):
        if user.sucursal_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Tu cuenta no está vinculada a ninguna sucursal. Contactá al administrador.",
            )
        return user.sucursal_id
    return None


# --------------------------------------------------------------------------- #
#  CU18 — Solicitar cambio o devolución (Cliente)
# --------------------------------------------------------------------------- #
@router.post("", response_model=SolicitudOut, status_code=status.HTTP_201_CREATED)
def solicitar(data: SolicitudCrearIn, session: SessionDep, user: ClienteUser):
    return service.crear_solicitud(session, user, data)


@router.get("/mias", response_model=list[SolicitudOut])
def mis_solicitudes(session: SessionDep, user: ClienteUser):  # CU23
    return service.mis_solicitudes(session, user.id)


# --------------------------------------------------------------------------- #
#  CU23 — Historial de la sucursal (Cajero/Encargado/Administrador)
# --------------------------------------------------------------------------- #
@router.get("/sucursal", response_model=SolicitudPage)
def listar_sucursal(
    session: SessionDep,
    user: StaffUser,
    estado: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    sucursal_permitida = _sucursal_permitida(session, user)
    items, total = service.listar_sucursal(
        session,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
        estado=estado,
        page=page,
        size=size,
    )
    return SolicitudPage(items=items, total=total, page=page, size=size)


# --------------------------------------------------------------------------- #
#  CU21 — Confirmar recepción de la prenda devuelta
# --------------------------------------------------------------------------- #
@router.post("/{solicitud_id}/recepcionar", response_model=SolicitudOut)
def recepcionar(solicitud_id: int, session: SessionDep, user: StaffUser):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.recepcionar(
        session,
        user,
        solicitud_id,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )


# --------------------------------------------------------------------------- #
#  CU19/CU20 — Resolver (entregar la talla nueva, o devolver/reembolsar)
# --------------------------------------------------------------------------- #
@router.post("/{solicitud_id}/resolver", response_model=ResolverSolicitudOut)
def resolver(
    solicitud_id: int, data: ResolverSolicitudIn, session: SessionDep, user: StaffUser
):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.resolver(
        session,
        user,
        solicitud_id,
        data,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )


@router.post("/{solicitud_id}/confirmar-cobro", response_model=SolicitudOut)
def confirmar_cobro(solicitud_id: int, session: SessionDep, user: StaffUser):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.confirmar_cobro_diferencia(
        session,
        user,
        solicitud_id,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )


# --------------------------------------------------------------------------- #
#  CU22 — Nota de recepción / resolución
# --------------------------------------------------------------------------- #
@router.get("/{solicitud_id}/nota", response_model=NotaCambioOut)
def nota(solicitud_id: int, session: SessionDep, user: StaffUser):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.obtener_nota(
        session,
        solicitud_id,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )
