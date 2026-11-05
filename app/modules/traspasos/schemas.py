"""Esquemas del módulo Traspasos — CU24 (código anterior: CU61)."""

from datetime import datetime

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  Paso 2 — GET /traspasos/opciones
# --------------------------------------------------------------------------- #
class SucursalTraspasoOut(BaseModel):
    id: int
    nombre: str
    ciudad: str


class OpcionesTraspasoOut(BaseModel):
    rol: str
    empresa_id: int
    # El rol Cliente no está atado a una sucursal; el Encargado sí.
    cliente_id: int | None = None
    sucursal_asignada_id: int | None = None
    # Todas las sucursales activas de la empresa (posibles orígenes).
    sucursales: list[SucursalTraspasoOut]
    # El Cliente elige el destino (null); el Encargado solo puede apuntar a la
    # propia, así que este campo lleva su id.
    destino_permitido_id: int | None = None
    # Se conserva por compatibilidad; el frontend usa `destino_permitido_id`.
    destinos_permitidos: list[SucursalTraspasoOut]


# --------------------------------------------------------------------------- #
#  Paso 12 — GET /traspasos/variantes?q=...
# --------------------------------------------------------------------------- #
class VarianteConStockOut(BaseModel):
    """Una fila por variante + sucursal de origen con stock disponible.

    El frontend agrupa estas filas por `variante_id` para mostrar todos los
    orígenes posibles. No se anidan las sucursales para mantener el contrato
    plano que consume la UI.
    """

    variante_id: int
    producto_id: int | None = None
    producto: str
    sku: str | None = None
    talla: str | None = None
    color: str | None = None
    color_hex: str | None = None
    origen_id: int
    origen: str
    ciudad: str
    cantidad_disponible: int


class VariantesConStockOut(BaseModel):
    items: list[VarianteConStockOut]
    total: int


# --------------------------------------------------------------------------- #
#  Paso 22 — POST /traspasos
# --------------------------------------------------------------------------- #
class SolicitudTraspasoCrearIn(BaseModel):
    variante_id: int
    origen_id: int
    destino_id: int
    cantidad: int = Field(gt=0)
    motivo: str | None = Field(
        default=None,
        max_length=255,
        description="Motivo u observación de la solicitud",
    )
    # Paso 33: si el destino YA tiene stock suficiente, la API pide una
    # confirmación explícita antes de registrar la solicitud.
    confirmar_stock_destino: bool = Field(
        default=False,
        description=(
            "Confirmar que se quiere pedir el traspaso aunque el destino ya "
            "tenga stock suficiente"
        ),
    )
    confirmar_stock_suficiente: bool = Field(
        default=False,
        description="Alias de confirmar_stock_destino",
    )


class SolicitudTraspasoOut(BaseModel):
    id: int
    codigo: str
    estado: str
    resumen: str

    variante_id: int
    # Texto legible "Producto · Talla · Color" para la tarjeta de resumen.
    variante: str | None = None
    producto_id: int
    producto: str
    sku: str
    talla: str
    color: str
    color_hex: str | None = None
    imagen_url: str | None = None

    origen_id: int
    origen: str
    ciudad_origen: str
    destino_id: int
    destino: str
    ciudad_destino: str

    cantidad: int
    motivo: str | None = None
    solicitante_id: int
    solicitante: str
    solicitante_rol: str | None = None
    fecha_solicitud: datetime

    # CU25 — Aprobación / Rechazo
    admin_id: int | None = None
    admin_nombre: str | None = None
    fecha_decision: datetime | None = None
    observacion_motivo: str | None = None

    # CU26 — Despacho
    despachador_id: int | None = None
    despachador_nombre: str | None = None
    fecha_despacho: datetime | None = None
    transportista: str | None = None
    numero_guia: str | None = None
    observaciones_despacho: str | None = None

    # Stock al momento de crear/consultar (informativo; no se modifica).
    stock_origen: int | None = None
    stock_destino: int | None = None

    # Presente en respuestas de "necesita confirmación" (ver exceptions.py).
    requiere_confirmacion: bool = False
    mensaje: str | None = None


# --------------------------------------------------------------------------- #
#  CU25 — Esquemas para Aprobar o Rechazar Traspaso
# --------------------------------------------------------------------------- #
class SolicitudTraspasoDecisionIn(BaseModel):
    decision: str = Field(
        description="Decisión: APROBAR o RECHAZAR (o APROBADA / RECHAZADA)"
    )
    observacion_motivo: str | None = Field(
        default=None,
        max_length=255,
        description="Motivo de rechazo u observación opcional de aprobación",
    )
    motivo: str | None = Field(
        default=None,
        max_length=255,
        description="Alias de observacion_motivo",
    )


class SolicitudTraspasoPage(BaseModel):
    items: list[SolicitudTraspasoOut]
    total: int
    page: int
    size: int


# --------------------------------------------------------------------------- #
#  CU26 — Esquemas para Despachar Traspaso
# --------------------------------------------------------------------------- #
class DatosEnvioOut(BaseModel):
    transportista: str | None = None
    numero_guia: str | None = None
    fecha_envio: str | None = None  # Fecha separada (ej. 2026-11-04)
    hora_envio: str | None = None   # Hora separada (ej. 14:30:00)
    fecha_hora: str | None = None   # Formato ISO combinado (compatibilidad)
    observaciones: str | None = None


class SolicitudTraspasoDespachoIn(BaseModel):
    """Datos de entrada para el despacho de una solicitud de traspaso."""
    datos_envio: DatosEnvioOut | None = None


class SolicitudTraspasoDespachoOut(BaseModel):
    """Respuesta después de intentar el despacho."""
    solicitud_id: int
    id: int | None = None
    codigo: str
    estado_anterior: str
    estado_nuevo: str
    estado: str | None = None
    mensaje: str
    requiere_accion: bool = False
    cantidad_descontada: int = 0
    movimiento_registrado: bool = False
    transportista: str | None = None
    numero_guia: str | None = None
    fecha_despacho: datetime | None = None
    observaciones: str | None = None
    observaciones_despacho: str | None = None
