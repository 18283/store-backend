"""Endpoints del módulo Ventas — CU22 (compra web), CU24/25/26 (venta
presencial, pago en caja, comprobante) y CU27/CU28 (pago electrónico)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.identidad.schemas import ClienteRegistroIn
from app.modules.ventas import service
from app.modules.ventas.schemas import (
    CheckoutCreate,
    ClienteBuscarOut,
    ComprobanteOut,
    EstadoPagoOut,
    PagoCajaCreate,
    PagoOut,
    TarjetaEstadoOut,
    TarjetaIniciarOut,
    VentaAdminPage,
    VentaCajaOut,
    VentaOut,
    VentaPresencialCreate,
)

router = APIRouter(prefix="/ventas", tags=["ventas"])

ClienteUser = Annotated[Usuario, Depends(require_roles(RolNombre.CLIENTE))]
CajeroUser = Annotated[Usuario, Depends(require_roles(RolNombre.CAJERO))]
AdminOEncargado = Annotated[
    Usuario, Depends(require_roles(RolNombre.ADMINISTRADOR, RolNombre.ENCARGADO))
]


def _sucursal_permitida(session: SessionDep, user: Usuario) -> int | None:
    """None = admin, puede ver cualquier sucursal. Un id = encargado,
    forzado a la suya. Mismo patrón que inventario/reservas."""
    rol = session.get(Rol, user.rol_id)
    if rol is not None and rol.nombre == RolNombre.ENCARGADO:
        if user.sucursal_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Tu cuenta no está vinculada a ninguna sucursal. Contactá al administrador.",
            )
        return user.sucursal_id
    return None


@router.post("/checkout", response_model=VentaOut, status_code=status.HTTP_201_CREATED)
def checkout(  # CU22
    data: CheckoutCreate, session: SessionDep, user: ClienteUser
):
    return service.crear_checkout(session, user.id, data)


@router.get("/mias", response_model=list[VentaOut])
def mis_ventas(session: SessionDep, user: ClienteUser):  # CU22
    return service.mis_ventas(session, user.id)


@router.post("/{venta_id}/cancelar", response_model=VentaOut)
def cancelar(venta_id: int, session: SessionDep, user: ClienteUser):  # CU22
    return service.cancelar_venta(session, user.id, venta_id)


@router.post(
    "/{venta_id}/pagos", response_model=PagoOut, status_code=status.HTTP_201_CREATED
)
def iniciar_pago(venta_id: int, session: SessionDep, user: ClienteUser):  # CU27
    return service.iniciar_pago(session, user.id, venta_id)


@router.get("/{venta_id}/pagos/estado", response_model=EstadoPagoOut)
def estado_pago(venta_id: int, session: SessionDep, user: ClienteUser):  # CU28
    return service.consultar_estado_pago(session, user.id, venta_id)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Tarjeta guardada (necesaria para comprar sin conexión)
# --------------------------------------------------------------------------- #
@router.post("/tarjeta/iniciar", response_model=TarjetaIniciarOut)
def iniciar_guardar_tarjeta(session: SessionDep, user: ClienteUser):
    return service.iniciar_guardar_tarjeta(session, user)


@router.post("/tarjeta/confirmar", response_model=TarjetaEstadoOut)
def confirmar_tarjeta_guardada(session: SessionDep, user: ClienteUser):
    return service.confirmar_tarjeta_guardada(session, user)


@router.get("/tarjeta", response_model=TarjetaEstadoOut)
def estado_tarjeta_guardada(user: ClienteUser):
    return service.estado_tarjeta_guardada(user)


@router.delete("/tarjeta", response_model=TarjetaEstadoOut)
def eliminar_tarjeta_guardada(session: SessionDep, user: ClienteUser):
    return service.eliminar_tarjeta_guardada(session, user)


# --------------------------------------------------------------------------- #
#  CU24/25/26 — Venta presencial, pago en caja, comprobante (rol Cajero)
# --------------------------------------------------------------------------- #
@router.get("/clientes/buscar", response_model=list[ClienteBuscarOut])
def buscar_clientes(session: SessionDep, cajero: CajeroUser, q: str = ""):
    # q vacío = listado por defecto (para mostrar clientes recientes como
    # ayuda antes de escribir nada), igual que con las prendas.
    usuarios = service.buscar_clientes(session, q, empresa_id=cajero.empresa_id)
    return [
        ClienteBuscarOut(
            id=u.id,
            nombre=u.nombre,
            apellido=u.apellido,
            email=u.email,
            telefono=u.telefono,
        )
        for u in usuarios
    ]


@router.post(
    "/clientes", response_model=ClienteBuscarOut, status_code=status.HTTP_201_CREATED
)
def registrar_cliente_rapido(
    data: ClienteRegistroIn, session: SessionDep, cajero: CajeroUser
):
    usuario = service.registrar_cliente_rapido(
        session, data, empresa_id=cajero.empresa_id
    )
    return ClienteBuscarOut(
        id=usuario.id,
        nombre=usuario.nombre,
        apellido=usuario.apellido,
        email=usuario.email,
        telefono=usuario.telefono,
    )


@router.get("/caja/historial", response_model=list[VentaCajaOut])
def historial_caja(session: SessionDep, cajero: CajeroUser):
    return service.historial_caja(session, cajero)


@router.get("/caja/por-cobrar", response_model=list[VentaCajaOut])
def ventas_por_cobrar(session: SessionDep, cajero: CajeroUser):  # CU25
    return service.ventas_por_cobrar(session, cajero)


@router.post("/{venta_id}/caja/anular", response_model=VentaOut)
def anular_venta_por_cobrar(  # CU25
    venta_id: int, session: SessionDep, cajero: CajeroUser
):
    return service.anular_venta_por_cobrar(session, cajero, venta_id)


@router.post(
    "/presencial", response_model=VentaOut, status_code=status.HTTP_201_CREATED
)
def crear_venta_presencial(  # CU24
    data: VentaPresencialCreate, session: SessionDep, cajero: CajeroUser
):
    return service.crear_venta_presencial(session, cajero, data)


@router.post("/{venta_id}/pagos/caja", response_model=VentaOut)
def procesar_pago_caja(  # CU25
    venta_id: int, data: PagoCajaCreate, session: SessionDep, cajero: CajeroUser
):
    return service.procesar_pago_caja(session, cajero, venta_id, data)


@router.get("/{venta_id}/comprobante", response_model=ComprobanteOut)
def obtener_comprobante(venta_id: int, session: SessionDep, cajero: CajeroUser):  # CU26
    return service.emitir_comprobante(session, cajero, venta_id)


# --------------------------------------------------------------------------- #
#  CU36/CU37 — Consultar Ventas (Global para Admin, de la sucursal para Encargado)
# --------------------------------------------------------------------------- #
@router.get("/sucursal", response_model=VentaAdminPage)
def listar_ventas_sucursal(
    session: SessionDep,
    user: AdminOEncargado,
    sucursal_id: int | None = None,
    estado: str | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    sucursal_permitida = _sucursal_permitida(session, user)
    items, total = service.listar_ventas_sucursal(
        session,
        sucursal_id=sucursal_id,
        estado=estado,
        sucursal_permitida=sucursal_permitida,
        empresa_id=user.empresa_id,
        page=page,
        size=size,
    )
    return VentaAdminPage(items=items, total=total, page=page, size=size)
