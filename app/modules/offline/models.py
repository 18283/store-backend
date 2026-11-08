"""Modelos del módulo Offline — cola de sincronización y push (CU11-CU15).

La cola de operaciones pendientes vive en el dispositivo (IndexedDB en la
PWA, SQLite en el móvil). El servidor solo necesita recordar, por clave de
idempotencia, qué ya procesó — así un reintento de sincronización nunca
duplica una reserva o una venta.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, SQLModel


class TipoOperacionOffline:
    RESERVA = "RESERVA"
    VENTA = "VENTA"


class EstadoOperacionOffline:
    CONFIRMADO = "CONFIRMADO"
    RECHAZADO = "RECHAZADO"


class OperacionSincronizada(SQLModel, table=True):
    __tablename__ = "operacion_sincronizada"

    id: int | None = Field(default=None, primary_key=True)
    clave_idempotencia: str = Field(max_length=80, unique=True, index=True)
    usuario_id: int = Field(foreign_key="usuario.id", index=True)
    tipo: str = Field(max_length=20)
    estado: str = Field(max_length=20)

    reserva_id: int | None = Field(default=None, foreign_key="reserva.id")
    venta_id: int | None = Field(default=None, foreign_key="venta.id")
    motivo_rechazo: str | None = Field(default=None, max_length=255)

    fecha_sincronizacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class DispositivoPush(SQLModel, table=True):
    """Token de FCM de un dispositivo — para avisarle al usuario el resultado
    de sincronizar sus reservas/ventas offline (CU14/CU15)."""

    __tablename__ = "dispositivo_push"

    id: int | None = Field(default=None, primary_key=True)
    usuario_id: int = Field(foreign_key="usuario.id", index=True)
    token: str = Field(max_length=255, unique=True, index=True)
    plataforma: str = Field(max_length=20)  # ANDROID | WEB
    fecha_registro: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EstadoReembolsoEfectivo:
    PENDIENTE = "PENDIENTE"
    ENTREGADO = "ENTREGADO"


class ReembolsoEfectivo(SQLModel, table=True):
    """CU16 — Devolución de efectivo pendiente.

    Se crea cuando una venta de contado en EFECTIVO del Cajero (CU12), hecha
    sin conexión, se rechaza al sincronizar (CU14) porque otra operación ya
    se llevó el stock. El cajero ya tiene el efectivo en caja (lo cobró
    físicamente antes de sincronizar) y el cliente ya se llevó la prenda: hay
    que esperar a que el cliente vuelva a la sucursal, devolverle la prenda y
    el efectivo en el mismo trámite, y recién ahí confirmarlo acá.
    """

    __tablename__ = "reembolso_efectivo"

    id: int | None = Field(default=None, primary_key=True)
    venta_id: int = Field(foreign_key="venta.id", unique=True)
    pago_id: int = Field(foreign_key="pago.id")
    monto: Decimal = Field(max_digits=10, decimal_places=2)
    estado: str = Field(default=EstadoReembolsoEfectivo.PENDIENTE, max_length=20)

    # Quien confirma la entrega del efectivo y recibe la prenda de vuelta.
    cajero_confirma_id: int | None = Field(default=None, foreign_key="usuario.id")
    nota: str | None = Field(default=None, max_length=255)

    fecha_creacion: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    fecha_entregado: datetime | None = Field(default=None)
