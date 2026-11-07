"""Esquemas del módulo Reportes — CU31 (reportes de ventas e inventario) y CU30 (exportación Excel/PDF)."""

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class TipoReporteExportacion(str, Enum):
    VENTAS = "ventas"
    INVENTARIO = "inventario"


class FormatoExportacion(str, Enum):
    XLSX = "xlsx"
    PDF = "pdf"


class SucursalVentasOut(BaseModel):
    sucursal: str
    cantidad_ventas: int
    ingresos: Decimal


class ProductoVendidoOut(BaseModel):
    nombre: str
    unidades: int
    ingresos: Decimal


class ReporteVentasOut(BaseModel):
    fecha_desde: date
    fecha_hasta: date
    sucursal: str  # nombre, o "Todas las sucursales"
    cantidad_ventas: int
    unidades: int
    ingresos: Decimal
    costo: Decimal
    # Solo sobre las prendas con costo registrado (para no inflarla).
    ganancia: Decimal
    descuentos: Decimal
    ticket_promedio: Decimal
    lineas_sin_costo: int
    # CU14: dinero cobrado offline y devuelto por falta de stock al sincronizar.
    egresos_reembolsos: Decimal
    cantidad_reembolsos: int
    por_sucursal: list[SucursalVentasOut]
    top_productos: list[ProductoVendidoOut]


class ItemInventarioOut(BaseModel):
    producto: str
    talla: str | None
    color: str | None
    sucursal: str
    disponible: int
    reservada: int
    costo_promedio: Decimal | None
    valor: Decimal | None  # disponible × costo promedio
    stock_bajo: bool


class ReporteInventarioOut(BaseModel):
    sucursal: str
    umbral: int
    total_variantes: int
    unidades_disponibles: int
    unidades_reservadas: int
    valor_inventario: Decimal
    variantes_stock_bajo: int
    items: list[ItemInventarioOut]
