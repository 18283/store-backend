"""Endpoints del módulo Traspasos — CU24 (código anterior: CU61).

Flujo (diagrama de robustez):

    GET  /traspasos/opciones          -> rol, sucursales y destinos permitidos
    GET  /traspasos/variantes?q=...   -> variantes activas con sucursales y stock
    POST /traspasos                   -> registra la solicitud en PENDIENTE
    GET  /traspasos/{codigo}          -> consulta una solicitud existente
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from app.core.deps import AuthenticatedUser, SessionDep, require_roles
from app.modules.identidad.models import RolNombre, Usuario

from . import service
from .models import EstadoTraspaso
from .schemas import (
    OpcionesTraspasoOut,
    SolicitudTraspasoCrearIn,
    SolicitudTraspasoDecisionIn,
    SolicitudTraspasoDespachoOut,
    SolicitudTraspasoOut,
    SolicitudTraspasoPage,
    VariantesConStockOut,
)

router = APIRouter(prefix="/traspasos", tags=["traspasos"])

TraspasoUser = Annotated[
    Usuario,
    Depends(require_roles(RolNombre.CLIENTE, RolNombre.ENCARGADO)),
]

AdminUser = Annotated[
    Usuario,
    Depends(require_roles(RolNombre.ADMINISTRADOR)),
]

EncargadoOrAdminUser = Annotated[
    Usuario,
    Depends(require_roles(RolNombre.ADMINISTRADOR, RolNombre.ENCARGADO)),
]


# --------------------------------------------------------------------------- #
#  CU24 — Pasos 2-9: preparar el formulario
# --------------------------------------------------------------------------- #
@router.get("/opciones", response_model=OpcionesTraspasoOut)
def opciones(session: SessionDep, user: TraspasoUser):
    return service.preparar_formulario(session, user)


# --------------------------------------------------------------------------- #
#  CU24 — Pasos 11-20: buscar variante y sucursales con stock
# --------------------------------------------------------------------------- #
@router.get("/variantes", response_model=VariantesConStockOut)
def variantes(
    session: SessionDep,
    user: TraspasoUser,
    q: str | None = Query(
        default=None,
        max_length=80,
        description="Producto, SKU, talla o color",
    ),
    variante_id: int | None = Query(
        default=None, description="Filtra una variante concreta"
    ),
    limite: int = Query(default=20, ge=1, le=50),
):
    return service.buscar_variantes(
        session, user, q=q, variante_id=variante_id, limite=limite
    )


# --------------------------------------------------------------------------- #
#  CU24 — Pasos 21-40: registrar la solicitud
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=SolicitudTraspasoOut,
    status_code=status.HTTP_201_CREATED,
)
def solicitar(
    data: SolicitudTraspasoCrearIn, session: SessionDep, user: TraspasoUser
):
    """Registra el traspaso en PENDIENTE. No mueve inventario todavía.

    Errores de negocio con `detail.codigo`:
    - `CONFIRMACION_STOCK_DESTINO`: el destino ya tiene stock suficiente y hay
      que reenviar con `confirmar_stock_suficiente=true`.
    - `SOLICITUD_PENDIENTE_EXISTENTE`: ya hay una solicitud igual; incluye
      `codigo_solicitud` para consultarla.
    - `SIN_STOCK_ORIGEN` / `STOCK_INSUFICIENTE`: el origen no puede cubrir la
      cantidad pedida.
    """
    return service.solicitar_traspaso(session, user, data)


# --------------------------------------------------------------------------- #
#  CU25 — Listar solicitudes pendientes (Administrador)
# --------------------------------------------------------------------------- #
@router.get("/pendientes", response_model=SolicitudTraspasoPage)
def listar_pendientes(
    session: SessionDep,
    user: AdminUser,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Muestra las solicitudes de la empresa en estado PENDIENTE, ordenadas por fecha."""
    items, total = service.listar_solicitudes(
        session, user, estado=EstadoTraspaso.PENDIENTE, page=page, size=size
    )
    return SolicitudTraspasoPage(items=items, total=total, page=page, size=size)


@router.get("", response_model=SolicitudTraspasoPage)
def listar_todas(
    session: SessionDep,
    user: EncargadoOrAdminUser,
    estado: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """Muestra todas las solicitudes de la empresa con filtro opcional de estado."""
    items, total = service.listar_solicitudes(
        session, user, estado=estado, page=page, size=size
    )
    return SolicitudTraspasoPage(items=items, total=total, page=page, size=size)


# --------------------------------------------------------------------------- #
#  CU25 — Aprobar o rechazar traspaso (Administrador)
# --------------------------------------------------------------------------- #
@router.patch("/{solicitud_id}/decidir", response_model=SolicitudTraspasoOut)
@router.post("/{solicitud_id}/decidir", response_model=SolicitudTraspasoOut)
@router.patch("/{solicitud_id}/decision", response_model=SolicitudTraspasoOut)
@router.post("/{solicitud_id}/decision", response_model=SolicitudTraspasoOut)
def decidir(
    solicitud_id: int | str,
    data: SolicitudTraspasoDecisionIn,
    session: SessionDep,
    user: AdminUser,
):
    """Procesa la decisión de aprobar o rechazar una solicitud de traspaso."""
    return service.procesar_decision(session, user, solicitud_id, data)


@router.patch("/{solicitud_id}/aprobar", response_model=SolicitudTraspasoOut)
@router.post("/{solicitud_id}/aprobar", response_model=SolicitudTraspasoOut)
def aprobar(
    solicitud_id: int | str,
    session: SessionDep,
    user: AdminUser,
    data: SolicitudTraspasoDecisionIn | None = None,
):
    """Atajo para aprobar una solicitud de traspaso."""
    payload = data or SolicitudTraspasoDecisionIn(decision="APROBAR")
    payload.decision = "APROBAR"
    return service.procesar_decision(session, user, solicitud_id, payload)


@router.patch("/{solicitud_id}/rechazar", response_model=SolicitudTraspasoOut)
@router.post("/{solicitud_id}/rechazar", response_model=SolicitudTraspasoOut)
def rechazar(
    solicitud_id: int | str,
    data: SolicitudTraspasoDecisionIn,
    session: SessionDep,
    user: AdminUser,
):
    """Atajo para rechazar una solicitud de traspaso (requiere motivo)."""
    data.decision = "RECHAZAR"
    return service.procesar_decision(session, user, solicitud_id, data)


# --------------------------------------------------------------------------- #
#  CU26 — Despachar Traspaso (Acción)
# --------------------------------------------------------------------------- #
@router.post(
    "/{solicitud_id}/despachar",
    response_model=SolicitudTraspasoDespachoOut,
    status_code=status.HTTP_200_OK,
)
@router.patch(
    "/{solicitud_id}/despachar",
    response_model=SolicitudTraspasoDespachoOut,
    status_code=status.HTTP_200_OK,
)
def despachar(
    solicitud_id: int | str,
    datos_envio: Annotated[
        dict | None,
        Body(
            openapi_examples={
                "sin_datos": {"value": None},
                "con_datos": {
                    "value": {
                        "transportista": "Transportes XYZ",
                        "numero_guia": "1234567890",
                        "fecha_hora": "2026-10-28T14:30:00",
                        "observaciones": "Entrega normal",
                    }
                },
            }
        ),
    ],
    session: SessionDep,
    user: EncargadoOrAdminUser,
) -> SolicitudTraspasoDespachoOut:
    """CU26 — Despachar traspaso.

    Registra la salida en tránsito sin incrementar todavía el inventario de destino.

    Errores de negocio con `detail.codigo`:
    - `DESPECHO_INVALIDO`: la solicitud no está en estado APROBADA o ya fue despachada.
    - `SIN_PERMISO_DESPECHO`: el actor no es Encargado o no pertenece a la sucursal de origen.
    - `STOCK_INCONSISTENTE`: la cantidad reservada no alcanza o hay inconsistencia de stock.
    - `VARIANTE_INACTIVA_DESPECHO`: la variante está inactiva o la sucursal destino está inactiva.
    - `DATOS_ENVIO_INCOMPLETOS`: datos de envío obligatorios incompletos (flujo A6).
    """
    print(f"[DEBUG ROUTER] Recibido solicitud_id: {solicitud_id} (tipo: {type(solicitud_id)}) de usuario: {user.email}")
    return service.despachar_traspaso(
        session, user, solicitud_id, datos_envio=datos_envio
    )


# --------------------------------------------------------------------------- #
#  CU24/CU25 — Consulta de una solicitud (para seguimiento)
# --------------------------------------------------------------------------- #
@router.get("/{codigo}", response_model=SolicitudTraspasoOut)
def consultar(codigo: str, session: SessionDep, user: AuthenticatedUser):
    return service.consultar_traspaso(session, user, codigo)


# --------------------------------------------------------------------------- #
#  CU26 — Despachar Traspaso (Listar solicitudes aprobadas de la sucursal origen)
# --------------------------------------------------------------------------- #
@router.get("/origen", response_model=SolicitudTraspasoPage)
@router.get("/salientes", response_model=SolicitudTraspasoPage)
@router.get("/origen/aprobados", response_model=SolicitudTraspasoPage)
@router.get("/despachables", response_model=SolicitudTraspasoPage)
def listar_despachables(
    session: SessionDep,
    user: EncargadoOrAdminUser,
    origen_id: int | None = Query(default=None, description="Filtrar por sucursal de origen (Admin)"),
    estado: str | None = Query(default=EstadoTraspaso.APROBADA, description="Estado de las solicitudes (por defecto APROBADA)"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
):
    """CU26 — Muestra las solicitudes APROBADAS donde la sucursal de origen coincide
    con la sucursal asignada del Encargado logueado (o filtrada por origen_id si es Admin).
    """
    items, total = service.listar_traspasos_origen(
        session, user, origen_id=origen_id, estado=estado, page=page, size=size
    )
    return SolicitudTraspasoPage(items=items, total=total, page=page, size=size)
