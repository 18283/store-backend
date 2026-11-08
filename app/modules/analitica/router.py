"""Endpoints del módulo Analítica inteligente — CU31/CU32/CU33/CU34."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.deps import SessionDep, require_roles
from app.modules.analitica import service
from app.modules.analitica.schemas import (
    AgrupacionesOut,
    DashboardAnaliticoOut,
    EntrenarModeloIn,
    ModeloAnaliticoOut,
    PrediccionDemandaOut,
)
from app.modules.identidad.models import RolNombre, Usuario
from app.modules.saas.models import CodigoFuncionalidad
from app.modules.saas.service import validar_acceso_plan

router = APIRouter(prefix="/analitica", tags=["analitica"])

AdminUser = Annotated[Usuario, Depends(require_roles(RolNombre.ADMINISTRADOR))]
SuperadminUser = Annotated[
    Usuario, Depends(require_roles(RolNombre.SUPERADMINISTRADOR))
]


def _validar_acceso_analitica(session: SessionDep, admin: Usuario) -> None:
    if admin.empresa_id is None:
        raise HTTPException(403, "El usuario no está asociado a una empresa")
    validar_acceso_plan(
        session, admin.empresa_id, funcionalidad=CodigoFuncionalidad.REPORTES_IA
    )


@router.get("/dashboard", response_model=DashboardAnaliticoOut)
def dashboard(session: SessionDep, admin: AdminUser):  # CU31
    _validar_acceso_analitica(session, admin)
    return service.dashboard(session, admin.empresa_id)


@router.get("/prediccion-demanda", response_model=PrediccionDemandaOut)
def prediccion_demanda(
    session: SessionDep,
    admin: AdminUser,
    semanas: int = Query(default=service.SEMANAS_DEFECTO, ge=4, le=52),
):  # CU32
    _validar_acceso_analitica(session, admin)
    return service.predecir_demanda(session, admin.empresa_id, semanas)


@router.get("/agrupaciones", response_model=AgrupacionesOut)
def agrupaciones(
    session: SessionDep,
    admin: AdminUser,
    dias: int = Query(default=service.DIAS_AGRUPACION_DEFECTO, ge=7, le=365),
):  # CU33
    _validar_acceso_analitica(session, admin)
    return service.agrupar_productos(session, admin.empresa_id, dias)


@router.post("/modelos/entrenar", response_model=ModeloAnaliticoOut)
def entrenar_modelo(
    data: EntrenarModeloIn, session: SessionDep, superadmin: SuperadminUser
):  # CU34
    return service.entrenar_modelo(session, superadmin, data)


@router.get("/modelos", response_model=list[ModeloAnaliticoOut])
def listar_modelos(session: SessionDep, superadmin: SuperadminUser):  # CU34
    return service.listar_modelos(session)
