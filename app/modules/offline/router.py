from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import RolNombre, Usuario

from .schemas import (
    DatosOfflineOut,
    DispositivoPushIn,
    ResultadoSincronizacionOut,
    SincronizarReservaIn,
    SincronizarVentaIn,
)
from .service import (
    preparar_datos,
    registrar_dispositivo,
    sincronizar_reserva,
    sincronizar_venta,
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
