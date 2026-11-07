"""Endpoints del módulo Reportes — CU30 (Exportar Excel/PDF) y CU31 (Reportes de Ventas e Inventario)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.core.deps import SessionDep, require_roles
from app.modules.identidad.models import RolNombre, Usuario
from app.modules.reportes import service
from app.modules.reportes.schemas import FormatoExportacion, ReporteInventarioOut, ReporteVentasOut, TipoReporteExportacion
from app.modules.saas.models import CodigoFuncionalidad
from app.modules.saas.service import validar_acceso_plan

router = APIRouter(prefix="/reportes", tags=["reportes"])

AdminUser = Annotated[Usuario, Depends(require_roles(RolNombre.ADMINISTRADOR))]


def _validar_reporte(session: SessionDep, admin: Usuario, sucursal_id: int | None) -> None:
    if admin.empresa_id is None:
        raise HTTPException(403, "El usuario no está asociado a una empresa")
    validar_acceso_plan(
        session,
        admin.empresa_id,
        funcionalidad=CodigoFuncionalidad.REPORTES_CONVENCIONALES,
    )
    if sucursal_id is None:
        validar_acceso_plan(
            session,
            admin.empresa_id,
            funcionalidad=CodigoFuncionalidad.REPORTES_MULTISUCURSAL,
        )


@router.get("/ventas", response_model=ReporteVentasOut)
def reporte_ventas(  # CU31
    session: SessionDep,
    admin: AdminUser,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    sucursal_id: int | None = None,
):
    _validar_reporte(session, admin, sucursal_id)
    return service.reporte_ventas(
        session,
        admin.id,
        fecha_desde,
        fecha_hasta,
        sucursal_id,
        empresa_id=admin.empresa_id,
    )


@router.get("/inventario", response_model=ReporteInventarioOut)
def reporte_inventario(  # CU31
    session: SessionDep,
    admin: AdminUser,
    sucursal_id: int | None = None,
    umbral: int = Query(default=3, ge=0, le=1000),
):
    _validar_reporte(session, admin, sucursal_id)
    return service.reporte_inventario(
        session,
        admin.id,
        sucursal_id,
        umbral,
        empresa_id=admin.empresa_id,
    )


def _descargar(contenido: bytes, nombre_archivo: str, media_type: str) -> Response:
    return Response(
        content=contenido,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


@router.get("/exportar")  # CU30 — endpoint dinámico único
def exportar_reporte(
    session: SessionDep,
    admin: AdminUser,
    tipo_reporte: TipoReporteExportacion = Query(description="ventas o inventario"),
    formato: FormatoExportacion = Query(description="xlsx (Excel) o pdf"),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    sucursal_id: int | None = None,
    umbral: int = Query(default=3, ge=0, le=1000),
):
    """CU30 — Exporta un reporte dinámico (ventas o inventario) a Excel o PDF.

    Valida rol de Administrador y el plan SaaS (REPORTES_CONVENCIONALES,
    más REPORTES_MULTISUCURSAL si no se filtra sucursal), calcula los
    totales desde la base de datos y devuelve el archivo para descarga.
    """
    _validar_reporte(session, admin, sucursal_id)
    contenido, nombre_archivo, media_type = service.exportar_reporte(
        session,
        admin.id,
        tipo_reporte.value,
        formato.value,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        sucursal_id=sucursal_id,
        umbral=umbral,
        empresa_id=admin.empresa_id,
    )
    return _descargar(contenido, nombre_archivo, media_type)


@router.get("/ventas/exportar")  # CU30 — atajo de ventas
def exportar_ventas(
    session: SessionDep,
    admin: AdminUser,
    formato: FormatoExportacion = Query(description="xlsx (Excel) o pdf"),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    sucursal_id: int | None = None,
):
    _validar_reporte(session, admin, sucursal_id)
    contenido, nombre_archivo, media_type = service.exportar_reporte(
        session,
        admin.id,
        "ventas",
        formato.value,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        sucursal_id=sucursal_id,
        empresa_id=admin.empresa_id,
    )
    return _descargar(contenido, nombre_archivo, media_type)


@router.get("/inventario/exportar")  # CU30 — atajo de inventario
def exportar_inventario(
    session: SessionDep,
    admin: AdminUser,
    formato: FormatoExportacion = Query(description="xlsx (Excel) o pdf"),
    sucursal_id: int | None = None,
    umbral: int = Query(default=3, ge=0, le=1000),
):
    _validar_reporte(session, admin, sucursal_id)
    contenido, nombre_archivo, media_type = service.exportar_reporte(
        session,
        admin.id,
        "inventario",
        formato.value,
        sucursal_id=sucursal_id,
        umbral=umbral,
        empresa_id=admin.empresa_id,
    )
    return _descargar(contenido, nombre_archivo, media_type)
