"""Modelos del módulo Inventario — CU12/CU13/CU14.

Inventario = stock actual por variante (talla+color) y sucursal.
MovimientoInventario = historial de entradas/salidas (CU14) que va
actualizando ese stock actual (CU15).
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, SQLModel, UniqueConstraint


class TipoMovimiento:
    """Tipos de movimiento válidos. Por ahora solo se puede REGISTRAR
    (vía API) el de tipo INGRESO; los demás los genera el sistema solo
    (reservas, ventas)."""

    INGRESO = "INGRESO"  # el proveedor entregó mercadería
    AJUSTE = "AJUSTE"  # corrección manual de conteo (CU13 "Ajustar stock")
    SALIDA_VENTA = "SALIDA_VENTA"  # se vendió una prenda (CU22)
    LIBERACION_RESERVA = "LIBERACION_RESERVA"  # se liberó una reserva no comprada (CU17)
    ANULACION_VENTA = "ANULACION_VENTA"  # se canceló una venta pendiente de pago (CU22)
    # CU18-23 — cambios y devoluciones (plazo 48h, motivo talla).
    DEVOLUCION_CLIENTE = "DEVOLUCION_CLIENTE"  # el cajero/encargado confirmó que recibió la prenda devuelta
    SALIDA_CAMBIO = "SALIDA_CAMBIO"  # se entregó la variante nueva de un cambio de talla

    TODOS = (
        INGRESO,
        AJUSTE,
        SALIDA_VENTA,
        LIBERACION_RESERVA,
        ANULACION_VENTA,
        DEVOLUCION_CLIENTE,
        SALIDA_CAMBIO,
    )


class Inventario(SQLModel, table=True):
    __tablename__ = "inventario"
    __table_args__ = (
        UniqueConstraint(
            "variante_id", "sucursal_id", name="uq_inventario_variante_sucursal"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    variante_id: int = Field(foreign_key="producto_variante.id")
    sucursal_id: int = Field(foreign_key="sucursal.id")
    cantidad_disponible: int = Field(default=0, ge=0)
    cantidad_reservada: int = Field(default=0, ge=0)
    # Costo promedio ponderado de esta variante en esta sucursal (CU14).
    # Se recalcula en cada INGRESO. NULL = todavía no se cargó ningún ingreso.
    costo_promedio: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )


class MovimientoInventario(SQLModel, table=True):
    __tablename__ = "movimiento_inventario"

    id: int | None = Field(default=None, primary_key=True)
    variante_id: int = Field(foreign_key="producto_variante.id")
    sucursal_id: int = Field(foreign_key="sucursal.id")
    usuario_id: int = Field(foreign_key="usuario.id")

    tipo: str = Field(max_length=30)
    cantidad: int = Field(gt=0)
    # Solo aplica (por ahora) a movimientos de tipo INGRESO.
    costo_unitario: Decimal | None = Field(
        default=None, max_digits=10, decimal_places=2
    )
    nota: str | None = Field(default=None, max_length=255)

    fecha: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
