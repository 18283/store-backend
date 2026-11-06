"""Lógica de negocio — CU24 Solicitar traspaso de prenda (código anterior CU61).

Reglas implementadas (trazadas contra el diagrama de robustez):

- Solo actúan Cliente y EncargadoSucursal, siempre dentro de su empresa.
- El Encargado solo puede pedir traspasos HACIA su propia sucursal; el
  Cliente elige cualquier destino activo de su empresa.
- Origen y destino deben existir, estar activos, pertenecer a la empresa y
  ser distintos.
- La variante debe existir, ser de la empresa y pertenecer a un producto
  activo.
- El origen debe tener stock disponible suficiente.
- Si el destino ya tiene stock suficiente, se exige una confirmación
  explícita antes de registrar la solicitud.
- No se permite otra solicitud PENDIENTE para la misma variante + origen +
  destino; se devuelve el código de la existente para consultarla.
- Crear una solicitud NO modifica el inventario: el movimiento se hace recién
  cuando el traspaso se ejecute en un caso de uso posterior.
- Si falla el guardado (por ejemplo, colisión del código único), se hace
  rollback y se reintenta sin duplicar la solicitud.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, or_, select

from app.modules.catalogo.models import Color, Talla
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.inventario.models import Inventario, MovimientoInventario
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.saas.models import Empresa
from app.core.deps import SessionDep
from app.modules.sucursales.models import Sucursal

from .exceptions import (
    CONFLICTO_DESPECHO_SIMULTANEO,
    DATOS_ENVIO_INCOMPLETOS,
    DESPECHO_INVALIDO,
    NO_SE_PUDO_REGISTRAR,
    SIN_PERMISO_DESPECHO,
    SIN_STOCK_SUFICIENTE,
    SOLICITUD_YA_PROCESADA,
    STOCK_INCONSISTENTE,
    SUCURSAL_INACTIVA_TRASPASO,
    VARIANTE_INACTIVA_DESPECHO,
    SIN_STOCK_ORIGEN,
    SOLICITUD_PENDIENTE_EXISTENTE,
    STOCK_DESTINO_SUFICIENTE,
    STOCK_INSUFICIENTE,
    TraspasoError,
    conflicto,
)
from .models import EstadoTraspaso, SolicitudTraspaso
from .schemas import (
    SolicitudTraspasoCrearIn,
    SolicitudTraspasoDecisionIn,
    SolicitudTraspasoDespachoOut,
)

_ROLES_PERMITIDOS = (RolNombre.CLIENTE, RolNombre.ENCARGADO)
_conflicto = conflicto


def _error(
    status_code: int, codigo: str, mensaje: str, **extra
) -> TraspasoError:
    """Error de negocio con código estable para el frontend (web o móvil)."""
    return TraspasoError(status_code, codigo, mensaje, **extra)


def _contexto(session: Session, actor: Usuario) -> dict:
    """Paso 4/5: rol, empresa activa y sucursal asignada del actor."""
    rol = session.get(Rol, actor.rol_id)
    if rol is None or rol.nombre not in _ROLES_PERMITIDOS:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "SIN_PERMISO",
            "No tiene permisos para esta operación",
        )
    if not actor.activo:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "USUARIO_INACTIVO",
            "El usuario está inactivo",
        )
    if actor.empresa_id is None:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "USUARIO_SIN_EMPRESA",
            "El usuario no está asociado a una empresa",
        )
    empresa = session.get(Empresa, actor.empresa_id)
    if empresa is None or not empresa.activa:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "EMPRESA_SUSPENDIDA",
            "La empresa está suspendida",
        )
    return {
        "rol": rol.nombre,
        "empresa_id": actor.empresa_id,
        "cliente_id": actor.id if rol.nombre == RolNombre.CLIENTE else None,
        "sucursal_asignada_id": (
            actor.sucursal_id if rol.nombre == RolNombre.ENCARGADO else None
        ),
    }


def _sucursales_activas(session: Session, empresa_id: int) -> list[Sucursal]:
    """Paso 6/7: sucursales activas de la empresa, ordenadas por ciudad."""
    return list(
        session.exec(
            select(Sucursal)
            .where(
                Sucursal.empresa_id == empresa_id,
                Sucursal.activa == True,  # noqa: E712
            )
            .order_by(Sucursal.ciudad, Sucursal.nombre)
        ).all()
    )


def _sucursal_out(sucursal: Sucursal) -> dict:
    return {
        "id": sucursal.id,
        "nombre": sucursal.nombre,
        "ciudad": sucursal.ciudad,
    }


def preparar_formulario(session: Session, actor: Usuario) -> dict:
    """Pasos 2-9: opciones para abrir el formulario de traspaso."""
    ctx = _contexto(session, actor)
    sucursales = _sucursales_activas(session, ctx["empresa_id"])

    if ctx["rol"] == RolNombre.ENCARGADO:
        propia = next(
            (
                s
                for s in sucursales
                if s.id == ctx["sucursal_asignada_id"]
            ),
            None,
        )
        if propia is None:
            raise _error(
                status.HTTP_409_CONFLICT,
                "SUCURSAL_ASIGNADA_INVALIDA",
                "Tu sucursal asignada no existe o está inactiva. "
                "Contactá al administrador.",
            )
        destinos = [propia]
        destino_permitido_id = propia.id
    else:
        destinos = sucursales
        destino_permitido_id = None

    return {
        "rol": ctx["rol"],
        "empresa_id": ctx["empresa_id"],
        "cliente_id": ctx["cliente_id"],
        "sucursal_asignada_id": ctx["sucursal_asignada_id"],
        "sucursales": [_sucursal_out(s) for s in sucursales],
        "destino_permitido_id": destino_permitido_id,
        "destinos_permitidos": [_sucursal_out(s) for s in destinos],
    }


def _sucursales_con_stock(
    session: Session, variante_id: int, empresa_id: int
) -> list[dict]:
    """Paso 16/17: sucursales activas con stock disponible de la variante."""
    filas = session.exec(
        select(Inventario, Sucursal)
        .join(Sucursal, Inventario.sucursal_id == Sucursal.id)  # type: ignore[arg-type]
        .where(
            Inventario.variante_id == variante_id,
            Inventario.cantidad_disponible > 0,
            Sucursal.activa == True,  # noqa: E712
            Sucursal.empresa_id == empresa_id,
        )
        .order_by(Sucursal.ciudad, Sucursal.nombre)
    ).all()
    return [
        {
            "sucursal_id": sucursal.id,
            "sucursal": sucursal.nombre,
            "ciudad": sucursal.ciudad,
            "cantidad_disponible": inventario.cantidad_disponible,
        }
        for inventario, sucursal in filas
    ]


def _filas_variante(
    session: Session, variante: ProductoVariante, empresa_id: int
) -> list[dict]:
    """Una fila plana por cada origen con stock de la variante.

    El frontend agrupa por `variante_id`; por eso no se anida la lista de
    sucursales.
    """
    producto = session.get(Producto, variante.producto_id)
    talla = session.get(Talla, variante.talla_id)
    color = session.get(Color, variante.color_id)
    datos = {
        "variante_id": variante.id,
        "producto_id": variante.producto_id,
        "producto": producto.nombre if producto else "",
        "sku": variante.sku,
        "talla": talla.valor if talla else None,
        "color": color.nombre if color else None,
        "color_hex": color.codigo_hex if color else None,
    }
    return [
        {
            **datos,
            "origen_id": origen["sucursal_id"],
            "origen": origen["sucursal"],
            "ciudad": origen["ciudad"],
            "cantidad_disponible": origen["cantidad_disponible"],
        }
        for origen in _sucursales_con_stock(session, variante.id, empresa_id)
    ]


def buscar_variantes(
    session: Session,
    actor: Usuario,
    *,
    q: str | None = None,
    variante_id: int | None = None,
    limite: int = 20,
) -> dict:
    """Pasos 11-20: busca variantes activas y muestra dónde hay stock."""
    ctx = _contexto(session, actor)
    empresa_id = ctx["empresa_id"]

    filtros = [
        ProductoVariante.empresa_id == empresa_id,
        Producto.empresa_id == empresa_id,
        Producto.activo == True,  # noqa: E712
    ]
    if variante_id is not None:
        filtros.append(ProductoVariante.id == variante_id)
    if q and q.strip():
        patron = f"%{q.strip().lower()}%"
        filtros.append(
            or_(
                func.lower(Producto.nombre).like(patron),
                func.lower(ProductoVariante.sku).like(patron),
                func.lower(Talla.valor).like(patron),
                func.lower(Color.nombre).like(patron),
            )
        )

    # Solo variantes que hoy pueden salir de alguna sucursal activa.
    filtros.append(
        ProductoVariante.id.in_(
            select(Inventario.variante_id)
            .join(Sucursal, Inventario.sucursal_id == Sucursal.id)  # type: ignore[arg-type]
            .where(
                Inventario.cantidad_disponible > 0,
                Sucursal.activa == True,  # noqa: E712
                Sucursal.empresa_id == empresa_id,
            )
            .distinct()
        )
    )

    base = (
        select(ProductoVariante)
        .join(Producto, ProductoVariante.producto_id == Producto.id)  # type: ignore[arg-type]
        .join(Talla, ProductoVariante.talla_id == Talla.id)  # type: ignore[arg-type]
        .join(Color, ProductoVariante.color_id == Color.id)  # type: ignore[arg-type]
        .where(*filtros)
    )
    total = session.exec(select(func.count()).select_from(base.subquery())).one()
    variantes = session.exec(
        base.order_by(func.lower(Producto.nombre), ProductoVariante.sku).limit(limite)
    ).all()
    filas = [
        fila
        for variante in variantes
        for fila in _filas_variante(session, variante, empresa_id)
    ]
    return {"items": filas, "total": total}


def _validar_variante(
    session: Session, variante_id: int, empresa_id: int
) -> tuple[ProductoVariante, Producto]:
    """Pasos 26/27: variante existente, de la empresa y con producto activo."""
    variante = session.get(ProductoVariante, variante_id)
    if variante is None or variante.empresa_id != empresa_id:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "VARIANTE_INEXISTENTE",
            "La variante no existe",
        )
    producto = session.get(Producto, variante.producto_id)
    if producto is None or producto.empresa_id != empresa_id:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "VARIANTE_INEXISTENTE",
            "La variante no existe",
        )
    if not producto.activo:
        raise _error(
            status.HTTP_409_CONFLICT,
            "VARIANTE_INACTIVA",
            "La variante está inactiva y no puede traspasarse",
        )
    return variante, producto


def _validar_origen_destino(
    session: Session, origen_id: int, destino_id: int, empresa_id: int
) -> tuple[Sucursal, Sucursal]:
    """Pasos 28/29: ambas sucursales existen, son de la empresa, están activas
    y son distintas entre sí."""
    if origen_id == destino_id:
        raise _error(
            422,
            "ORIGEN_DESTINO_IGUALES",
            "La sucursal de origen y la de destino deben ser distintas",
        )
    origen = session.get(Sucursal, origen_id)
    destino = session.get(Sucursal, destino_id)
    if (
        origen is None
        or destino is None
        or origen.empresa_id != empresa_id
        or destino.empresa_id != empresa_id
    ):
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "SUCURSAL_INEXISTENTE",
            "La sucursal de origen o destino no existe",
        )
    if not origen.activa:
        raise _error(
            status.HTTP_409_CONFLICT,
            "SUCURSAL_INACTIVA",
            f"La sucursal de origen '{origen.nombre}' está inactiva",
        )
    if not destino.activa:
        raise _error(
            status.HTTP_409_CONFLICT,
            "SUCURSAL_INACTIVA",
            f"La sucursal de destino '{destino.nombre}' está inactiva",
        )
    return origen, destino


def _validar_permiso_destino(ctx: dict, destino_id: int) -> None:
    """Paso 24/25: el EncargadoSucursal solo puede pedir hacia su sucursal."""
    if (
        ctx["rol"] == RolNombre.ENCARGADO
        and ctx["sucursal_asignada_id"] != destino_id
    ):
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "DESTINO_NO_PERMITIDO",
            "El Encargado de sucursal solo puede solicitar traspasos hacia "
            "su propia sucursal",
        )


def _stock(session: Session, variante_id: int, sucursal_id: int) -> int:
    inv = session.exec(
        select(Inventario).where(
            Inventario.variante_id == variante_id,
            Inventario.sucursal_id == sucursal_id,
        )
    ).first()
    return inv.cantidad_disponible if inv is not None else 0


def _siguiente_codigo(session: Session, empresa_id: int) -> str:
    """Código legible: TRP-YYYYMMDD-0001, correlativo por empresa y día."""
    prefijo = f"TRP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
    total = session.exec(
        select(func.count())
        .select_from(SolicitudTraspaso)
        .where(
            SolicitudTraspaso.empresa_id == empresa_id,
            SolicitudTraspaso.codigo.like(f"{prefijo}%"),
        )
    ).one()
    return f"{prefijo}{total + 1:04d}"


def _resumen(
    *,
    cantidad: int,
    producto: Producto | None,
    talla: Talla | None,
    color: Color | None,
    variante: ProductoVariante,
    origen: Sucursal,
    destino: Sucursal,
    estado: str,
) -> str:
    prenda = producto.nombre if producto else variante.sku
    detalle = " · ".join(
        parte
        for parte in (
            talla.valor if talla else None,
            color.nombre if color else None,
            variante.sku,
        )
        if parte
    )
    return (
        f"{cantidad} × {prenda} ({detalle}) desde {origen.nombre} "
        f"({origen.ciudad}) hacia {destino.nombre} ({destino.ciudad}). "
        f"Estado: {estado}."
    )


def _etiqueta_variante(
    producto: Producto | None,
    talla: Talla | None,
    color: Color | None,
    variante: ProductoVariante,
) -> str:
    """Texto "Producto · Talla · Color" que muestra la tarjeta de resumen."""
    partes = (
        producto.nombre if producto else None,
        talla.valor if talla else None,
        color.nombre if color else None,
    )
    return " · ".join(parte for parte in partes if parte) or variante.sku


def _solicitud_out(session: Session, s: SolicitudTraspaso) -> dict:
    variante = session.get(ProductoVariante, s.variante_id)
    producto = session.get(Producto, variante.producto_id) if variante else None
    talla = session.get(Talla, variante.talla_id) if variante else None
    color = session.get(Color, variante.color_id) if variante else None
    origen = session.get(Sucursal, s.origen_id)
    destino = session.get(Sucursal, s.destino_id)
    solicitante = session.get(Usuario, s.solicitante_id)
    rol_solicitante = (
        session.get(Rol, solicitante.rol_id) if solicitante else None
    )
    admin = session.get(Usuario, s.admin_id) if s.admin_id else None
    despachador = (
        session.get(Usuario, s.despachador_id)
        if getattr(s, "despachador_id", None)
        else None
    )

    return {
        "id": s.id,
        "codigo": s.codigo,
        "estado": s.estado,
        "resumen": (
            _resumen(
                cantidad=s.cantidad,
                producto=producto,
                talla=talla,
                color=color,
                variante=variante,
                origen=origen,
                destino=destino,
                estado=s.estado,
            )
            if variante is not None and origen is not None and destino is not None
            else ""
        ),
        "variante_id": s.variante_id,
        "variante": (
            _etiqueta_variante(producto, talla, color, variante)
            if variante is not None
            else None
        ),
        "producto_id": producto.id if producto else 0,
        "producto": producto.nombre if producto else "",
        "sku": variante.sku if variante else "",
        "talla": talla.valor if talla else "",
        "color": color.nombre if color else "",
        "color_hex": color.codigo_hex if color else None,
        "imagen_url": (
            variante.imagen_url
            if variante and variante.imagen_url is not None
            else (producto.imagen_url if producto else None)
        ),
        "origen_id": s.origen_id,
        "origen": origen.nombre if origen else "",
        "ciudad_origen": origen.ciudad if origen else "",
        "destino_id": s.destino_id,
        "destino": destino.nombre if destino else "",
        "ciudad_destino": destino.ciudad if destino else "",
        "cantidad": s.cantidad,
        "motivo": s.motivo,
        "solicitante_id": s.solicitante_id,
        "solicitante": (
            f"{solicitante.nombre} {solicitante.apellido}"
            if solicitante
            else ""
        ),
        "solicitante_rol": rol_solicitante.nombre if rol_solicitante else None,
        "fecha_solicitud": s.fecha_solicitud,
        "admin_id": s.admin_id,
        "admin_nombre": f"{admin.nombre} {admin.apellido}" if admin else None,
        "fecha_decision": s.fecha_decision,
        "observacion_motivo": s.observacion_motivo,
        "despachador_id": getattr(s, "despachador_id", None),
        "despachador_nombre": (
            f"{despachador.nombre} {despachador.apellido}"
            if despachador
            else None
        ),
        "fecha_despacho": getattr(s, "fecha_despacho", None),
        "transportista": getattr(s, "transportista", None),
        "numero_guia": getattr(s, "numero_guia", None),
        "observaciones_despacho": getattr(s, "observaciones_despacho", None),
        "stock_origen": _stock(session, s.variante_id, s.origen_id),
        "stock_destino": _stock(session, s.variante_id, s.destino_id),
    }


def solicitar_traspaso(
    session: Session, actor: Usuario, data: SolicitudTraspasoCrearIn
) -> dict:
    """Pasos 21-40: valida todo y registra la solicitud en PENDIENTE."""
    ctx = _contexto(session, actor)
    empresa_id = ctx["empresa_id"]

    if data.cantidad <= 0:
        raise HTTPException(422, "La cantidad debe ser mayor que cero")

    _validar_permiso_destino(ctx, data.destino_id)
    variante, producto = _validar_variante(session, data.variante_id, empresa_id)
    origen, destino = _validar_origen_destino(
        session, data.origen_id, data.destino_id, empresa_id
    )

    # Paso 30/31 — stock del origen. Se bloquea la fila para que dos
    # solicitudes concurrentes no validen el mismo stock ni dupliquen el pedido.
    inv_origen = session.exec(
        select(Inventario)
        .where(
            Inventario.variante_id == variante.id,
            Inventario.sucursal_id == origen.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if inv_origen is None or inv_origen.cantidad_disponible <= 0:
        raise _conflicto(
            "SIN_STOCK_ORIGEN",
            f"No hay stock disponible de esta variante en '{origen.nombre}'",
            stock_disponible=0,
        )
    if data.cantidad > inv_origen.cantidad_disponible:
        raise _conflicto(
            "STOCK_INSUFICIENTE",
            (
                f"En '{origen.nombre}' solo hay "
                f"{inv_origen.cantidad_disponible} unidad(es) disponible(s)"
            ),
            stock_disponible=inv_origen.cantidad_disponible,
        )

    # Paso 32/33 — si el destino ya tiene suficiente, pedir confirmación.
    stock_destino = _stock(session, variante.id, destino.id)
    confirmado = getattr(data, "confirmar_stock_destino", False) or getattr(
        data, "confirmar_stock_suficiente", False
    )
    if stock_destino >= data.cantidad and not confirmado:
        raise _conflicto(
            "CONFIRMACION_STOCK_DESTINO",
            (
                f"'{destino.nombre}' ya tiene {stock_destino} unidad(es) de "
                "esta variante. Confirmá si igual querés solicitar el traspaso."
            ),
            stock_destino=stock_destino,
            requiere_confirmacion=True,
        )

    # Paso 34/35 — no duplicar una solicitud PENDIENTE idéntica.
    duplicada = session.exec(
        select(SolicitudTraspaso).where(
            SolicitudTraspaso.empresa_id == empresa_id,
            SolicitudTraspaso.variante_id == variante.id,
            SolicitudTraspaso.origen_id == origen.id,
            SolicitudTraspaso.destino_id == destino.id,
            SolicitudTraspaso.estado == EstadoTraspaso.PENDIENTE,
        )
    ).first()
    if duplicada is not None:
        raise _conflicto(
            "SOLICITUD_PENDIENTE_EXISTENTE",
            (
                "Ya existe una solicitud pendiente para esta variante desde "
                f"'{origen.nombre}' hacia '{destino.nombre}' "
                f"(código {duplicada.codigo})"
            ),
            solicitud_id=duplicada.id,
            codigo_solicitud=duplicada.codigo,
            estado=duplicada.estado,
        )

    # Paso 36/37 — crear sin tocar inventario. Si el guardado falla (código
    # repetido por concurrencia), rollback y reintento sin duplicar.
    for _ in range(5):
        solicitud = SolicitudTraspaso(
            empresa_id=empresa_id,
            codigo=_siguiente_codigo(session, empresa_id),
            variante_id=variante.id,
            origen_id=origen.id,
            destino_id=destino.id,
            cantidad=data.cantidad,
            motivo=data.motivo,
            estado=EstadoTraspaso.PENDIENTE,
            solicitante_id=actor.id,
        )
        session.add(solicitud)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            continue
        session.refresh(solicitud)
        return _solicitud_out(session, solicitud)

    raise _conflicto(
        "NO_SE_PUDO_REGISTRAR",
        "No se pudo registrar la solicitud por un conflicto de concurrencia. "
        "Intentá nuevamente.",
    )


def _contexto_usuario(session: Session, actor: Usuario) -> dict:
    """Valida rol, cuenta activa y empresa para cliente, encargado o administrador."""
    rol = session.get(Rol, actor.rol_id)
    es_superadmin = rol is not None and rol.nombre == RolNombre.SUPERADMINISTRADOR
    es_admin = rol is not None and rol.nombre == RolNombre.ADMINISTRADOR
    if (
        rol is None
        or rol.nombre not in (RolNombre.CLIENTE, RolNombre.ENCARGADO, RolNombre.ADMINISTRADOR)
        and not es_superadmin
    ):
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "SIN_PERMISO",
            "No tiene permisos para esta operación",
        )
    if not actor.activo:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "USUARIO_INACTIVO",
            "El usuario está inactivo",
        )
    if not es_superadmin:
        if actor.empresa_id is None:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "USUARIO_SIN_EMPRESA",
                "El usuario no está asociado a una empresa",
            )
        empresa = session.get(Empresa, actor.empresa_id)
        if empresa is None or not empresa.activa:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "EMPRESA_SUSPENDIDA",
                "La empresa está suspendida",
            )
    return {
        "rol": rol.nombre if rol else "",
        "empresa_id": actor.empresa_id,
        "cliente_id": actor.id if rol and rol.nombre == RolNombre.CLIENTE else None,
        "sucursal_asignada_id": (
            actor.sucursal_id if rol and rol.nombre == RolNombre.ENCARGADO else None
        ),
        "es_admin": es_admin or es_superadmin,
        "es_superadmin": es_superadmin,
    }


def _contexto_admin(session: Session, actor: Usuario) -> dict:
    """Valida que el actor sea Administrador (o Superadmin) de una empresa activa."""
    ctx = _contexto_usuario(session, actor)
    if not ctx["es_admin"]:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "SIN_PERMISO",
            "No tiene permisos de Administrador",
        )
    return ctx


def _normalizar_estado(estado: str | None) -> str | None:
    """Normaliza y mapea parámetros de estado (case-insensitive, singular/plural)."""
    if not estado or not estado.strip():
        return None
    val = estado.strip().upper()
    if val in ("PENDIENTE", "PENDIENTES"):
        return EstadoTraspaso.PENDIENTE
    if val in ("APROBADA", "APROBADO", "APROBADOS"):
        return EstadoTraspaso.APROBADA
    if val in ("RECHAZADA", "RECHAZADO", "RECHAZADOS"):
        return EstadoTraspaso.RECHAZADA
    if val in ("EN_TRANSITO", "TRANSITO", "ENVIADO", "ENVIADOS"):
        return EstadoTraspaso.EN_TRANSITO
    if val in ("RECIBIDO", "RECIBIDOS"):
        return EstadoTraspaso.RECIBIDO
    return val


def listar_solicitudes(
    session: Session,
    actor: Usuario,
    *,
    estado: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    """Lista las solicitudes de traspaso de la empresa.

    - Si es Administrador o Superadmin, ve todas las de la empresa.
    - Si es EncargadoSucursal, ve las que corresponden a su sucursal (origen o destino).
    - Si es Cliente, ve las suyas.
    Permite filtrar por estado de forma flexible y paginar.
    """
    ctx = _contexto_usuario(session, actor)
    empresa_id = ctx["empresa_id"]

    query = select(SolicitudTraspaso).where(SolicitudTraspaso.empresa_id == empresa_id)

    if ctx["rol"] == RolNombre.ENCARGADO:
        sucursal_id = ctx["sucursal_asignada_id"]
        if sucursal_id is not None:
            query = query.where(
                or_(
                    SolicitudTraspaso.origen_id == sucursal_id,
                    SolicitudTraspaso.destino_id == sucursal_id,
                )
            )
        else:
            query = query.where(SolicitudTraspaso.id == -1)
    elif ctx["rol"] == RolNombre.CLIENTE:
        query = query.where(SolicitudTraspaso.solicitante_id == actor.id)
    elif not ctx["es_admin"] and not ctx["es_superadmin"]:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "SIN_PERMISO",
            "No tiene permisos para listar las solicitudes",
        )

    est_normalizado = _normalizar_estado(estado)
    if est_normalizado:
        query = query.where(SolicitudTraspaso.estado == est_normalizado)

    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    offset = (page - 1) * size
    solicitudes = session.exec(
        query.order_by(SolicitudTraspaso.fecha_solicitud.desc())
        .offset(offset)
        .limit(size)
    ).all()
    return [_solicitud_out(session, s) for s in solicitudes], total


def procesar_decision(
    session: Session,
    actor: Usuario,
    solicitud_id: int | str,
    data: SolicitudTraspasoDecisionIn,
) -> dict:
    """CU25 — Aprobar o rechazar traspaso (Administrador).

    Permite identificar la solicitud tanto por ID numérico interno como por código textual.
    El Administrador de tenant tiene acceso total sin restricción de sucursal.
    """
    ctx = _contexto_admin(session, actor)
    empresa_id = ctx["empresa_id"]

    query = select(SolicitudTraspaso).where(SolicitudTraspaso.empresa_id == empresa_id)
    if isinstance(solicitud_id, int) or (
        isinstance(solicitud_id, str) and str(solicitud_id).strip().isdigit()
    ):
        sid = int(str(solicitud_id).strip())
        query = query.where(SolicitudTraspaso.id == sid)
    else:
        codigo = str(solicitud_id).strip()
        query = query.where(func.upper(SolicitudTraspaso.codigo) == codigo.upper())

    # Bloqueo de la solicitud para concurrencia (A7)
    solicitud = session.exec(
        query.with_for_update().execution_options(populate_existing=True)
    ).first()

    if solicitud is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "SOLICITUD_NO_ENCONTRADA",
            "La solicitud de traspaso no existe",
        )

    # Validar que siga pendiente (A3)
    if solicitud.estado != EstadoTraspaso.PENDIENTE:
        raise _conflicto(
            "SOLICITUD_YA_PROCESADA",
            f"La solicitud ya fue procesada previamente (estado actual: {solicitud.estado})",
            estado_actual=solicitud.estado,
        )

    decision = data.decision.strip().upper()
    if decision not in ("APROBAR", "APROBADA", "RECHAZAR", "RECHAZADA"):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "DECISION_INVALIDA",
            "La decisión debe ser APROBAR o RECHAZAR",
        )

    es_aprobacion = decision in ("APROBAR", "APROBADA")
    observacion_motivo = (data.observacion_motivo or data.motivo or "").strip() or None

    if not es_aprobacion:
        # A6: Rechazo sin motivo
        if not observacion_motivo:
            raise _error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "MOTIVO_RECHAZO_REQUERIDO",
                "Debe ingresar un motivo para rechazar la solicitud",
            )

    origen = session.get(Sucursal, solicitud.origen_id)
    destino = session.get(Sucursal, solicitud.destino_id)

    # A5: Sucursal de origen o destino inactiva
    if origen is None or destino is None or not origen.activa or not destino.activa:
        raise _conflicto(
            "SUCURSAL_INACTIVA_TRASPASO",
            "La sucursal de origen o destino está inactiva. No se puede aprobar.",
        )

    ahora = datetime.now(timezone.utc)

    if es_aprobacion:
        # A2: Validar stock disponible suficiente en origen (con bloqueo with_for_update)
        inv_origen = session.exec(
            select(Inventario)
            .where(
                Inventario.variante_id == solicitud.variante_id,
                Inventario.sucursal_id == origen.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

        stock_disponible = inv_origen.cantidad_disponible if inv_origen else 0
        if inv_origen is None or stock_disponible < solicitud.cantidad:
            raise _conflicto(
                "SIN_STOCK_SUFICIENTE",
                (
                    f"No hay stock suficiente en '{origen.nombre}' para aprobar. "
                    f"Disponible: {stock_disponible}, solicitado: {solicitud.cantidad}"
                ),
                stock_disponible=stock_disponible,
                cantidad_solicitada=solicitud.cantidad,
            )

        # Paso 7: Descontar de disponible y sumar a reservada en origen
        inv_origen.cantidad_disponible -= solicitud.cantidad
        inv_origen.cantidad_reservada += solicitud.cantidad
        session.add(inv_origen)

        # Paso 8: Cambiar estado a APROBADA y registrar datos de decisión
        solicitud.estado = EstadoTraspaso.APROBADA
        solicitud.admin_id = actor.id
        solicitud.fecha_decision = ahora
        solicitud.observacion_motivo = observacion_motivo
    else:
        # A1: Rechazo. Inventario no se modifica.
        solicitud.estado = EstadoTraspaso.RECHAZADA
        solicitud.admin_id = actor.id
        solicitud.fecha_decision = ahora
        solicitud.observacion_motivo = observacion_motivo

    session.add(solicitud)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _conflicto(
            "ERROR_GUARDAR_DECISION",
            "No se pudo guardar la decisión por un error de base de datos. Intentá nuevamente.",
        ) from exc

    session.refresh(solicitud)
    return _solicitud_out(session, solicitud)


def consultar_traspaso(
    session: Session, actor: Usuario, codigo: str
) -> dict:
    """CU28 — Consultar seguimiento de traspaso.

    Consultar estado solicitado, aprobado, enviado, recibido o cancelado.
    Actores: Cliente, Encargado de sucursal.

    El Administrador ve cualquiera de su empresa; el Cliente solo ve las suyas;
    el Encargado, las que salen o llegan a su sucursal. Siempre dentro de la
    empresa del actor.

    Parámetro `codigo`:
    - Si es numérico puro: busca por ID interno de la base de datos.
    - Si es texto: busca por código legible (TRP-...).

    Retorna diccionario con datos formateados: resumen, prenda, origen,
    destino, cantidades, estados, datos de envío, etc.
    """
    ctx = _contexto_usuario(session, actor)
    empresa_id = ctx["empresa_id"]

    # Búsqueda: ID numérico o código textual
    codigo_strip = codigo.strip()
    is_digit = codigo_strip.isdigit()

    solicitud = session.exec(
        select(SolicitudTraspaso).where(
            SolicitudTraspaso.empresa_id == empresa_id,
            or_(
                SolicitudTraspaso.id == int(codigo_strip) if is_digit else True,
                func.upper(SolicitudTraspaso.codigo) == codigo_strip.upper() if not is_digit else True,
            ),
        )
    ).first()

    # Si es búsqueda por ID numérico, filtrar directamente
    if is_digit:
        solicitud = session.get(SolicitudTraspaso, int(codigo_strip))

    if solicitud is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Solicitud de traspaso no encontrada"
        )

    # --- Filtrado por rol (según especificación CU28) ---
    if ctx["es_admin"] or ctx["es_superadmin"]:
        pass  # Admin ve todo de la empresa
    elif ctx["rol"] == RolNombre.CLIENTE:
        # Cliente solo puede consultar sus propias solicitudes
        if solicitud.solicitante_id != actor.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "SIN_PERMISO",
                "Esta solicitud es de otro cliente",
            )
    else:
        # Encargado: solo puede consultar solicitudes donde su sucursal sea origen o destino
        if ctx["sucursal_asignada_id"] not in (
            solicitud.origen_id,
            solicitud.destino_id,
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "SIN_PERMISO",
                "Esta solicitud no corresponde a tu sucursal asignada",
            )

    return _solicitud_out(session, solicitud)


def listar_traspasos_origen(
    session: Session,
    actor: Usuario,
    *,
    origen_id: int | None = None,
    estado: str | None = EstadoTraspaso.APROBADA,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    """CU26 — Lista solicitudes de traspaso para la sucursal de origen (Encargado o Administrador).

    Compara correctamente el `origen_id` con la `sucursal_asignada_id` del usuario logueado
    si es Encargado, o permite filtrar si es Administrador.
    Incluye logs/prints de depuración para inspeccionar `origen_id` y sucursal del usuario.
    """
    ctx = _contexto_usuario(session, actor)
    empresa_id = ctx["empresa_id"]
    rol = ctx["rol"]

    if rol == RolNombre.ENCARGADO:
        sucursal_origen_id = ctx["sucursal_asignada_id"]
        if sucursal_origen_id is None:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "SIN_SUCURSAL_ASIGNADA",
                "El encargado no tiene una sucursal asignada",
            )
        filtro_origen = sucursal_origen_id
    elif ctx["es_admin"] or ctx["es_superadmin"]:
        filtro_origen = origen_id
    else:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "SIN_PERMISO",
            "No tiene permisos para consultar los traspasos por origen",
        )
    
    # Print / log temporal de depuración solicitado
    print(f"\n[DEBUG CU26] =========================================")
    print(f"[DEBUG CU26] Usuario: {actor.nombre} {actor.apellido} (ID: {actor.id})")
    print(f"[DEBUG CU26] Rol: {rol}, sucursal_asignada_id: {ctx.get('sucursal_asignada_id')}")
    print(f"[DEBUG CU26] Filtro origen_id aplicado: {filtro_origen}, estado: {estado}")
    print(f"[DEBUG CU26] =========================================\n")

    query = select(SolicitudTraspaso).where(SolicitudTraspaso.empresa_id == empresa_id)
    if filtro_origen is not None:
        query = query.where(SolicitudTraspaso.origen_id == filtro_origen)

    est_normalizado = _normalizar_estado(estado)
    if est_normalizado:
        query = query.where(SolicitudTraspaso.estado == est_normalizado)

    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    offset = (page - 1) * size
    solicitudes = session.exec(
        query.order_by(SolicitudTraspaso.fecha_solicitud.desc())
        .offset(offset)
        .limit(size)
    ).all()

    return [_solicitud_out(session, s) for s in solicitudes], total


# --------------------------------------------------------------------------- #
#  CU26 — Despachar Traspaso
# --------------------------------------------------------------------------- #

print(">>> DEBUG: ¡LA FUNCIÓN SE EJECUTÓ!", flush=True)
def despachar_traspaso(
    session: Session,
    actor: Usuario,
    solicitud_id: int | str,
    datos_envio: dict | None = None,
) -> SolicitudTraspasoDespachoOut:
    """CU26 — Despachar traspaso.

    Registra la salida en tránsito sin incrementar todavía el inventario de destino.
    Soporta datos de envío con fecha_envio, hora_envio o fecha_hora combinada.
    """
    from fastapi import status
    from app.modules.inventario.models import MovimientoInventario
    from app.modules.identidad.models import RolNombre

    # ---- Paso 1: Contexto y permiso de rol ----
    print(f"\n[DEBUG DESPACHO] =========================================")
    print(f"[DEBUG DESPACHO] Iniciando despacho para solicitud_id: {solicitud_id} (tipo: {type(solicitud_id)})")
    print(f"[DEBUG DESPACHO] Actor: {actor.email} (ID: {actor.id})")
    print(f"[DEBUG DESPACHO] datos_envio recibido: {datos_envio}")
    
    ctx = _contexto_usuario(session, actor)
    if not ctx["es_admin"] and ctx["rol"] != RolNombre.ENCARGADO:
        raise _error(
            status.HTTP_403_FORBIDDEN,
            SIN_PERMISO_DESPECHO,
            "Solo el Encargado de sucursal o Administrador puede despachar traspasos",
        )
    print(f"[DEBUG DESPACHO] Contexto validado: rol={ctx['rol']}, es_admin={ctx['es_admin']}, empresa_id={ctx['empresa_id']}, sucursal_asignada={ctx.get('sucursal_asignada_id')}")

    # ---- Paso 2: Obtener la solicitud (por ID numérico o código) ----
    if isinstance(solicitud_id, int) or (
        isinstance(solicitud_id, str) and str(solicitud_id).strip().isdigit()
    ):
        sid = int(str(solicitud_id).strip())
        solicitud = session.exec(
            select(SolicitudTraspaso).where(
                SolicitudTraspaso.empresa_id == ctx["empresa_id"],
                SolicitudTraspaso.id == sid,
            ).with_for_update().execution_options(populate_existing=True)
        ).first()
    else:
        codigo = str(solicitud_id).strip()
        # Intentar primero encontrar la aprobada para esta sucursal / empresa
        query = select(SolicitudTraspaso).where(
            SolicitudTraspaso.empresa_id == ctx["empresa_id"],
            func.upper(SolicitudTraspaso.codigo) == codigo.upper(),
        )
        if ctx["rol"] == RolNombre.ENCARGADO and ctx.get("sucursal_asignada_id"):
            query = query.where(SolicitudTraspaso.origen_id == ctx["sucursal_asignada_id"])
        
        # Priorizar estado APROBADA
        solicitud = session.exec(
            query.where(func.upper(SolicitudTraspaso.estado).in_(("APROBADO", "APROBADA", "APROBADOS")))
            .with_for_update().execution_options(populate_existing=True)
        ).first()

        # Si no se encuentra una aprobada específica, buscar por código general en la empresa
        if solicitud is None:
            solicitud = session.exec(
                select(SolicitudTraspaso).where(
                    SolicitudTraspaso.empresa_id == ctx["empresa_id"],
                    func.upper(SolicitudTraspaso.codigo) == codigo.upper(),
                ).with_for_update().execution_options(populate_existing=True)
            ).first()

    if solicitud is None:
        print(f"[DEBUG DESPACHO] Solicitud identificada por '{solicitud_id}' NO encontrada.")
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "SOLICITUD_NO_ENCONTRADA",
            "Solicitud de traspaso no encontrada",
        )
    print(f"[DEBUG DESPACHO] Solicitud encontrada: ID={solicitud.id}, Código={solicitud.codigo}, Estado DB={solicitud.estado}")

    # ---- Paso 3: Validar que la solicitud esté en estado APROBADA (flexible) ----
    estado_str = str(solicitud.estado or "").strip().upper()

    print(f"[DEBUG VALIDACION ESTADO] estado_bruto={solicitud.estado!r}, estado_procesado='{estado_str}'", flush=True)

    if estado_str not in ("APROBADO", "APROBADA", "APROBADOS"):
        print(f"[DEBUG DESPACHO] Error: estado '{solicitud.estado}' no es APROBADO.")
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            DESPECHO_INVALIDO,
            "La solicitud no está en estado APROBADO, no puede ser despachada.",
            estado_actual=solicitud.estado,
        )

    # ---- Paso 4: Validar sucursal de origen si es Encargado ----
    if ctx["rol"] == RolNombre.ENCARGADO:
        if solicitud.origen_id != ctx["sucursal_asignada_id"]:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                SIN_PERMISO_DESPECHO,
                "No puede despachar un traspaso que no corresponda a su sucursal asignada.",
            )

    # ---- Paso 5: Validar variante y producto activo ----
    variante = session.get(ProductoVariante, solicitud.variante_id)
    if variante is None or variante.empresa_id != ctx["empresa_id"]:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            VARIANTE_INACTIVA_DESPECHO,
            "La variante de la solicitud no existe o no pertenece a su empresa.",
        )

    producto = session.get(Producto, variante.producto_id)
    if producto is None or producto.empresa_id != ctx["empresa_id"]:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            VARIANTE_INACTIVA_DESPECHO,
            "El producto de la variante no existe o no pertenece a su empresa.",
        )

    if not producto.activo:
        raise _error(
            status.HTTP_409_CONFLICT,
            VARIANTE_INACTIVA_DESPECHO,
            "La variante está inactiva y no puede despacharse.",
        )

    # ---- Paso 6: Validar sucursal de destino activa ----
    destino = session.get(Sucursal, solicitud.destino_id)
    if destino is None or not destino.activa:
        raise _error(
            status.HTTP_409_CONFLICT,
            VARIANTE_INACTIVA_DESPECHO,
            "La sucursal de destino no existe o está inactiva.",
        )

    # ---- Paso 7: Validar y descontar stock reservado o disponible en origen ----
    inv_origen = session.exec(
        select(Inventario).where(
            Inventario.variante_id == solicitud.variante_id,
            Inventario.sucursal_id == solicitud.origen_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()

    if inv_origen is None:
        raise _error(
            status.HTTP_409_CONFLICT,
            STOCK_INCONSISTENTE,
            "No hay registro de inventario para esta variante en la sucursal de origen.",
            variante_id=solicitud.variante_id,
            sucursal_id=solicitud.origen_id,
        )

    cantidad_a_descontar = solicitud.cantidad

    if inv_origen.cantidad_reservada >= cantidad_a_descontar:
        inv_origen.cantidad_reservada -= cantidad_a_descontar
    elif (inv_origen.cantidad_reservada + inv_origen.cantidad_disponible) >= cantidad_a_descontar:
        faltante = cantidad_a_descontar - inv_origen.cantidad_reservada
        inv_origen.cantidad_reservada = 0
        inv_origen.cantidad_disponible -= faltante
    else:
        raise _error(
            status.HTTP_409_CONFLICT,
            STOCK_INCONSISTENTE,
            (f"El stock en origen (reservado: {inv_origen.cantidad_reservada}, "
             f"disponible: {inv_origen.cantidad_disponible}) no alcanza para "
             f"despachar la cantidad requerida ({cantidad_a_descontar})."),
            cantidad_reservada=inv_origen.cantidad_reservada,
            cantidad_disponible=inv_origen.cantidad_disponible,
            cantidad_solicitada=cantidad_a_descontar,
        )

    session.add(inv_origen)

    # ---- Paso 8: Registrar MovimientoInventario SALIDA_TRASPASO ----
    movimiento = MovimientoInventario(
        variante_id=solicitud.variante_id,
        sucursal_id=solicitud.origen_id,
        usuario_id=actor.id,
        tipo="SALIDA_TRASPASO",
        cantidad=cantidad_a_descontar,
        nota=f"Despacho solicitud {solicitud.codigo} - tránsito",
    )
    session.add(movimiento)

    # ---- Paso 9: Procesar datos de envío (Soporta fecha_envio + hora_envio o fecha_hora, plano o anidado) ----
    datos = datos_envio or {}
    if "datos_envio" in datos and isinstance(datos["datos_envio"], dict):
        datos = datos["datos_envio"]
    elif "datosEnvio" in datos and isinstance(datos["datosEnvio"], dict):
        datos = datos["datosEnvio"]

    transportista = datos.get("transportista")
    numero_guia = datos.get("numero_guia") or datos.get("numeroGuia") or datos.get("guia")
    observaciones = datos.get("observaciones") or datos.get("observacion") or datos.get("nota")

    # Extraer fecha y hora de envío (flexible: fecha_envio/hora_envio separadas o fecha_hora combinada, con soporte camelCase)
    fecha_envio_str = datos.get("fecha_envio") or datos.get("fechaEnvio")
    hora_envio_str = datos.get("hora_envio") or datos.get("horaEnvio")
    fecha_hora_str = datos.get("fecha_hora") or datos.get("fechaHora")

    fecha_hora_despacho = datetime.now(timezone.utc)
    if fecha_envio_str:
        try:
            time_part = hora_envio_str or "00:00:00"
            if len(time_part.split(':')) == 2:
                time_part += ":00"
            dt_str = f"{fecha_envio_str}T{time_part}"
            fecha_hora_despacho = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            pass
    elif fecha_hora_str:
        try:
            fecha_hora_despacho = datetime.fromisoformat(fecha_hora_str.replace("Z", "+00:00"))
        except Exception:
            pass

    # ---- Paso 10: Actualizar estado y datos en SolicitudTraspaso ----
    estado_anterior_str = solicitud.estado
    solicitud.estado = EstadoTraspaso.EN_TRANSITO
    solicitud.despachador_id = actor.id
    solicitud.fecha_despacho = fecha_hora_despacho
    solicitud.fecha_decision = fecha_hora_despacho  # compatibilidad
    solicitud.transportista = transportista
    solicitud.numero_guia = numero_guia
    solicitud.observaciones_despacho = observaciones

    session.add(solicitud)

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise _error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            ERROR_GUARDAR_DESPECHO,
            "No se pudo guardar el despacho por un error de base de datos. Intentá nuevamente.",
        ) from exc

    session.refresh(solicitud)
    print(f"[DEBUG DESPACHO] Éxito: Solicitud {solicitud.codigo} actualizada a {solicitud.estado}")
    print(f"[DEBUG DESPACHO] =========================================\n")

    # ---- Paso 11: Devolver respuesta ----
    return SolicitudTraspasoDespachoOut(
        solicitud_id=solicitud.id,
        id=solicitud.id,
        codigo=solicitud.codigo,
        estado_anterior=estado_anterior_str,
        estado_nuevo=solicitud.estado,
        estado=solicitud.estado,
        mensaje="Despacho registrado exitosamente. La solicitud queda en estado EN_TRANSITO.",
        requiere_accion=False,
        cantidad_descontada=cantidad_a_descontar,
        movimiento_registrado=True,
    )
