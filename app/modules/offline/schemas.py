from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field


class VarianteOfflineOut(BaseModel):
    id: int
    sku: str
    talla_id: int
    talla: str
    color_id: int
    color: str
    codigo_hex: str | None
    precio: Decimal | None
    imagen_url: str | None
    cantidad_disponible: int
    cantidad_reservada: int


class ProductoOfflineOut(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    categoria_id: int
    categoria: str
    precio_base: Decimal | None
    imagen_url: str | None
    variantes: list[VarianteOfflineOut]


class ClienteOfflineOut(BaseModel):
    """CU12 (Cajero) — clientes de la empresa, cacheados en el dispositivo
    para poder buscar/elegir uno sin conexión al armar una venta en efectivo."""

    id: int
    nombre: str
    apellido: str
    email: str
    telefono: str | None


class DatosOfflineOut(BaseModel):
    schema_version: int = 1
    version_datos: str
    empresa_id: int
    sucursal_id: int
    sucursal_nombre: str
    generado_en: datetime
    productos: list[ProductoOfflineOut]
    total_productos: int
    total_variantes: int
    clientes: list[ClienteOfflineOut] = []
    total_clientes: int = 0


# --------------------------------------------------------------------------- #
#  CU11/CU12/CU14 — Sincronizar reservas/ventas registradas sin conexión
# --------------------------------------------------------------------------- #
class ItemOfflineIn(BaseModel):
    variante_id: int
    cantidad: int = Field(gt=0, le=20)


class SincronizarReservaIn(BaseModel):
    clave_idempotencia: str = Field(min_length=10, max_length=80)
    sucursal_id: int
    fecha: date
    hora_inicio: time
    duracion_minutos: int = Field(description="30 o 60")
    items: list[ItemOfflineIn] = Field(min_length=1, max_length=10)
    creado_en_dispositivo: datetime


class SincronizarVentaIn(BaseModel):
    clave_idempotencia: str = Field(min_length=10, max_length=80)
    items: list[ItemOfflineIn] = Field(min_length=1, max_length=20)
    direccion_entrega: str = Field(min_length=5, max_length=200)
    referencia_entrega: str | None = Field(default=None, max_length=150)
    creado_en_dispositivo: datetime


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Venta de contado en EFECTIVO del Cajero, registrada sin
#  conexión (cliente presencial, sin tarjeta guardada: el pago ya se cobró
#  físicamente antes de sincronizar).
# --------------------------------------------------------------------------- #
class SincronizarVentaEfectivoIn(BaseModel):
    clave_idempotencia: str = Field(min_length=10, max_length=80)
    cliente_id: int = Field(description="Cliente presencial, buscado/creado antes de armar la venta")
    items: list[ItemOfflineIn] = Field(min_length=1, max_length=20)
    monto_recibido: Decimal = Field(gt=0)
    creado_en_dispositivo: datetime


class ResultadoSincronizacionOut(BaseModel):
    clave_idempotencia: str
    tipo: str  # RESERVA | VENTA
    estado: str  # CONFIRMADO | RECHAZADO
    motivo_rechazo: str | None = None
    reserva_id: int | None = None
    venta_id: int | None = None


# --------------------------------------------------------------------------- #
#  CU14/CU15 — Registro del dispositivo para avisar el resultado por push
# --------------------------------------------------------------------------- #
class DispositivoPushIn(BaseModel):
    token: str = Field(min_length=10, max_length=255)
    plataforma: str = Field(description="ANDROID o WEB")


# --------------------------------------------------------------------------- #
#  CU16 — Devolución de efectivo pendiente (conflicto de una venta offline
#  en efectivo) y su confirmación por el Cajero.
# --------------------------------------------------------------------------- #
class ReembolsoEfectivoOut(BaseModel):
    id: int
    venta_id: int
    cliente_nombre: str
    sucursal_id: int
    sucursal: str
    monto: Decimal
    estado: str
    fecha_creacion: datetime
    fecha_entregado: datetime | None


class ReembolsoEfectivoPage(BaseModel):
    items: list[ReembolsoEfectivoOut]
    total: int


class ConfirmarReembolsoEfectivoIn(BaseModel):
    nota: str | None = Field(default=None, max_length=255)
