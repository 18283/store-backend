"""Modelos del módulo Cambios y Devoluciones — CU17-CU23.

Dos pasos siempre: primero se recepciona la prenda devuelta (CU21 — el
stock vuelve ahí mismo), y recién después se resuelve (CU19 cambio de
talla, o CU20 devolución/reembolso) — puede ser el mismo día o más
adelante, si había que esperar que llegara la talla nueva. Cada paso
genera su propia nota (CU22): nota de recepción y nota de resolución.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, SQLModel


class TipoSolicitud:
    CAMBIO = "CAMBIO"
    DEVOLUCION = "DEVOLUCION"


class EstadoSolicitud:
    SOLICITADO = "SOLICITADO"  # CU18: el cliente la creó, todavía no trajo la prenda
    RECIBIDO = "RECIBIDO"  # CU21: la prenda ya volvió a la sucursal y al stock
    RESUELTO = "RESUELTO"  # CU19/20: se entregó la nueva prenda o se reembolsó
    CANCELADO = "CANCELADO"

    TODOS = (SOLICITADO, RECIBIDO, RESUELTO, CANCELADO)


class FormaPagoDiferencia:
    EFECTIVO = "EFECTIVO"
    STRIPE = "STRIPE"


PLAZO_HORAS_SOLICITUD = 48


class SolicitudCambioDevolucion(SQLModel, table=True):
    __tablename__ = "solicitud_cambio_devolucion"

    id: int | None = Field(default=None, primary_key=True)
    venta_id: int = Field(foreign_key="venta.id", index=True)
    venta_detalle_id: int = Field(foreign_key="venta_detalle.id")
    cliente_id: int = Field(foreign_key="usuario.id", index=True)
    # Sucursal de la venta original — ahí debe llevar la prenda el cliente.
    sucursal_id: int = Field(foreign_key="sucursal.id")

    tipo: str = Field(max_length=20)  # CAMBIO | DEVOLUCION (puede terminar distinto al pedido si no hay stock)
    motivo: str = Field(default="TALLA", max_length=30)

    variante_devuelta_id: int = Field(foreign_key="producto_variante.id")
    cantidad: int = Field(gt=0)
    # Solo si tipo=CAMBIO: la variante que el cliente quiere llevarse.
    variante_nueva_id: int | None = Field(
        default=None, foreign_key="producto_variante.id"
    )

    estado: str = Field(default=EstadoSolicitud.SOLICITADO, max_length=20)

    fecha_solicitud: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    # venta.fecha_creacion + 48h, calculado al crear la solicitud.
    fecha_limite: datetime = Field()

    fecha_recepcion: datetime | None = Field(default=None)
    responsable_recepcion_id: int | None = Field(
        default=None, foreign_key="usuario.id"
    )

    fecha_resolucion: datetime | None = Field(default=None)
    responsable_resolucion_id: int | None = Field(
        default=None, foreign_key="usuario.id"
    )
    # Positivo = el cliente pagó una diferencia; negativo = se le devolvió;
    # None = todavía no se resolvió. En una DEVOLUCION siempre es negativo
    # (se le reembolsa el precio completo de la línea).
    diferencia: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )
    forma_pago_diferencia: str | None = Field(default=None, max_length=20)
