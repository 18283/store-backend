"""Reglas de negocio SaaS y coordinación transaccional de CU01-CU07."""

import calendar
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, or_, select

from app.core.security import hash_password
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.saas.models import (
    CodigoLimite,
    Empresa,
    EstadoSuscripcion,
    FuncionalidadPlan,
    HistorialCambioPlan,
    LimitePlan,
    Plan,
    Suscripcion,
)
from app.modules.saas.schemas import (
    CambioPlanIn,
    CambioPlanOut,
    CambioPlanPreviewOut,
    ConsumoOut,
    EmpresaCreate,
    EmpresaOut,
    EmpresaUpdate,
    FuncionalidadPlanOut,
    HistorialCambioPlanOut,
    IncompatibilidadPlanOut,
    LimitePlanOut,
    PlanConsumoOut,
    PlanOut,
    PlanUpdate,
    SuscripcionGestionIn,
    SuscripcionOut,
)


def _sumar_meses(fecha: date, meses: int) -> date:
    indice = fecha.month - 1 + meses
    anio = fecha.year + indice // 12
    mes = indice % 12 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)


def _plan_or_404(session: Session, plan_id: int, *, activo: bool = False) -> Plan:
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan no encontrado")
    if activo and not plan.activo:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El plan está inactivo")
    return plan


def empresa_or_404(session: Session, empresa_id: int) -> Empresa:
    empresa = session.get(Empresa, empresa_id)
    if empresa is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no encontrada")
    return empresa


def _suscripcion_actual(session: Session, empresa_id: int) -> Suscripcion | None:
    return session.exec(
        select(Suscripcion)
        .where(Suscripcion.empresa_id == empresa_id)
        .order_by(Suscripcion.id.desc())
    ).first()


def _suscripcion_actual_bloqueada(
    session: Session, empresa_id: int
) -> Suscripcion | None:
    """Bloquea la fila durante cambios críticos; SQLite ignora FOR UPDATE."""
    return session.exec(
        select(Suscripcion)
        .where(Suscripcion.empresa_id == empresa_id)
        .order_by(Suscripcion.id.desc())
        .with_for_update()
    ).first()


def to_plan_out(session: Session, plan: Plan) -> PlanOut:
    limites = session.exec(
        select(LimitePlan)
        .where(LimitePlan.plan_id == plan.id)
        .order_by(LimitePlan.codigo)
    ).all()
    funcionalidades = session.exec(
        select(FuncionalidadPlan)
        .where(FuncionalidadPlan.plan_id == plan.id)
        .order_by(FuncionalidadPlan.codigo)
    ).all()
    return PlanOut(
        id=plan.id,
        codigo=plan.codigo,
        nombre=plan.nombre,
        descripcion=plan.descripcion,
        precio_mensual=plan.precio_mensual,
        activo=plan.activo,
        fecha_creacion=plan.fecha_creacion,
        limites=[LimitePlanOut.model_validate(x, from_attributes=True) for x in limites],
        funcionalidades=[
            FuncionalidadPlanOut.model_validate(x, from_attributes=True)
            for x in funcionalidades
        ],
    )


def to_suscripcion_out(
    session: Session, suscripcion: Suscripcion
) -> SuscripcionOut:
    empresa = empresa_or_404(session, suscripcion.empresa_id)
    plan = _plan_or_404(session, suscripcion.plan_id)
    estado = suscripcion.estado
    if estado == EstadoSuscripcion.ACTIVA and suscripcion.fecha_vencimiento < date.today():
        estado = EstadoSuscripcion.VENCIDA
    return SuscripcionOut(
        id=suscripcion.id,
        empresa_id=empresa.id,
        empresa_nombre=empresa.nombre,
        plan_id=plan.id,
        plan_nombre=plan.nombre,
        fecha_inicio=suscripcion.fecha_inicio,
        fecha_vencimiento=suscripcion.fecha_vencimiento,
        estado=estado,
        fecha_creacion=suscripcion.fecha_creacion,
        fecha_actualizacion=suscripcion.fecha_actualizacion,
    )


def to_empresa_out(session: Session, empresa: Empresa) -> EmpresaOut:
    suscripcion = _suscripcion_actual(session, empresa.id)
    plan = session.get(Plan, suscripcion.plan_id) if suscripcion else None
    suscripcion_out = (
        to_suscripcion_out(session, suscripcion) if suscripcion is not None else None
    )
    return EmpresaOut(
        id=empresa.id,
        identificador=empresa.identificador,
        nombre=empresa.nombre,
        razon_social=empresa.razon_social,
        activa=empresa.activa,
        fecha_registro=empresa.fecha_registro,
        fecha_actualizacion=empresa.fecha_actualizacion,
        plan_nombre=plan.nombre if plan else None,
        suscripcion_estado=suscripcion_out.estado if suscripcion_out else None,
        fecha_vencimiento=suscripcion.fecha_vencimiento if suscripcion else None,
    )


# ---------------------------------------------------------------------------
# CU38 y CU39 — empresas contratantes
# ---------------------------------------------------------------------------
def list_empresas(
    session: Session,
    *,
    q: str | None = None,
    activa: bool | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Empresa], int]:
    base = select(Empresa)
    if q:
        patron = f"%{q.strip().lower()}%"
        base = base.where(
            or_(
                func.lower(Empresa.nombre).like(patron),
                func.lower(Empresa.identificador).like(patron),
                func.lower(Empresa.razon_social).like(patron),
            )
        )
    if activa is not None:
        base = base.where(Empresa.activa == activa)
    total = session.exec(select(func.count()).select_from(base.subquery())).one()
    items = session.exec(
        base.order_by(Empresa.nombre).offset((page - 1) * size).limit(size)
    ).all()
    return items, total


def create_empresa(
    session: Session, data: EmpresaCreate, *, actor_id: int
) -> Empresa:
    if session.exec(
        select(Empresa).where(Empresa.identificador == data.identificador)
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "La empresa ya está registrada")
    if session.exec(select(Usuario).where(Usuario.email == str(data.administrador.email))).first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ya existe un usuario con el email del administrador",
        )
    plan = _plan_or_404(session, data.plan_id, activo=True)
    rol_admin = session.exec(
        select(Rol).where(Rol.nombre == RolNombre.ADMINISTRADOR)
    ).first()
    if rol_admin is None:
        raise HTTPException(500, "El rol Administrador no está configurado")

    try:
        ahora = datetime.now(timezone.utc)
        empresa = Empresa(
            identificador=data.identificador,
            nombre=data.nombre,
            razon_social=data.razon_social,
            fecha_registro=ahora,
            fecha_actualizacion=ahora,
        )
        session.add(empresa)
        session.flush()
        admin = Usuario(
            nombre=data.administrador.nombre,
            apellido=data.administrador.apellido,
            email=str(data.administrador.email),
            password_hash=hash_password(data.administrador.password),
            telefono=data.administrador.telefono,
            rol_id=rol_admin.id,
            empresa_id=empresa.id,
        )
        session.add(admin)
        suscripcion = Suscripcion(
            empresa_id=empresa.id,
            plan_id=plan.id,
            fecha_inicio=data.fecha_inicio,
            fecha_vencimiento=_sumar_meses(data.fecha_inicio, data.vigencia_meses),
            estado=EstadoSuscripcion.ACTIVA,
            fecha_creacion=ahora,
            fecha_actualizacion=ahora,
        )
        session.add(suscripcion)
        session.flush()
        session.add(
            HistorialCambioPlan(
                suscripcion_id=suscripcion.id,
                plan_nuevo_id=plan.id,
                usuario_id=actor_id,
                tipo_cambio="ASIGNACION",
                observacion="Suscripción inicial creada con CU38",
            )
        )
        session.commit()
        session.refresh(empresa)
        return empresa
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La empresa o el administrador ya existen"
        ) from exc
    except Exception:
        session.rollback()
        raise


def update_empresa(
    session: Session, empresa_id: int, data: EmpresaUpdate
) -> Empresa:
    empresa = empresa_or_404(session, empresa_id)
    cambios = data.model_dump(exclude_unset=True)
    try:
        cambio_estado = "activa" in cambios and cambios["activa"] != empresa.activa
        for campo, valor in cambios.items():
            setattr(empresa, campo, valor)
        empresa.fecha_actualizacion = datetime.now(timezone.utc)
        session.add(empresa)

        if cambio_estado:
            suscripcion = _suscripcion_actual(session, empresa.id)
            if suscripcion:
                if not empresa.activa and suscripcion.estado == EstadoSuscripcion.ACTIVA:
                    suscripcion.estado = EstadoSuscripcion.SUSPENDIDA
                elif empresa.activa and suscripcion.estado == EstadoSuscripcion.SUSPENDIDA:
                    suscripcion.estado = EstadoSuscripcion.ACTIVA
                suscripcion.fecha_actualizacion = datetime.now(timezone.utc)
                session.add(suscripcion)
        session.commit()
        session.refresh(empresa)
        return empresa
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------------
# CU40 — planes, límites y funcionalidades
# ---------------------------------------------------------------------------
def list_planes(session: Session, *, solo_activos: bool = False) -> list[Plan]:
    query = select(Plan)
    if solo_activos:
        query = query.where(Plan.activo == True)  # noqa: E712
    return session.exec(query.order_by(Plan.id)).all()


def update_plan(session: Session, plan_id: int, data: PlanUpdate) -> Plan:
    plan = _plan_or_404(session, plan_id)
    cambios = data.model_dump(exclude_unset=True, exclude={"limites", "funcionalidades"})
    try:
        for campo, valor in cambios.items():
            setattr(plan, campo, valor)
        session.add(plan)

        if data.limites is not None:
            existentes = session.exec(
                select(LimitePlan).where(LimitePlan.plan_id == plan.id)
            ).all()
            for item in existentes:
                session.delete(item)
            session.flush()
            session.add_all(
                [LimitePlan(plan_id=plan.id, **x.model_dump()) for x in data.limites]
            )

        if data.funcionalidades is not None:
            existentes = session.exec(
                select(FuncionalidadPlan).where(FuncionalidadPlan.plan_id == plan.id)
            ).all()
            for item in existentes:
                session.delete(item)
            session.flush()
            session.add_all(
                [
                    FuncionalidadPlan(plan_id=plan.id, **x.model_dump())
                    for x in data.funcionalidades
                ]
            )
        session.commit()
        session.refresh(plan)
        return plan
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "La configuración contiene códigos duplicados") from exc
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------------
# CU41 — suscripciones
# ---------------------------------------------------------------------------
def list_suscripciones(session: Session, *, q: str | None = None) -> list[Suscripcion]:
    query = select(Suscripcion).join(Empresa, Empresa.id == Suscripcion.empresa_id)
    if q:
        patron = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Empresa.nombre).like(patron),
                func.lower(Empresa.identificador).like(patron),
            )
        )
    return session.exec(query.order_by(Empresa.nombre, Suscripcion.id.desc())).all()


def gestionar_suscripcion(
    session: Session, data: SuscripcionGestionIn, *, actor_id: int
) -> Suscripcion:
    empresa = empresa_or_404(session, data.empresa_id)
    plan = _plan_or_404(session, data.plan_id, activo=True)
    actual = _suscripcion_actual(session, empresa.id)
    ahora = datetime.now(timezone.utc)
    try:
        plan_anterior_id = actual.plan_id if actual else None
        if actual is None:
            actual = Suscripcion(
                empresa_id=empresa.id,
                plan_id=plan.id,
                fecha_inicio=data.fecha_inicio,
                fecha_vencimiento=_sumar_meses(data.fecha_inicio, data.vigencia_meses),
                estado=data.estado,
                fecha_creacion=ahora,
                fecha_actualizacion=ahora,
            )
            session.add(actual)
            tipo = "ASIGNACION"
        else:
            actual.plan_id = plan.id
            actual.fecha_inicio = data.fecha_inicio
            actual.fecha_vencimiento = _sumar_meses(
                data.fecha_inicio, data.vigencia_meses
            )
            actual.estado = data.estado
            actual.fecha_actualizacion = ahora
            session.add(actual)
            tipo = "CAMBIO" if plan_anterior_id != plan.id else "RENOVACION"
        session.flush()
        session.add(
            HistorialCambioPlan(
                suscripcion_id=actual.id,
                plan_anterior_id=plan_anterior_id,
                plan_nuevo_id=plan.id,
                usuario_id=actor_id,
                tipo_cambio=tipo,
                observacion=data.observacion,
            )
        )
        empresa.activa = data.estado == EstadoSuscripcion.ACTIVA
        empresa.fecha_actualizacion = ahora
        session.add(empresa)
        session.commit()
        session.refresh(actual)
        # CU35 — Avisar a los Administradores de la empresa.
        from app.modules.traspasos.notificacion_service import crear_notificacion_para_roles_segura

        crear_notificacion_para_roles_segura(
            session=session,
            roles=[RolNombre.ADMINISTRADOR],
            mensaje=f"Tu suscripción fue actualizada (estado: {actual.estado}, vence {actual.fecha_vencimiento}).",
            tipo="info",
            empresa_id=empresa.id,
            modulo="suscripcion",
        )
        return actual
    except Exception:
        session.rollback()
        raise


# ---------------------------------------------------------------------------
# CU42 — plan, vigencia y consumo del tenant autenticado
# ---------------------------------------------------------------------------
def _consumo_recurso(session: Session, empresa_id: int, codigo: str) -> int:
    from app.modules.ia.models import ReporteGenerado
    from app.modules.sucursales.models import Sucursal

    if codigo == CodigoLimite.MAX_SUCURSALES:
        return session.exec(
            select(func.count()).select_from(Sucursal).where(
                Sucursal.empresa_id == empresa_id, Sucursal.activa == True  # noqa: E712
            )
        ).one()
    if codigo == CodigoLimite.MAX_USUARIOS:
        return session.exec(
            select(func.count()).select_from(Usuario).where(
                Usuario.empresa_id == empresa_id, Usuario.activo == True  # noqa: E712
            )
        ).one()
    if codigo == CodigoLimite.MAX_REPORTES_IA_MES:
        hoy = date.today()
        inicio_mes = datetime(hoy.year, hoy.month, 1, tzinfo=timezone.utc)
        return session.exec(
            select(func.count())
            .select_from(ReporteGenerado)
            .join(Usuario, Usuario.id == ReporteGenerado.usuario_id)
            .where(
                Usuario.empresa_id == empresa_id,
                ReporteGenerado.fecha_generacion >= inicio_mes,
            )
        ).one()
    raise ValueError("Métrica no implementada")


_ETIQUETAS = {
    CodigoLimite.MAX_SUCURSALES: "Sucursales activas",
    CodigoLimite.MAX_USUARIOS: "Usuarios activos",
    CodigoLimite.MAX_REPORTES_IA_MES: "Reportes con IA este mes",
}


def validar_acceso_plan(
    session: Session,
    empresa_id: int,
    *,
    funcionalidad: str | None = None,
    limite_codigo: str | None = None,
    incremento: int = 0,
) -> Suscripcion:
    """CU07: valida suscripción, funcionalidad y consumo de forma reutilizable."""
    empresa = empresa_or_404(session, empresa_id)
    if not empresa.activa:
        raise HTTPException(403, "La empresa está suspendida")
    suscripcion = _suscripcion_actual(session, empresa_id)
    if suscripcion is None:
        raise HTTPException(403, "La empresa no tiene una suscripción")
    if suscripcion.estado != EstadoSuscripcion.ACTIVA:
        raise HTTPException(403, f"La suscripción está {suscripcion.estado.lower()}")
    if suscripcion.fecha_vencimiento < date.today():
        raise HTTPException(403, "La suscripción está vencida")

    if funcionalidad is not None:
        configuracion = session.exec(
            select(FuncionalidadPlan).where(
                FuncionalidadPlan.plan_id == suscripcion.plan_id,
                FuncionalidadPlan.codigo == funcionalidad,
            )
        ).first()
        if configuracion is None or not configuracion.habilitada:
            raise HTTPException(
                403, f"La funcionalidad {funcionalidad} no está incluida en el plan"
            )

    if limite_codigo is not None:
        limite = session.exec(
            select(LimitePlan).where(
                LimitePlan.plan_id == suscripcion.plan_id,
                LimitePlan.codigo == limite_codigo,
            )
        ).first()
        if limite is not None:
            try:
                usado = _consumo_recurso(session, empresa_id, limite_codigo)
            except Exception as exc:
                raise HTTPException(
                    503,
                    "No se pudo medir el consumo del plan; inténtelo nuevamente",
                ) from exc
            if usado + incremento > limite.valor_maximo:
                etiqueta = _ETIQUETAS.get(limite_codigo, limite_codigo)
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Límite alcanzado para {etiqueta}: uso actual {usado}, "
                    f"máximo {limite.valor_maximo}",
                )
    return suscripcion


def validar_limite_disponible(
    session: Session, empresa_id: int, codigo: str
) -> None:
    validar_acceso_plan(
        session, empresa_id, limite_codigo=codigo, incremento=1
    )


def _incompatibilidades_plan(
    session: Session, empresa_id: int, plan: Plan
) -> list[IncompatibilidadPlanOut]:
    incompatibilidades: list[IncompatibilidadPlanOut] = []
    limites = session.exec(
        select(LimitePlan).where(LimitePlan.plan_id == plan.id)
    ).all()
    for limite in limites:
        etiqueta = _ETIQUETAS.get(limite.codigo, limite.codigo)
        try:
            utilizado = _consumo_recurso(session, empresa_id, limite.codigo)
        except Exception:
            incompatibilidades.append(
                IncompatibilidadPlanOut(
                    codigo=limite.codigo,
                    etiqueta=etiqueta,
                    utilizado=None,
                    limite=limite.valor_maximo,
                    mensaje=f"No se pudo medir {etiqueta}",
                )
            )
            continue
        if utilizado > limite.valor_maximo:
            incompatibilidades.append(
                IncompatibilidadPlanOut(
                    codigo=limite.codigo,
                    etiqueta=etiqueta,
                    utilizado=utilizado,
                    limite=limite.valor_maximo,
                    mensaje=(
                        f"{etiqueta}: se utilizan {utilizado} y el plan permite "
                        f"{limite.valor_maximo}"
                    ),
                )
            )
    return incompatibilidades


def preview_cambio_plan(
    session: Session, empresa_id: int, data: CambioPlanIn
) -> CambioPlanPreviewOut:
    actual = validar_acceso_plan(session, empresa_id)
    plan_actual = _plan_or_404(session, actual.plan_id)
    plan_nuevo = _plan_or_404(session, data.plan_id, activo=True)
    tipo = "RENOVACION" if actual.plan_id == plan_nuevo.id else "CAMBIO"
    hoy = date.today()
    if tipo == "RENOVACION":
        fecha_inicio = actual.fecha_inicio
        fecha_vencimiento = _sumar_meses(
            max(actual.fecha_vencimiento, hoy), data.vigencia_meses
        )
    else:
        fecha_inicio = hoy
        fecha_vencimiento = _sumar_meses(hoy, data.vigencia_meses)
    incompatibilidades = _incompatibilidades_plan(session, empresa_id, plan_nuevo)
    return CambioPlanPreviewOut(
        empresa_id=empresa_id,
        suscripcion_id=actual.id,
        plan_actual=to_plan_out(session, plan_actual),
        plan_nuevo=to_plan_out(session, plan_nuevo),
        tipo_cambio=tipo,
        fecha_inicio_nueva=fecha_inicio,
        fecha_vencimiento_nueva=fecha_vencimiento,
        compatible=not incompatibilidades,
        incompatibilidades=incompatibilidades,
    )


def _cambio_out(
    session: Session,
    historial: HistorialCambioPlan,
    suscripcion: Suscripcion,
    *,
    idempotente: bool,
) -> CambioPlanOut:
    anterior = _plan_or_404(session, historial.plan_anterior_id)
    nuevo = _plan_or_404(session, historial.plan_nuevo_id)
    return CambioPlanOut(
        suscripcion=to_suscripcion_out(session, suscripcion),
        tipo_cambio=historial.tipo_cambio,
        plan_anterior_id=anterior.id,
        plan_anterior_nombre=anterior.nombre,
        plan_nuevo_id=nuevo.id,
        plan_nuevo_nombre=nuevo.nombre,
        fecha_inicio_nueva=historial.fecha_inicio_nueva or suscripcion.fecha_inicio,
        fecha_vencimiento_nueva=(
            historial.fecha_vencimiento_nueva or suscripcion.fecha_vencimiento
        ),
        idempotente=idempotente,
    )


def cambiar_o_renovar_plan(
    session: Session,
    empresa_id: int,
    data: CambioPlanIn,
    *,
    actor_id: int,
    clave_idempotencia: str,
) -> CambioPlanOut:
    repetido = session.exec(
        select(HistorialCambioPlan).where(
            HistorialCambioPlan.clave_idempotencia == clave_idempotencia
        )
    ).first()
    if repetido is not None:
        suscripcion = session.get(Suscripcion, repetido.suscripcion_id)
        if suscripcion is None:
            raise HTTPException(409, "La operación previa no puede recuperarse")
        return _cambio_out(session, repetido, suscripcion, idempotente=True)

    actual = _suscripcion_actual_bloqueada(session, empresa_id)
    if actual is None:
        raise HTTPException(404, "La empresa no tiene una suscripción registrada")
    vista = preview_cambio_plan(session, empresa_id, data)
    if not vista.compatible:
        mensajes = "; ".join(x.mensaje for x in vista.incompatibilidades)
        raise HTTPException(409, f"El plan no es compatible: {mensajes}")
    ahora = datetime.now(timezone.utc)
    historial = HistorialCambioPlan(
        suscripcion_id=actual.id,
        plan_anterior_id=actual.plan_id,
        plan_nuevo_id=data.plan_id,
        usuario_id=actor_id,
        tipo_cambio=vista.tipo_cambio,
        observacion=data.observacion,
        clave_idempotencia=clave_idempotencia,
        fecha_inicio_nueva=vista.fecha_inicio_nueva,
        fecha_vencimiento_nueva=vista.fecha_vencimiento_nueva,
        fecha_cambio=ahora,
    )
    try:
        actual.plan_id = data.plan_id
        actual.fecha_inicio = vista.fecha_inicio_nueva
        actual.fecha_vencimiento = vista.fecha_vencimiento_nueva
        actual.estado = EstadoSuscripcion.ACTIVA
        actual.fecha_actualizacion = ahora
        session.add(actual)
        session.add(historial)
        session.commit()
        session.refresh(actual)
        session.refresh(historial)
        # CU35 — Avisar renovación/cambio de plan a los Administradores.
        from app.modules.traspasos.notificacion_service import crear_notificacion_para_roles_segura

        _tipo = "renovada" if vista.tipo_cambio == "RENOVACION" else "actualizada"
        crear_notificacion_para_roles_segura(
            session=session,
            roles=[RolNombre.ADMINISTRADOR],
            mensaje=f"Tu suscripción fue {_tipo} (vence {actual.fecha_vencimiento}).",
            tipo="success",
            empresa_id=empresa_id,
            modulo="suscripcion",
        )
        return _cambio_out(session, historial, actual, idempotente=False)
    except IntegrityError as exc:
        session.rollback()
        repetido = session.exec(
            select(HistorialCambioPlan).where(
                HistorialCambioPlan.clave_idempotencia == clave_idempotencia
            )
        ).first()
        if repetido is not None:
            suscripcion = session.get(Suscripcion, repetido.suscripcion_id)
            if suscripcion is not None:
                return _cambio_out(session, repetido, suscripcion, idempotente=True)
        raise HTTPException(409, "La solicitud ya fue procesada") from exc
    except Exception:
        session.rollback()
        raise


def historial_cambios_plan(
    session: Session, empresa_id: int
) -> list[HistorialCambioPlanOut]:
    suscripcion = _suscripcion_actual(session, empresa_id)
    if suscripcion is None:
        return []
    items = session.exec(
        select(HistorialCambioPlan)
        .where(HistorialCambioPlan.suscripcion_id == suscripcion.id)
        .order_by(HistorialCambioPlan.fecha_cambio.desc())
    ).all()
    salida: list[HistorialCambioPlanOut] = []
    for item in items:
        anterior = (
            session.get(Plan, item.plan_anterior_id)
            if item.plan_anterior_id is not None
            else None
        )
        nuevo = _plan_or_404(session, item.plan_nuevo_id)
        salida.append(
            HistorialCambioPlanOut(
                id=item.id,
                suscripcion_id=item.suscripcion_id,
                plan_anterior_id=item.plan_anterior_id,
                plan_anterior_nombre=anterior.nombre if anterior else None,
                plan_nuevo_id=nuevo.id,
                plan_nuevo_nombre=nuevo.nombre,
                usuario_id=item.usuario_id,
                tipo_cambio=item.tipo_cambio,
                observacion=item.observacion,
                fecha_inicio_nueva=item.fecha_inicio_nueva,
                fecha_vencimiento_nueva=item.fecha_vencimiento_nueva,
                fecha_cambio=item.fecha_cambio,
            )
        )
    return salida


def get_plan_consumo(session: Session, empresa_id: int) -> PlanConsumoOut:
    empresa = empresa_or_404(session, empresa_id)
    suscripcion = _suscripcion_actual(session, empresa_id)
    if suscripcion is None:
        raise HTTPException(404, "La empresa no tiene una suscripción registrada")
    plan = _plan_or_404(session, suscripcion.plan_id)
    plan_out = to_plan_out(session, plan)
    consumos: list[ConsumoOut] = []
    for limite in plan_out.limites:
        try:
            usado = _consumo_recurso(session, empresa_id, limite.codigo)
            porcentaje = (
                100.0 if limite.valor_maximo == 0 and usado > 0
                else 0.0 if limite.valor_maximo == 0
                else round(usado * 100 / limite.valor_maximo, 2)
            )
            nivel = (
                "BLOQUEADO"
                if usado >= limite.valor_maximo
                else "ADVERTENCIA" if porcentaje >= 80 else "NORMAL"
            )
            consumos.append(
                ConsumoOut(
                    codigo=limite.codigo,
                    etiqueta=_ETIQUETAS.get(limite.codigo, limite.codigo),
                    utilizado=usado,
                    limite=limite.valor_maximo,
                    porcentaje=porcentaje,
                    nivel=nivel,
                )
            )
        except Exception as exc:  # una métrica no impide mostrar las demás
            consumos.append(
                ConsumoOut(
                    codigo=limite.codigo,
                    etiqueta=_ETIQUETAS.get(limite.codigo, limite.codigo),
                    utilizado=None,
                    limite=limite.valor_maximo,
                    porcentaje=None,
                    nivel="ERROR",
                    error=str(exc),
                )
            )
    dias_restantes = max((suscripcion.fecha_vencimiento - date.today()).days, 0)
    # CU35 — Aviso de vencimiento próximo (<= 7 días), una vez por día.
    if dias_restantes <= 7:
        try:
            from app.modules.traspasos.models import Notificacion
            from app.modules.traspasos.notificacion_service import (
                crear_notificacion_para_roles_segura,
            )

            _mensaje = f"Tu plan vence en {dias_restantes} día(s) (vencimiento {suscripcion.fecha_vencimiento})."
            _ya_avisado = session.exec(
                select(Notificacion).where(
                    Notificacion.modulo == "suscripcion",
                    Notificacion.mensaje == _mensaje,
                )
            ).first()
            if _ya_avisado is None:
                crear_notificacion_para_roles_segura(
                    session=session,
                    roles=[RolNombre.ADMINISTRADOR],
                    mensaje=_mensaje,
                    tipo="warning",
                    empresa_id=empresa.id,
                    modulo="suscripcion",
                )
        except Exception:  # noqa: BLE001 - el aviso no rompe la consulta
            session.rollback()
    return PlanConsumoOut(
        empresa_id=empresa.id,
        empresa_nombre=empresa.nombre,
        plan=plan_out,
        suscripcion=to_suscripcion_out(session, suscripcion),
        dias_restantes=dias_restantes,
        funcionalidades=plan_out.funcionalidades,
        consumos=consumos,
    )
