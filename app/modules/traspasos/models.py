"""Modelos del módulo Traspasos — CU24 (código anterior: CU61)."""

from datetime import datetime, timezone

from sqlalchemy import Index
from sqlmodel import Field, SQLModel, UniqueConstraint


class EstadoTraspaso:
    """Estados de una solicitud de traspaso."""

    PENDIENTE = "PENDIENTE"
    APROBADA = "APROBADA"
    APROBADO = "APROBADA"  # alias compatibilidad
    RECHAZADA = "RECHAZADA"
    RECHAZADO = "RECHAZADA"  # alias compatibilidad
    EN_TRANSITO = "EN_TRANSITO"
    ENVIADO = "EN_TRANSITO"  # alias compatibilidad
    RECIBIDO = "RECIBIDO"

    TODOS = (PENDIENTE, APROBADA, RECHAZADA, EN_TRANSITO, RECIBIDO)


class SolicitudTraspaso(SQLModel, table=True):
    __tablename__ = "solicitud_traspaso"
    __table_args__ = (
        UniqueConstraint("empresa_id", "codigo", name="uq_traspaso_empresa_codigo"),
        Index(
            "ix_traspaso_pendiente",
            "empresa_id",
            "variante_id",
            "origen_id",
            "destino_id",
            "estado",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    empresa_id: int = Field(foreign_key="empresa.id", index=True)
    codigo: str = Field(max_length=30, index=True)

    variante_id: int = Field(foreign_key="producto_variante.id", index=True)
    origen_id: int = Field(foreign_key="sucursal.id", index=True)
    destino_id: int = Field(foreign_key="sucursal.id", index=True)
    cantidad: int = Field(gt=0)

    motivo: str | None = Field(default=None, max_length=255)
    estado: str = Field(default=EstadoTraspaso.PENDIENTE, max_length=20, index=True)

    solicitante_id: int = Field(foreign_key="usuario.id", index=True)
    fecha_solicitud: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # CU25 — Aprobación o rechazo por Administrador
    admin_id: int | None = Field(default=None, foreign_key="usuario.id", index=True)
    fecha_decision: datetime | None = Field(default=None)
    observacion_motivo: str | None = Field(default=None, max_length=255)

    # CU26 — Despacho por Encargado de sucursal origen
    despachador_id: int | None = Field(default=None, foreign_key="usuario.id", index=True)
    fecha_despacho: datetime | None = Field(default=None)
    transportista: str | None = Field(default=None, max_length=100)
    numero_guia: str | None = Field(default=None, max_length=100)
    observaciones_despacho: str | None = Field(default=None, max_length=255)


class ModuloNotificacion:
    """Origen del aviso dentro del centro de notificaciones (CU35)."""

    TRASPASOS = "traspasos"
    RESERVAS = "reservas"
    CAMBIOS = "cambios"
    DEVOLUCIONES = "devoluciones"
    SINCRONIZACION = "sincronizacion"
    SUSCRIPCION = "suscripcion"
    MASIVA = "masiva"
    GENERAL = "general"

    TODOS = (TRASPASOS, RESERVAS, CAMBIOS, DEVOLUCIONES, SINCRONIZACION, SUSCRIPCION, MASIVA, GENERAL)


class Notificacion(SQLModel, table=True):
    __tablename__ = "notificacion"

    id: int | None = Field(default=None, primary_key=True)
    usuario_id: int = Field(foreign_key="usuario.id", index=True)
    mensaje: str = Field(max_length=500)
    tipo: str = Field(default="info", max_length=20)
    leida: bool = Field(default=False)
    codigo_traspaso: str | None = Field(default=None, max_length=30, index=True)
    modulo: str | None = Field(default=None, max_length=30, index=True)
    fecha: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))