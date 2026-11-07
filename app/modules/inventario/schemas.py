"""Esquemas del módulo Inventario — CU12/CU13/CU14."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InventarioOut(BaseModel):
    id: int
    variante_id: int
    producto_id: int | None
    producto: str | None
    talla: str | None
    color: str | None
    color_hex: str | None
    sku: str | None
    sucursal_id: int
    sucursal: str | None
    ciudad: str | None
    cantidad_disponible: int
    cantidad_reservada: int
    costo_promedio: Decimal | None
    estado: str  # DISPONIBLE / AGOTADO


class InventarioPage(BaseModel):
    items: list[InventarioOut]
    total: int
    page: int
    size: int


class InventarioAjuste(BaseModel):
    variante_id: int
    sucursal_id: int
    cantidad_disponible: int = Field(ge=0)


class DisponibilidadSucursal(BaseModel):
    sucursal_id: int
    sucursal: str
    ciudad: str
    cantidad_disponible: int


# --------------------------------------------------------------------------- #
#  CU14 — Movimientos de inventario
# --------------------------------------------------------------------------- #
class MovimientoInventarioCreate(BaseModel):
    """Por ahora solo se registran ingresos (recepción de mercadería)."""

    variante_id: int
    sucursal_id: int
    cantidad: int = Field(gt=0)
    costo_unitario: Decimal = Field(gt=0)
    nota: str | None = Field(default=None, max_length=255)


class MovimientoInventarioOut(BaseModel):
    id: int
    variante_id: int
    producto_id: int | None
    producto: str | None
    talla: str | None
    color: str | None
    sku: str | None
    sucursal_id: int
    sucursal: str | None
    ciudad: str | None
    tipo: str
    cantidad: int
    costo_unitario: Decimal | None
    nota: str | None
    usuario_id: int
    usuario: str | None
    fecha: datetime


class MovimientoInventarioPage(BaseModel):
    items: list[MovimientoInventarioOut]
    total: int
    page: int
    size: int


# --------------------------------------------------------------------------- #
#  CU36 — Alertas y sugerencias de reposición
# --------------------------------------------------------------------------- #
class AlertaReposicionOut(BaseModel):
    variante_id: int
    producto_id: int | None
    producto: str | None
    talla: str | None
    color: str | None
    color_hex: str | None
    sku: str | None
    sucursal_id: int
    sucursal: str | None
    ciudad: str | None
    cantidad_disponible: int
    cantidad_reservada: int
    estado: str  # AGOTADO / STOCK_BAJO
    rotacion_periodo: int
    demanda_diaria_estimada: float | None
    dias_stock_restantes: float | None
    cantidad_a_reponer: int | None
    sugerencia: str


class AlertasReposicionPage(BaseModel):
    items: list[AlertaReposicionOut]
    total: int
    page: int
    size: int
    umbral: int
    fecha_desde: str
    fecha_hasta: str
    horizonte_dias: int
    agotados: int
    stock_bajo: int
    sin_datos_demanda: bool
