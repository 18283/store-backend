"""Esquemas del módulo Reservas — CU16."""

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field


class ItemReservaCreate(BaseModel):
    variante_id: int
    cantidad: int = Field(gt=0, le=20)


class ReservaCreate(BaseModel):
    sucursal_id: int
    fecha: date
    hora_inicio: time
    duracion_minutos: int = Field(description="30 o 60")
    items: list[ItemReservaCreate] = Field(min_length=1, max_length=10)


class ItemReservaOut(BaseModel):
    detalle_id: int
    cantidad_llevada: int | None = None  # se llena al finalizar la reserva
    variante_id: int
    producto_id: int | None
    producto: str | None
    talla: str | None
    color: str | None
    sku: str | None
    cantidad: int


class ReservaOut(BaseModel):
    id: int
    sucursal_id: int
    sucursal: str | None
    ciudad: str | None
    fecha: date
    hora_inicio: time
    hora_fin: time
    duracion_minutos: int
    estado: str
    fecha_creacion: datetime
    items: list[ItemReservaOut] = []


class SlotsDisponibilidad(BaseModel):
    fecha: date
    duracion_minutos: int
    cerrado: bool
    slots: list[time]


# --------------------------------------------------------------------------- #
#  CU18/CU19 — Vista de la sucursal (encargado / admin)
# --------------------------------------------------------------------------- #
class ReservaSucursalOut(ReservaOut):
    cliente_id: int
    cliente: str | None
    cliente_telefono: str | None


class ItemFinalizarIn(BaseModel):
    detalle_id: int
    cantidad_llevada: int = Field(ge=0, le=20)
    # CU17 — si el cliente se prueba la reserva y quiere otra talla/color de
    # la misma prenda, se vende esta variante en vez de la reservada. Se
    # libera la reservada original y se descuenta esta al cerrar la venta.
    variante_sustituta_id: int | None = Field(default=None)


class FinalizarReservaIn(BaseModel):
    items: list[ItemFinalizarIn] = Field(min_length=1)


class FinalizarReservaOut(BaseModel):
    reserva: ReservaSucursalOut
    venta_id: int | None  # None si el cliente devolvió todo
    unidades_llevadas: int
    unidades_devueltas: int
    total: Decimal


class ReservaSucursalPage(BaseModel):
    items: list[ReservaSucursalOut]
    total: int
    page: int
    size: int
