"""Modelos del módulo Identidad: Rol y Usuario.

Cubre a todos los actores que inician sesión (Cliente, Administrador,
Encargado de sucursal, Cajero, Proveedor).
"""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class RolNombre:
    """Nombres de rol válidos (se siembran en el seed)."""

    SUPERADMINISTRADOR = "Superadministrador"
    CLIENTE = "Cliente"
    ADMINISTRADOR = "Administrador"
    ENCARGADO = "EncargadoSucursal"
    CAJERO = "Cajero"
    PROVEEDOR = "Proveedor"

    TODOS = (
        SUPERADMINISTRADOR,
        CLIENTE,
        ADMINISTRADOR,
        ENCARGADO,
        CAJERO,
        PROVEEDOR,
    )


class Rol(SQLModel, table=True):
    __tablename__ = "rol"

    id: int | None = Field(default=None, primary_key=True)
    nombre: str = Field(max_length=30, unique=True, index=True)


class Usuario(SQLModel, table=True):
    __tablename__ = "usuario"

    id: int | None = Field(default=None, primary_key=True)
    nombre: str = Field(max_length=80)
    apellido: str = Field(max_length=80)
    email: str = Field(max_length=120, unique=True, index=True)
    password_hash: str = Field(max_length=255)
    telefono: str | None = Field(default=None, max_length=20)

    # NULL únicamente para el Superadministrador de plataforma.
    empresa_id: int | None = Field(default=None, foreign_key="empresa.id", index=True)
    rol_id: int = Field(foreign_key="rol.id")
    sucursal_id: int | None = Field(default=None, foreign_key="sucursal.id")
    proveedor_id: int | None = Field(default=None, foreign_key="proveedor.id")

    fecha_registro: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    activo: bool = Field(default=True)

    # Tarjeta guardada (CU12/CU14): permite cobrar sin que el cliente esté
    # presente, recién cuando el dispositivo offline recupera conexión.
    stripe_customer_id: str | None = Field(default=None, max_length=255)
    stripe_payment_method_id: str | None = Field(default=None, max_length=255)
