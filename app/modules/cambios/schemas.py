"""Esquemas del módulo Cambios y Devoluciones — CU17-CU23."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  CU18 — Solicitar cambio o devolución (Cliente)
# --------------------------------------------------------------------------- #
class SolicitudCrearIn(BaseModel):
    venta_id: int
    venta_detalle_id: int
    tipo: str = Field(description="CAMBIO o DEVOLUCION")
    variante_nueva_id: int | None = Field(
        default=None, description="Requerido si tipo=CAMBIO"
    )
    cantidad: int = Field(gt=0, le=20)


class SolicitudOut(BaseModel):
    id: int
    venta_id: int
    venta_detalle_id: int
    cliente_id: int
    cliente: str | None = None
    sucursal_id: int
    sucursal: str | None = None
    tipo: str
    motivo: str
    producto: str | None = None
    variante_devuelta_id: int
    variante_devuelta: str | None = None  # "Talla · Color · SKU"
    cantidad: int
    variante_nueva_id: int | None
    variante_nueva: str | None = None
    estado: str
    fecha_solicitud: datetime
    fecha_limite: datetime
    fecha_recepcion: datetime | None
    fecha_resolucion: datetime | None
    diferencia: Decimal | None
    forma_pago_diferencia: str | None


class SolicitudPage(BaseModel):
    items: list[SolicitudOut]
    total: int
    page: int
    size: int


# --------------------------------------------------------------------------- #
#  CU19/CU20 — Resolver (cambio de talla o devolución/reembolso)
# --------------------------------------------------------------------------- #
class ResolverSolicitudIn(BaseModel):
    accion: str = Field(description="CAMBIO o DEVOLUCION")
    forma_pago: str | None = Field(
        default=None, description="EFECTIVO o STRIPE — requerido si hay diferencia"
    )
    monto_recibido: Decimal | None = Field(
        default=None, description="Si forma_pago=EFECTIVO y el cliente debe pagar"
    )


class CobroPendienteOut(BaseModel):
    pago_id: int
    monto: Decimal
    checkout_url: str
    qr_data_url: str | None = None


class ResolverSolicitudOut(BaseModel):
    solicitud: SolicitudOut
    # Si hay que cobrar una diferencia por Stripe, queda pendiente acá hasta
    # que el cliente pague (CU19 no termina de resolverse todavía).
    cobro_pendiente: CobroPendienteOut | None = None


# --------------------------------------------------------------------------- #
#  CU22 — Notas (de recepción y de resolución)
# --------------------------------------------------------------------------- #
class NotaCambioOut(BaseModel):
    solicitud_id: int
    tipo: str
    venta_id: int
    cliente_nombre: str
    sucursal: str
    producto: str
    variante_devuelta: str
    variante_nueva: str | None
    cantidad: int
    fecha_recepcion: datetime | None
    responsable_recepcion: str | None
    fecha_resolucion: datetime | None
    responsable_resolucion: str | None
    diferencia: Decimal | None
    forma_pago_diferencia: str | None
    estado: str
