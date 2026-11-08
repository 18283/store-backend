"""Punto de entrada de la API de FashionStore."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import SessionDep

app = FastAPI(title=settings.project_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["root"])
def root() -> dict:
    return {"message": settings.project_name, "docs": "/docs"}


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
def health_db(session: SessionDep) -> dict:
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


# --- Routers por módulo (se agregan a medida que avanzan los casos de uso) ---
from app.modules.cambios.router import router as cambios_router  # noqa: E402
from app.modules.analitica.router import router as analitica_router  # noqa: E402
from app.modules.catalogo.router import router as catalogo_router  # noqa: E402
from app.modules.ia.router import router as ia_router  # noqa: E402
from app.modules.identidad.router import router as identidad_router  # noqa: E402
from app.modules.inventario.router import router as inventario_router  # noqa: E402
from app.modules.offline.router import router as offline_router  # noqa: E402
from app.modules.productos.router import router as productos_router  # noqa: E402
from app.modules.productos.portal_router import (  # noqa: E402
    router as portal_proveedor_router,
)
from app.modules.productos.public_router import (  # noqa: E402
    router as catalogo_publico_router,
)
from app.modules.promociones.router import router as promociones_router  # noqa: E402
from app.modules.proveedores.router import router as proveedores_router  # noqa: E402
from app.modules.reportes.router import router as reportes_router  # noqa: E402
from app.modules.reservas.router import router as reservas_router  # noqa: E402
from app.modules.saas.router import router as saas_router  # noqa: E402
from app.modules.sucursales.router import router as sucursales_router  # noqa: E402
from app.modules.traspasos.router import router as traspasos_router  # noqa: E402
from app.modules.traspasos.notificacion_router import router as notificacion_router  # noqa: E402
from app.modules.ventas.carrito_router import router as carrito_router  # noqa: E402
from app.modules.ventas.ventas_router import router as ventas_router  # noqa: E402

app.include_router(identidad_router)
app.include_router(sucursales_router)
app.include_router(proveedores_router)
app.include_router(catalogo_router)
app.include_router(productos_router)
app.include_router(portal_proveedor_router)
app.include_router(catalogo_publico_router)
app.include_router(inventario_router)
app.include_router(offline_router)
app.include_router(reservas_router)
app.include_router(carrito_router)
app.include_router(ventas_router)
app.include_router(ia_router)
app.include_router(promociones_router)
app.include_router(reportes_router)
app.include_router(saas_router)
app.include_router(cambios_router)
app.include_router(traspasos_router)
app.include_router(notificacion_router)
app.include_router(analitica_router)
