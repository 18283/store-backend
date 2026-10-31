"""Punto único de importación de TODOS los modelos.

Alembic importa este módulo para poblar `SQLModel.metadata` y detectar cambios.
Cada vez que se agrega un módulo con tablas nuevas, se agrega su import acá.
"""

from app.modules.catalogo import models as catalogo  # noqa: F401
from app.modules.ia import models as ia  # noqa: F401
from app.modules.identidad import models as identidad  # noqa: F401
from app.modules.inventario import models as inventario  # noqa: F401
from app.modules.offline import models as offline  # noqa: F401
from app.modules.productos import models as productos  # noqa: F401
from app.modules.promociones import models as promociones  # noqa: F401
from app.modules.proveedores import models as proveedores  # noqa: F401
from app.modules.reservas import models as reservas  # noqa: F401
from app.modules.saas import models as saas  # noqa: F401
from app.modules.sucursales import models as sucursales  # noqa: F401
from app.modules.ventas import models as ventas  # noqa: F401
