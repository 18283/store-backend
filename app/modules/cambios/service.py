"""Lógica de negocio — CU17-CU23 (cambios y devoluciones)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import stripe
from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.core.crud import paginate
from app.modules.catalogo.models import Color, Talla
from app.modules.identidad.models import Usuario
from app.modules.inventario.models import Inventario, MovimientoInventario, TipoMovimiento
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.sucursales.models import Sucursal
from app.modules.ventas import service as ventas_service
from app.modules.ventas import stripe_client
from app.modules.ventas.models import EstadoPago, EstadoVenta, MetodoPago, Pago, Venta, VentaDetalle

from .models import (
    PLAZO_HORAS_SOLICITUD,
    EstadoSolicitud,
    FormaPagoDiferencia,
    SolicitudCambioDevolucion,
    TipoSolicitud,
)
from .schemas import (
    CobroPendienteOut,
    NotaCambioOut,
    ResolverSolicitudIn,
    ResolverSolicitudOut,
    SolicitudCrearIn,
)


def _variante_texto(session: Session, variante_id: int | None) -> str | None:
    if variante_id is None:
        return None
    variante = session.get(ProductoVariante, variante_id)
    if variante is None:
        return None
    talla = session.get(Talla, variante.talla_id)
    color = session.get(Color, variante.color_id)
    return f"{talla.valor if talla else '?'} · {color.nombre if color else '?'} · {variante.sku}"


def _diferencias_estimadas(
    session: Session, s: SolicitudCambioDevolucion
) -> tuple[Decimal | None, Decimal]:
    """Para mostrar en pantalla ANTES de resolver: cuánto saldría cada acción
    posible (entregar cambio / devolver dinero), con los precios de hoy. El
    monto real al resolver puede diferir levemente si el precio cambió."""
    precio_devuelto = _precio_unitario_devuelto(session, s) * s.cantidad
    diferencia_devolucion = -precio_devuelto
    diferencia_cambio: Decimal | None = None
    if s.variante_nueva_id is not None:
        nueva_variante = session.get(ProductoVariante, s.variante_nueva_id)
        if nueva_variante is not None:
            producto_nuevo = session.get(Producto, nueva_variante.producto_id)
            _, precio_unitario_nuevo, _ = ventas_service._precio_venta(
                session, nueva_variante, producto_nuevo
            )
            diferencia_cambio = precio_unitario_nuevo * s.cantidad - precio_devuelto
    return diferencia_cambio, diferencia_devolucion


def _solicitud_out(session: Session, s: SolicitudCambioDevolucion) -> dict:
    cliente = session.get(Usuario, s.cliente_id)
    sucursal = session.get(Sucursal, s.sucursal_id)
    variante_devuelta = session.get(ProductoVariante, s.variante_devuelta_id)
    producto = (
        session.get(Producto, variante_devuelta.producto_id)
        if variante_devuelta
        else None
    )
    diferencia_cambio_est = diferencia_devolucion_est = None
    if s.estado in (EstadoSolicitud.SOLICITADO, EstadoSolicitud.RECIBIDO):
        diferencia_cambio_est, diferencia_devolucion_est = _diferencias_estimadas(session, s)
    return {
        "id": s.id,
        "venta_id": s.venta_id,
        "venta_detalle_id": s.venta_detalle_id,
        "cliente_id": s.cliente_id,
        "cliente": f"{cliente.nombre} {cliente.apellido}" if cliente else None,
        "sucursal_id": s.sucursal_id,
        "sucursal": sucursal.nombre if sucursal else None,
        "tipo": s.tipo,
        "motivo": s.motivo,
        "producto": producto.nombre if producto else None,
        "variante_devuelta_id": s.variante_devuelta_id,
        "variante_devuelta": _variante_texto(session, s.variante_devuelta_id),
        "cantidad": s.cantidad,
        "variante_nueva_id": s.variante_nueva_id,
        "variante_nueva": _variante_texto(session, s.variante_nueva_id),
        "estado": s.estado,
        "fecha_solicitud": s.fecha_solicitud,
        "fecha_limite": s.fecha_limite,
        "fecha_recepcion": s.fecha_recepcion,
        "fecha_resolucion": s.fecha_resolucion,
        "diferencia": s.diferencia,
        "forma_pago_diferencia": s.forma_pago_diferencia,
        "diferencia_cambio_estimada": diferencia_cambio_est,
        "diferencia_devolucion_estimada": diferencia_devolucion_est,
    }


# --------------------------------------------------------------------------- #
#  CU18 — Solicitar cambio o devolución (Cliente)
# --------------------------------------------------------------------------- #
def crear_solicitud(
    session: Session, cliente: Usuario, data: SolicitudCrearIn
) -> dict:
    if data.tipo not in (TipoSolicitud.CAMBIO, TipoSolicitud.DEVOLUCION):
        raise HTTPException(422, "El tipo debe ser CAMBIO o DEVOLUCION")
    if data.tipo == TipoSolicitud.CAMBIO and data.variante_nueva_id is None:
        raise HTTPException(422, "Un cambio necesita indicar la variante nueva")

    venta = session.get(Venta, data.venta_id)
    if venta is None or venta.cliente_id != cliente.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Venta no encontrada")
    if venta.estado not in (EstadoVenta.PAGADA, EstadoVenta.COMPLETADA):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo se puede pedir un cambio o devolución de una venta ya pagada",
        )

    limite = venta.fecha_creacion + timedelta(hours=PLAZO_HORAS_SOLICITUD)
    # fecha_creacion viene de la base como datetime naive (sin tz); comparar
    # contra un "ahora" naive en UTC evita el TypeError de comparar un
    # datetime aware con uno naive.
    if datetime.now(timezone.utc).replace(tzinfo=None) > limite:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Pasaron más de {PLAZO_HORAS_SOLICITUD} horas desde la compra; ya no se puede solicitar.",
        )

    detalle = session.get(VentaDetalle, data.venta_detalle_id)
    if detalle is None or detalle.venta_id != venta.id:
        raise HTTPException(422, "Esa prenda no pertenece a esta venta")

    ya_solicitado = session.exec(
        select(SolicitudCambioDevolucion).where(
            SolicitudCambioDevolucion.venta_detalle_id == detalle.id,
            SolicitudCambioDevolucion.estado != EstadoSolicitud.CANCELADO,
        )
    ).all()
    cantidad_pedida = sum(s.cantidad for s in ya_solicitado)
    if cantidad_pedida + data.cantidad > detalle.cantidad:
        raise HTTPException(
            422,
            f"Ya hay una solicitud por {cantidad_pedida} unidad(es) de esta prenda; "
            f"no se puede pedir más de lo comprado ({detalle.cantidad}).",
        )

    if data.tipo == TipoSolicitud.CAMBIO:
        nueva = session.get(ProductoVariante, data.variante_nueva_id)
        original = session.get(ProductoVariante, detalle.variante_id)
        if nueva is None or original is None:
            raise HTTPException(422, "Una de las prendas elegidas ya no existe")
        if nueva.producto_id != original.producto_id:
            raise HTTPException(
                422, "El cambio solo puede ser de talla o color de la misma prenda"
            )
        if nueva.id == original.id:
            raise HTTPException(422, "Elegí una variante distinta a la comprada")

    solicitud = SolicitudCambioDevolucion(
        venta_id=venta.id,
        venta_detalle_id=detalle.id,
        cliente_id=cliente.id,
        sucursal_id=detalle.sucursal_id or venta.sucursal_id,
        tipo=data.tipo,
        variante_devuelta_id=detalle.variante_id,
        cantidad=data.cantidad,
        variante_nueva_id=data.variante_nueva_id,
        fecha_limite=limite,
    )
    session.add(solicitud)
    session.commit()
    session.refresh(solicitud)
    return _solicitud_out(session, solicitud)


def mis_solicitudes(session: Session, cliente_id: int) -> list[dict]:
    filas = session.exec(
        select(SolicitudCambioDevolucion)
        .where(SolicitudCambioDevolucion.cliente_id == cliente_id)
        .order_by(SolicitudCambioDevolucion.fecha_solicitud.desc())
    ).all()
    return [_solicitud_out(session, s) for s in filas]


def listar_sucursal(
    session: Session,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
    estado: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    filtros = []
    if empresa_id is not None:
        filtros.append(
            SolicitudCambioDevolucion.sucursal_id.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    if sucursal_permitida is not None:
        filtros.append(SolicitudCambioDevolucion.sucursal_id == sucursal_permitida)
    if estado is not None:
        filtros.append(SolicitudCambioDevolucion.estado == estado)

    items, total = paginate(
        session,
        SolicitudCambioDevolucion,
        filters=filtros,
        order_by=(SolicitudCambioDevolucion.fecha_solicitud.desc(),),
        page=page,
        size=size,
    )
    return [_solicitud_out(session, s) for s in items], total


def _solicitud_de_sucursal(
    session: Session,
    solicitud_id: int,
    sucursal_permitida: int | None,
    empresa_id: int | None,
) -> SolicitudCambioDevolucion:
    s = session.get(SolicitudCambioDevolucion, solicitud_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Solicitud no encontrada")
    if sucursal_permitida is not None and s.sucursal_id != sucursal_permitida:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta solicitud es de otra sucursal")
    if empresa_id is not None:
        sucursal = session.get(Sucursal, s.sucursal_id)
        if sucursal is None or sucursal.empresa_id != empresa_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta solicitud es de otra empresa")
    return s


# --------------------------------------------------------------------------- #
#  CU21 — Confirmar recepción de la prenda devuelta
# --------------------------------------------------------------------------- #
def recepcionar(
    session: Session,
    cajero: Usuario,
    solicitud_id: int,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> dict:
    s = _solicitud_de_sucursal(session, solicitud_id, sucursal_permitida, empresa_id)
    if s.estado != EstadoSolicitud.SOLICITADO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Solo se pueden recepcionar solicitudes pendientes"
        )

    inv = session.exec(
        select(Inventario).where(
            Inventario.variante_id == s.variante_devuelta_id,
            Inventario.sucursal_id == s.sucursal_id,
        )
    ).first()
    if inv is None:
        inv = Inventario(
            variante_id=s.variante_devuelta_id,
            sucursal_id=s.sucursal_id,
            cantidad_disponible=0,
            cantidad_reservada=0,
        )
    inv.cantidad_disponible += s.cantidad
    session.add(inv)
    session.add(
        MovimientoInventario(
            variante_id=s.variante_devuelta_id,
            sucursal_id=s.sucursal_id,
            usuario_id=cajero.id,
            tipo=TipoMovimiento.DEVOLUCION_CLIENTE,
            cantidad=s.cantidad,
            nota=f"Recepción de cambio/devolución #{s.id} (venta #{s.venta_id})",
        )
    )

    s.estado = EstadoSolicitud.RECIBIDO
    s.fecha_recepcion = datetime.now(timezone.utc)
    s.responsable_recepcion_id = cajero.id
    session.add(s)
    session.commit()
    session.refresh(s)
    return _solicitud_out(session, s)


# --------------------------------------------------------------------------- #
#  CU19/CU20 — Resolver: entregar la nueva talla, o devolver/reembolsar
# --------------------------------------------------------------------------- #
def _precio_unitario_devuelto(session: Session, s: SolicitudCambioDevolucion) -> Decimal:
    detalle = session.get(VentaDetalle, s.venta_detalle_id)
    return detalle.precio_unitario


def _pago_original_stripe(session: Session, venta_id: int) -> Pago | None:
    return session.exec(
        select(Pago)
        .where(
            Pago.venta_id == venta_id,
            Pago.metodo.in_((MetodoPago.STRIPE, MetodoPago.STRIPE_GUARDADA)),
            Pago.estado == EstadoPago.APROBADO,
        )
        .order_by(Pago.id.desc())
    ).first()


def resolver(
    session: Session,
    cajero: Usuario,
    solicitud_id: int,
    data: ResolverSolicitudIn,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> ResolverSolicitudOut:
    s = _solicitud_de_sucursal(session, solicitud_id, sucursal_permitida, empresa_id)
    if s.estado != EstadoSolicitud.RECIBIDO:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Primero hay que confirmar la recepción de la prenda",
        )
    if data.accion not in (TipoSolicitud.CAMBIO, TipoSolicitud.DEVOLUCION):
        raise HTTPException(422, "La acción debe ser CAMBIO o DEVOLUCION")

    precio_devuelto = _precio_unitario_devuelto(session, s) * s.cantidad
    nueva_variante: ProductoVariante | None = None
    precio_nuevo = Decimal(0)

    if data.accion == TipoSolicitud.CAMBIO:
        if s.variante_nueva_id is None:
            raise HTTPException(422, "Esta solicitud no tiene una variante nueva elegida")
        nueva_variante = session.get(ProductoVariante, s.variante_nueva_id)
        producto_nuevo = session.get(Producto, nueva_variante.producto_id)
        inv_nueva = session.exec(
            select(Inventario)
            .where(
                Inventario.variante_id == nueva_variante.id,
                Inventario.sucursal_id == s.sucursal_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if inv_nueva is None or inv_nueva.cantidad_disponible < s.cantidad:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "No hay stock de esa talla/color en esta sucursal. "
                "Podés convertir la solicitud en devolución.",
            )
        _, precio_unitario_nuevo, _ = ventas_service._precio_venta(
            session, nueva_variante, producto_nuevo
        )
        precio_nuevo = precio_unitario_nuevo * s.cantidad
        diferencia = precio_nuevo - precio_devuelto
    else:
        diferencia = -precio_devuelto

    # --- Resolver el dinero ------------------------------------------------- #
    cobro_pendiente: CobroPendienteOut | None = None
    if diferencia > 0:
        if data.forma_pago is None:
            raise HTTPException(422, "Indicá cómo va a pagar la diferencia")
        if data.forma_pago == FormaPagoDiferencia.EFECTIVO:
            if data.monto_recibido is None or data.monto_recibido < diferencia:
                raise HTTPException(422, "El monto recibido no cubre la diferencia")
        elif data.forma_pago == FormaPagoDiferencia.STRIPE:
            base = settings.frontend_url.rstrip("/")
            stripe_session = stripe_client.crear_checkout_session(
                venta_id=s.venta_id,
                monto_bs=diferencia,
                descripcion=f"Diferencia por cambio de talla — solicitud #{s.id}",
                success_url=f"{base}/mis-cambios?resultado=exito",
                cancel_url=f"{base}/mis-cambios?resultado=cancelado",
            )
            pago = Pago(
                solicitud_cambio_id=s.id,
                metodo=MetodoPago.STRIPE,
                monto=diferencia,
                stripe_session_id=stripe_session.id,
            )
            session.add(pago)
            s.forma_pago_diferencia = FormaPagoDiferencia.STRIPE
            s.diferencia = diferencia
            session.add(s)
            session.commit()
            session.refresh(pago)
            qr = stripe_client.generar_qr_data_url(stripe_session.url)
            return ResolverSolicitudOut(
                solicitud=_solicitud_out(session, s),
                cobro_pendiente=CobroPendienteOut(
                    pago_id=pago.id,
                    monto=diferencia,
                    checkout_url=stripe_session.url,
                    qr_data_url=qr,
                ),
            )
        else:
            raise HTTPException(422, "forma_pago debe ser EFECTIVO o STRIPE")
    elif diferencia < 0:
        if data.forma_pago is None:
            raise HTTPException(422, "Indicá cómo se le devuelve la diferencia al cliente")
        if data.forma_pago == FormaPagoDiferencia.STRIPE:
            pago_original = _pago_original_stripe(session, s.venta_id)
            if pago_original is None:
                raise HTTPException(
                    422, "La venta original no se pagó por Stripe; elegí efectivo"
                )
            payment_intent_id = pago_original.stripe_payment_intent_id
            if not payment_intent_id and pago_original.stripe_session_id:
                # Pagos de antes de que empezáramos a guardar el payment_intent
                # al confirmar el cobro: lo recuperamos de Stripe en el momento.
                stripe_session = stripe_client.obtener_session(
                    pago_original.stripe_session_id
                )
                payment_intent_id = stripe_session.payment_intent
                pago_original.stripe_payment_intent_id = payment_intent_id
                session.add(pago_original)
            if not payment_intent_id:
                raise HTTPException(
                    422, "La venta original no se pagó por Stripe; elegí efectivo"
                )
            reembolso = stripe_client.reembolsar_parcial(payment_intent_id, abs(diferencia))
            session.add(
                Pago(
                    venta_id=s.venta_id,
                    solicitud_cambio_id=s.id,
                    metodo=MetodoPago.STRIPE,
                    estado=EstadoPago.REEMBOLSADO,
                    monto=abs(diferencia),
                    stripe_refund_id=reembolso.id,
                    fecha_reembolso=datetime.now(timezone.utc),
                )
            )
        elif data.forma_pago != FormaPagoDiferencia.EFECTIVO:
            raise HTTPException(422, "forma_pago debe ser EFECTIVO o STRIPE")

    # --- Diferencia resuelta (o no había): aplicar el cambio/devolución ----- #
    if data.accion == TipoSolicitud.CAMBIO and nueva_variante is not None:
        inv_nueva = session.exec(
            select(Inventario).where(
                Inventario.variante_id == nueva_variante.id,
                Inventario.sucursal_id == s.sucursal_id,
            )
        ).first()
        inv_nueva.cantidad_disponible -= s.cantidad
        session.add(inv_nueva)
        session.add(
            MovimientoInventario(
                variante_id=nueva_variante.id,
                sucursal_id=s.sucursal_id,
                usuario_id=cajero.id,
                tipo=TipoMovimiento.SALIDA_CAMBIO,
                cantidad=s.cantidad,
                nota=f"Entrega por cambio #{s.id} (venta #{s.venta_id})",
            )
        )

    s.tipo = data.accion
    s.estado = EstadoSolicitud.RESUELTO
    s.fecha_resolucion = datetime.now(timezone.utc)
    s.responsable_resolucion_id = cajero.id
    s.diferencia = diferencia
    s.forma_pago_diferencia = data.forma_pago if diferencia != 0 else s.forma_pago_diferencia
    session.add(s)
    session.commit()
    session.refresh(s)
    return ResolverSolicitudOut(solicitud=_solicitud_out(session, s))


# --------------------------------------------------------------------------- #
#  Confirmar un cobro de diferencia pagado por Stripe (CU19, continuación)
# --------------------------------------------------------------------------- #
def confirmar_cobro_diferencia(
    session: Session,
    cajero: Usuario,
    solicitud_id: int,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> dict:
    s = _solicitud_de_sucursal(session, solicitud_id, sucursal_permitida, empresa_id)
    pago = session.exec(
        select(Pago)
        .where(Pago.solicitud_cambio_id == s.id, Pago.estado == EstadoPago.PENDIENTE)
        .order_by(Pago.id.desc())
    ).first()
    if pago is None or not pago.stripe_session_id:
        raise HTTPException(422, "No hay un cobro pendiente para esta solicitud")

    stripe_session = stripe_client.obtener_session(pago.stripe_session_id)
    if stripe_session.payment_status != "paid":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Todavía no se registró el pago de la diferencia"
        )

    pago.estado = EstadoPago.APROBADO
    pago.stripe_payment_intent_id = stripe_session.payment_intent
    pago.fecha_actualizacion = datetime.now(timezone.utc)
    session.add(pago)

    nueva_variante = session.get(ProductoVariante, s.variante_nueva_id)
    inv_nueva = session.exec(
        select(Inventario)
        .where(
            Inventario.variante_id == nueva_variante.id,
            Inventario.sucursal_id == s.sucursal_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if inv_nueva is None or inv_nueva.cantidad_disponible < s.cantidad:
        # Se cobró pero justo se agotó el stock mientras esperábamos el pago:
        # no queda otra que reembolsar y avisar.
        reembolso = stripe_client.reembolsar(pago.stripe_payment_intent_id or "")
        pago.estado = EstadoPago.REEMBOLSADO
        pago.stripe_refund_id = reembolso.id if reembolso else None
        pago.fecha_reembolso = datetime.now(timezone.utc)
        session.add(pago)
        session.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Se agotó el stock de esa talla mientras se procesaba el pago; se reembolsó la diferencia.",
        )

    inv_nueva.cantidad_disponible -= s.cantidad
    session.add(inv_nueva)
    session.add(
        MovimientoInventario(
            variante_id=nueva_variante.id,
            sucursal_id=s.sucursal_id,
            usuario_id=cajero.id,
            tipo=TipoMovimiento.SALIDA_CAMBIO,
            cantidad=s.cantidad,
            nota=f"Entrega por cambio #{s.id} (venta #{s.venta_id})",
        )
    )

    s.estado = EstadoSolicitud.RESUELTO
    s.fecha_resolucion = datetime.now(timezone.utc)
    s.responsable_resolucion_id = cajero.id
    session.add(s)
    session.commit()
    session.refresh(s)
    return _solicitud_out(session, s)


# --------------------------------------------------------------------------- #
#  CU22 — Nota (de recepción y/o de resolución)
# --------------------------------------------------------------------------- #
def obtener_nota(
    session: Session,
    solicitud_id: int,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> NotaCambioOut:
    s = _solicitud_de_sucursal(session, solicitud_id, sucursal_permitida, empresa_id)
    cliente = session.get(Usuario, s.cliente_id)
    sucursal = session.get(Sucursal, s.sucursal_id)
    variante = session.get(ProductoVariante, s.variante_devuelta_id)
    producto = session.get(Producto, variante.producto_id) if variante else None
    resp_recepcion = (
        session.get(Usuario, s.responsable_recepcion_id)
        if s.responsable_recepcion_id
        else None
    )
    resp_resolucion = (
        session.get(Usuario, s.responsable_resolucion_id)
        if s.responsable_resolucion_id
        else None
    )
    return NotaCambioOut(
        solicitud_id=s.id,
        tipo=s.tipo,
        venta_id=s.venta_id,
        cliente_nombre=f"{cliente.nombre} {cliente.apellido}" if cliente else "",
        sucursal=sucursal.nombre if sucursal else "",
        producto=producto.nombre if producto else "",
        variante_devuelta=_variante_texto(session, s.variante_devuelta_id) or "",
        variante_nueva=_variante_texto(session, s.variante_nueva_id),
        cantidad=s.cantidad,
        fecha_recepcion=s.fecha_recepcion,
        responsable_recepcion=(
            f"{resp_recepcion.nombre} {resp_recepcion.apellido}" if resp_recepcion else None
        ),
        fecha_resolucion=s.fecha_resolucion,
        responsable_resolucion=(
            f"{resp_resolucion.nombre} {resp_resolucion.apellido}" if resp_resolucion else None
        ),
        diferencia=s.diferencia,
        forma_pago_diferencia=s.forma_pago_diferencia,
        estado=s.estado,
    )
