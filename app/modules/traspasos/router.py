"""Endpoints del módulo Traspasos — CU24 (código anterior: CU61).

Flujo (diagrama de robustez):

    GET  /traspasos/opciones          -> rol, sucursales y destinos permitidos
    GET  /traspasos/variantes?q=...   -> variantes activas con sucursales y stock
    POST /traspasos                   -> registra la solicitud en PENDIENTE
    GET  /traspasos/{codigo}          -> consulta una solicitud existente
"""

from app.modules.inventario.models import Inventario
from .models import SolicitudTraspaso
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status, HTTPException
from sqlalchemy import select

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
    """Muestra todas las solicitudes de la empresa con filtro opcional de estado.

    - Si es Administrador o Superadmin, ve todas las de la empresa.
    - Si es EncargadoSucursal, ve las que corresponden a su sucursal (origen o destino).
    - Si es Cliente, ve solo las suyas (filtrado automáticamente por service.listar_solicitudes).
    """
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
#  CU27 — Confirmar Recepción de Traspaso
# --------------------------------------------------------------------------- #
@router.patch(
    "/{solicitud_id}/confirmar-recepcion",
    response_model=SolicitudTraspasoOut,
)
def confirmar_recepcion(
    solicitud_id: int,
    datos_recepcion: Annotated[
        dict | None,
        Body(
            openapi_examples={
                "con_datos": {
                    "value": {
                        "cantidadRecibida": 1,
                        "observaciones": "Entrega correcta",
                    }
                }
            }
        ),
    ],
    session: SessionDep,
    user: EncargadoOrAdminUser,
) -> SolicitudTraspasoOut:
    """CU27 — Confirmar recepción de traspaso.

    Valida que la solicitud esté en estado EN_TRANSITO, cambia su estado a
    RECIBIDO y suma la cantidad recibida al inventario de la sucursal de
    destino.

    Errores de negocio con `detail.codigo`:
    - `DESPECHO_INVALIDO`: la solicitud no está en estado EN_TRANSITO.
    - `SIN_PERMISO_DESPECHO`: el actor no es Encargado o no pertenece a la
      sucursal de origen.
    """
    from fastapi import status
    from app.modules.inventario.models import MovimientoInventario, TipoMovimiento
    from app.modules.identidad.models import RolNombre

    # ---- Paso 1: Contexto y permiso de rol ----
    ctx = service._contexto_usuario(session, actor=user)
    if not ctx["es_admin"] and ctx["rol"] != RolNombre.ENCARGADO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el Encargado de sucursal o Administrador puede confirmar la recepción",
        )

    # ---- Paso 2: Obtener la solicitud ----
    solicitud = session.exec(
        select(SolicitudTraspaso).where(
            SolicitudTraspaso.id == solicitud_id,
            SolicitudTraspaso.empresa_id == ctx["empresa_id"],
        ).with_for_update()
    ).scalars().first()

    if solicitud is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Solicitud de traspaso no encontrada",
        )

    # ---- Paso 3: Validar que esté en estado EN_TRANSITO ----
    if solicitud.estado != EstadoTraspaso.EN_TRANSITO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"La solicitud no puede confirmarse porque su estado actual es "
                f"'{solicitud.estado}', no EN_TRANSITO. Es posible que ya fue "
                "confirmada o procesada previamente."
            )
        )

    # ---- Paso 4: Validar que el usuario tiene permiso de la sucursal origen ----
    if ctx["rol"] == RolNombre.ENCARGADO:
        if solicitud.destino_id != ctx["sucursal_asignada_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puede confirmar recepción de un traspaso que no corresponda a su sucursal asignada.",
            )

    # ---- Paso 5: Obtener los datos de recepción ----
    cantidad_recibida = datos_recepcion.get("cantidadRecibida") if datos_recepcion else 0
    observaciones = datos_recepcion.get("observaciones") if datos_recepcion else None

    if cantidad_recibida <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La cantidad recibida debe ser mayor que cero",
        )

    # ---- Paso 6: Cambiar estado a RECIBIDO y actualizar inventario ----
    # 1. Actualizar estado de la solicitud
    solicitud.estado = EstadoTraspaso.RECIBIDO
    solicitud.fecha_decision = datetime.now(timezone.utc)
    if observaciones:
        solicitud.observacion_motivo = observaciones

    # 2. Sumar cantidad al inventario de la sucursal de destino

    inventario_destino = session.exec(
        select(Inventario).where(
            Inventario.variante_id == solicitud.variante_id,
            Inventario.sucursal_id == solicitud.destino_id,
        ).with_for_update().execution_options(populate_existing=True)
    ).scalars().first()  # <--- ¡AGREGA EL .scalars() AQUÍ!

    if inventario_destino is None:
        # Crear registro de inventario si no existe para la sucursal destino
        from app.modules.sucursales.models import Sucursal
        sucursal_destino = session.get(Sucursal, solicitud.destino_id)
        if sucursal_destino is None or not sucursal_destino.activa:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La sucursal de destino está inactiva",
            )
        inventario_destino = Inventario(
            variante_id=solicitud.variante_id,
            sucursal_id=solicitud.destino_id,
            cantidad_disponible=cantidad_recibida,
            cantidad_reservada=0,
        )
        session.add(inventario_destino)
    else:
        # Sumar a la cantidad disponible existente
        inventario_destino.cantidad_disponible += cantidad_recibida

    session.add(inventario_destino)

    # 3. Registrar movimiento de inventario ENTRADA_TRASPASO
    movimiento = MovimientoInventario(
        variante_id=solicitud.variante_id,
        sucursal_id=solicitud.destino_id,
        usuario_id=user.id,
        tipo="ENTRADA_TRASPASO",
        cantidad=cantidad_recibida,
        nota=f"Confirmación recepción solicitud {solicitud.codigo}",
    )
    session.add(movimiento)

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo guardar la confirmación de recepción",
        ) from exc

    session.refresh(solicitud)

    # CU29 — Notificar al cliente y a los administradores
    from app.modules.traspasos.notificacion_service import crear_notificacion, crear_notificacion_para_roles
    crear_notificacion(
        session=session,
        usuario_id=solicitud.solicitante_id,
        mensaje=f"Tu solicitud de traspaso {solicitud.codigo} ha sido RECIBIDA en destino.",
        tipo="success",
        codigo_traspaso=solicitud.codigo,
    )
    crear_notificacion_para_roles(
        session=session,
        roles=[RolNombre.ADMINISTRADOR],
        mensaje=f"El traspaso {solicitud.codigo} fue confirmado como recibido.",
        tipo="info",
        codigo_traspaso=solicitud.codigo,
        empresa_id=solicitud.empresa_id,
    )

    return SolicitudTraspasoOut.model_validate(solicitud)


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
