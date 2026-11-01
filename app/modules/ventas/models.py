"""Modelos del módulo Ventas y Pagos — CU21 (carrito), CU22 (compra web),
CU27/CU28 (pago electrónico Stripe/QR y su confirmación).

CU24/25/26 (venta presencial, pago en caja, comprobante) reutilizan estas
mismas tablas Venta/VentaDetalle/Pago cuando lleguemos a ese bloque.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, SQLModel, UniqueConstraint


class EstadoCarrito:
    ACTIVO = "ACTIVO"
    CONVERTIDO = "CONVERTIDO"
    ABANDONADO = "ABANDONADO"


class Carrito(SQLModel, table=True):
    __tablename__ = "carrito"

    id: int | None = Field(default=None, primary_key=True)
    cliente_id: int = Field(foreign_key="usuario.id")
    estado: str = Field(default=EstadoCarrito.ACTIVO, max_length=20)
    fecha_creacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    fecha_actualizacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class CarritoDetalle(SQLModel, table=True):
    __tablename__ = "carrito_detalle"
    __table_args__ = (
        UniqueConstraint("carrito_id", "variante_id", name="uq_carrito_variante"),
    )

    id: int | None = Field(default=None, primary_key=True)
    carrito_id: int = Field(foreign_key="carrito.id")
    variante_id: int = Field(foreign_key="producto_variante.id")
    cantidad: int = Field(gt=0)


class EstadoVenta:
    PENDIENTE_PAGO = "PENDIENTE_PAGO"
    PAGADA = "PAGADA"
    COMPLETADA = "COMPLETADA"
    ANULADA = "ANULADA"


class EstadoPago:
    PENDIENTE = "PENDIENTE"
    PROCESANDO = "PROCESANDO"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"
    # CU14: se cobró (off-session, tarjeta guardada) pero al sincronizar ya
    # no había stock — se le devolvió el dinero al cliente.
    REEMBOLSADO = "REEMBOLSADO"


class MetodoPago:
    STRIPE = "STRIPE"  # CU27 — pago web/QR
    STRIPE_GUARDADA = "STRIPE_GUARDADA"  # CU12 — venta offline, tarjeta guardada
    EFECTIVO = "EFECTIVO"  # CU25 (futuro, venta presencial)
    TARJETA_CAJA = "TARJETA_CAJA"  # CU25 (futuro, venta presencial)


class OrigenVenta:
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class Venta(SQLModel, table=True):
    __tablename__ = "venta"

    id: int | None = Field(default=None, primary_key=True)
    cliente_id: int = Field(foreign_key="usuario.id")
    # Sucursal principal de despacho (la que aporta más monto de la venta).
    # Si la compra sale de varias sucursales, cada línea guarda la suya en
    # `VentaDetalle.sucursal_id`.
    sucursal_id: int = Field(foreign_key="sucursal.id")
    cajero_id: int | None = Field(default=None, foreign_key="usuario.id")  # CU24, null = compra web
    # Si la venta nació al finalizar una reserva (las prendas que el cliente
    # decidió llevarse). Queda pendiente hasta que el cajero la cobra.
    reserva_id: int | None = Field(default=None, foreign_key="reserva.id")

    # Solo compras en línea: a dónde se envía. El delivery en sí ocurre fuera
    # del sistema. Ventas en caja y ventas antiguas quedan en NULL.
    direccion_entrega: str | None = Field(default=None, max_length=200)
    referencia_entrega: str | None = Field(default=None, max_length=150)

    # CU12: si nació de una venta de contado registrada sin conexión.
    origen: str = Field(default=OrigenVenta.ONLINE, max_length=20)

    estado: str = Field(default=EstadoVenta.PENDIENTE_PAGO, max_length=20)
    total: Decimal = Field(max_digits=10, decimal_places=2)
    fecha_creacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class VentaDetalle(SQLModel, table=True):
    __tablename__ = "venta_detalle"

    id: int | None = Field(default=None, primary_key=True)
    venta_id: int = Field(foreign_key="venta.id")
    variante_id: int = Field(foreign_key="producto_variante.id")
    # Sucursal de la que sale (y a la que se le descontó) esta línea.
    sucursal_id: int | None = Field(default=None, foreign_key="sucursal.id")
    cantidad: int = Field(gt=0)
    # Foto del precio/costo al momento de la venta (para reportes exactos
    # aunque después cambien los precios o el costo promedio).
    # `precio_unitario` es lo que se cobró (ya con descuento); `precio_original`
    # es el precio sin promoción y `promocion_id` cuál se aplicó (CU33).
    precio_unitario: Decimal = Field(max_digits=10, decimal_places=2)
    precio_original: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )
    promocion_id: int | None = Field(default=None, foreign_key="promocion.id")
    costo_unitario: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )


class Pago(SQLModel, table=True):
    __tablename__ = "pago"

    id: int | None = Field(default=None, primary_key=True)
    # Nullable: un pago también puede corresponder a la diferencia cobrada
    # en un cambio de talla (CU19), que no es una Venta nueva.
    venta_id: int | None = Field(default=None, foreign_key="venta.id")
    solicitud_cambio_id: int | None = Field(
        default=None, foreign_key="solicitud_cambio_devolucion.id"
    )
    metodo: str = Field(max_length=20)
    estado: str = Field(default=EstadoPago.PENDIENTE, max_length=20)
    monto: Decimal = Field(max_digits=10, decimal_places=2)

    stripe_session_id: str | None = Field(default=None, max_length=255)
    stripe_payment_intent_id: str | None = Field(default=None, max_length=255)

    # CU14: si este pago se revirtió por conflicto de stock al sincronizar.
    stripe_refund_id: str | None = Field(default=None, max_length=255)
    fecha_reembolso: datetime | None = Field(default=None)

    # Solo para CU25 (pago en caja, método EFECTIVO): lo que puso el
    # cliente y el vuelto calculado. Quedan en None para STRIPE/TARJETA_CAJA.
    monto_recibido: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )
    vuelto: Decimal | None = Field(default=None, max_digits=10, decimal_places=2)

    fecha_creacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    fecha_actualizacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
