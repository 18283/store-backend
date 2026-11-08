from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import Rol, RolNombre, Usuario

from .schemas import (
    ConfirmarReembolsoEfectivoIn,
    DatosOfflineOut,
    DispositivoPushIn,
    ReembolsoEfectivoOut,
    ReembolsoEfectivoPage,
    ResultadoSincronizacionOut,
    SincronizarReservaIn,
    SincronizarVentaEfectivoIn,
    SincronizarVentaIn,
)
from .service import (
    confirmar_reembolso_efectivo,
    listar_reembolsos_pendientes,
    preparar_datos,
    registrar_dispositivo,
    sincronizar_reserva,
    sincronizar_venta,
    sincronizar_venta_efectivo,
)

router = APIRouter(prefix="/offline", tags=["offline"])

OfflineUser = Annotated[
    Usuario,
    Depends(
        require_roles(
            RolNombre.CLIENTE,
            RolNombre.ADMINISTRADOR,
            RolNombre.ENCARGADO,
            RolNombre.CAJERO,
        )
    ),
]

ClienteOffline = Annotated[Usuario, Depends(require_roles(RolNombre.CLIENTE))]
CajeroOffline = Annotated[Usuario, Depends(require_roles(RolNombre.CAJERO))]
StaffOffline = Annotated[
    Usuario,
    Depends(
        require_roles(
            RolNombre.ADMINISTRADOR, RolNombre.ENCARGADO, RolNombre.CAJERO
        )
    ),
]


def _sucursal_permitida(session: SessionDep, user: Usuario) -> int | None:
    """None = administrador, ve toda la empresa. Un id = Cajero/Encargado,
    forzado a la suya. Mismo patrón que inventario/reservas/cambios."""
    rol = session.get(Rol, user.rol_id)
    if rol is not None and rol.nombre in (RolNombre.ENCARGADO, RolNombre.CAJERO):
        if user.sucursal_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Tu cuenta no está vinculada a ninguna sucursal. Contactá al administrador.",
            )
        return user.sucursal_id
    return None


@router.get("/preparar-datos", response_model=DatosOfflineOut)
def preparar_datos_offline(
    session: SessionDep,
    usuario: OfflineUser,
    sucursal_id: int | None = Query(default=None),
):
    return preparar_datos(session, usuario, sucursal_id)


# --------------------------------------------------------------------------- #
#  CU14/CU15 — Dispositivo (token de push)
# --------------------------------------------------------------------------- #
@router.post("/dispositivos", status_code=status.HTTP_201_CREATED)
def registrar_dispositivo_push(
    data: DispositivoPushIn, session: SessionDep, usuario: OfflineUser
):
    return registrar_dispositivo(session, usuario, data)


# --------------------------------------------------------------------------- #
#  CU11/CU14 — Sincronizar reserva registrada sin conexión
# --------------------------------------------------------------------------- #
@router.post("/sincronizar/reservas", response_model=ResultadoSincronizacionOut)
def sincronizar_reserva_offline(
    data: SincronizarReservaIn, session: SessionDep, usuario: ClienteOffline
):
    return sincronizar_reserva(session, usuario, data)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Sincronizar venta de contado registrada sin conexión
# --------------------------------------------------------------------------- #
@router.post("/sincronizar/ventas", response_model=ResultadoSincronizacionOut)
def sincronizar_venta_offline(
    data: SincronizarVentaIn, session: SessionDep, usuario: ClienteOffline
):
    return sincronizar_venta(session, usuario, data)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Sincronizar venta de contado EN EFECTIVO del Cajero
# --------------------------------------------------------------------------- #
@router.post(
    "/sincronizar/ventas-efectivo", response_model=ResultadoSincronizacionOut
)
def sincronizar_venta_efectivo_offline(
    data: SincronizarVentaEfectivoIn, session: SessionDep, cajero: CajeroOffline
):
    return sincronizar_venta_efectivo(session, cajero, data)


# --------------------------------------------------------------------------- #
#  CU16 — Consultar y confirmar devoluciones de efectivo pendientes
# --------------------------------------------------------------------------- #
@router.get("/reembolsos-efectivo", response_model=ReembolsoEfectivoPage)
def listar_reembolsos_efectivo(session: SessionDep, user: StaffOffline):
    sucursal_permitida = _sucursal_permitida(session, user)
    items = listar_reembolsos_pendientes(
        session,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )
    return ReembolsoEfectivoPage(items=items, total=len(items))


@router.post(
    "/reembolsos-efectivo/{reembolso_id}/confirmar",
    response_model=ReembolsoEfectivoOut,
)
def confirmar_reembolso_efectivo_offline(
    reembolso_id: int,
    data: ConfirmarReembolsoEfectivoIn,
    session: SessionDep,
    user: StaffOffline,
):
    sucursal_permitida = _sucursal_permitida(session, user)
    return confirmar_reembolso_efectivo(
        session,
        user,
        reembolso_id,
        data,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
    )
