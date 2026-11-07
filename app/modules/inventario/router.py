"""Endpoints del módulo Inventario — CU13 (consultar) y CU14 (movimientos)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.inventario import service
from app.modules.inventario.schemas import (
    AlertasReposicionPage,
    InventarioAjuste,
    InventarioOut,
    InventarioPage,
    MovimientoInventarioCreate,
    MovimientoInventarioOut,
    MovimientoInventarioPage,
)

router = APIRouter(prefix="/inventario", tags=["inventario"])

AdminOEncargado = Annotated[
    Usuario, Depends(require_roles(RolNombre.ADMINISTRADOR, RolNombre.ENCARGADO))
]


def _sucursal_permitida(session: SessionDep, user: Usuario) -> int | None:
    """None = puede operar en cualquier sucursal (admin).
    Un id = solo puede operar en esa sucursal (encargado)."""
    rol = session.get(Rol, user.rol_id)
    if rol is not None and rol.nombre == RolNombre.ENCARGADO:
        if user.sucursal_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Tu cuenta no está vinculada a ninguna sucursal. Contactá al administrador.",
            )
        return user.sucursal_id
    return None


@router.get("/alertas", response_model=AlertasReposicionPage)
def alertas_reposicion(  # CU36
    session: SessionDep,
    user: AdminOEncargado,
    sucursal_id: int | None = None,
    umbral: int = Query(default=3, ge=0, le=1000),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    horizonte_dias: int = Query(default=30, ge=1, le=365),
    q: str | None = Query(default=None, description="Busca por producto o SKU"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """CU36 — Alertas de stock bajo y sugerencias de reposición (solo lectura).

    Admin: todas las sucursales de su empresa o una elegida. Encargado:
    solo su sucursal (409 si no tiene una). Sin historial de ventas se
    muestra solo la alerta por umbral (A2).
    """
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.consultar_alertas(
        session,
        sucursal_id=sucursal_id,
        umbral=umbral,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        horizonte_dias=horizonte_dias,
        q=q,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
        page=page,
        size=size,
    )


@router.get("", response_model=InventarioPage)
def listar(  # CU13
    session: SessionDep,
    user: AdminOEncargado,
    sucursal_id: int | None = None,
    q: str | None = Query(default=None, description="Busca por producto o SKU"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    sucursal_permitida = _sucursal_permitida(session, user)
    items, total = service.list_inventario(
        session,
        sucursal_id=sucursal_id,
        q=q,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
        page=page,
        size=size,
    )
    return InventarioPage(items=items, total=total, page=page, size=size)


@router.post("/ajustar", response_model=InventarioOut)
def ajustar(  # CU13
    data: InventarioAjuste, session: SessionDep, user: AdminOEncargado
):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.ajustar_stock(
        session,
        data,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )


@router.get("/movimientos", response_model=MovimientoInventarioPage)
def listar_movimientos(  # CU14
    session: SessionDep,
    user: AdminOEncargado,
    sucursal_id: int | None = None,
    variante_id: int | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    sucursal_permitida = _sucursal_permitida(session, user)
    items, total = service.list_movimientos(
        session,
        sucursal_id=sucursal_id,
        variante_id=variante_id,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
        page=page,
        size=size,
    )
    return MovimientoInventarioPage(items=items, total=total, page=page, size=size)


@router.post(
    "/movimientos",
    response_model=MovimientoInventarioOut,
    status_code=status.HTTP_201_CREATED,
)
def registrar_movimiento(  # CU14
    data: MovimientoInventarioCreate, session: SessionDep, user: AdminOEncargado
):
    sucursal_permitida = _sucursal_permitida(session, user)
    return service.registrar_ingreso(
        session,
        data,
        user.id,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )
