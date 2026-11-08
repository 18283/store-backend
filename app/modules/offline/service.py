from datetime import datetime, timezone
from decimal import Decimal

import stripe
from fastapi import HTTPException
from sqlmodel import Session, select

from app.core import push
from app.modules.catalogo.models import Categoria, Color, Talla
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.inventario.models import Inventario, MovimientoInventario, TipoMovimiento
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.reservas import service as reservas_service
from app.modules.reservas.schemas import ItemReservaCreate, ReservaCreate
from app.modules.saas.models import CodigoFuncionalidad
from app.modules.saas.service import validar_acceso_plan
from app.modules.sucursales.models import Sucursal
from app.modules.ventas import service as ventas_service
from app.modules.ventas import stripe_client
from app.modules.ventas.models import (
    EstadoPago,
    EstadoVenta,
    MetodoPago,
    OrigenVenta,
    Pago,
    Venta,
    VentaDetalle,
)

from .models import (
    DispositivoPush,
    EstadoOperacionOffline,
    EstadoReembolsoEfectivo,
    OperacionSincronizada,
    ReembolsoEfectivo,
    TipoOperacionOffline,
)
from .schemas import (
    ClienteOfflineOut,
    ConfirmarReembolsoEfectivoIn,
    DatosOfflineOut,
    DispositivoPushIn,
    ProductoOfflineOut,
    ReembolsoEfectivoOut,
    ResultadoSincronizacionOut,
    SincronizarReservaIn,
    SincronizarVentaEfectivoIn,
    SincronizarVentaIn,
    VarianteOfflineOut,
)


def _sucursal_autorizada(
    session: Session, usuario: Usuario, sucursal_id: int | None
) -> Sucursal:
    if usuario.empresa_id is None:
        raise HTTPException(403, "El usuario no está asociado a una empresa")
    if usuario.sucursal_id is not None:
        if sucursal_id is not None and sucursal_id != usuario.sucursal_id:
            raise HTTPException(403, "No puede preparar datos de otra sucursal")
        sucursal_id = usuario.sucursal_id
    if sucursal_id is None:
        sucursales = session.exec(
            select(Sucursal).where(
                Sucursal.empresa_id == usuario.empresa_id,
                Sucursal.activa == True,  # noqa: E712
            )
        ).all()
        if len(sucursales) != 1:
            raise HTTPException(422, "Seleccione la sucursal que desea preparar")
        return sucursales[0]
    sucursal = session.get(Sucursal, sucursal_id)
    if (
        sucursal is None
        or sucursal.empresa_id != usuario.empresa_id
        or not sucursal.activa
    ):
        raise HTTPException(404, "Sucursal no encontrada para esta empresa")
    return sucursal


def _clientes_para_offline(session: Session, usuario: Usuario) -> list[ClienteOfflineOut]:
    """CU12 (Cajero) — solo el personal de staff necesita elegir un cliente
    presencial para la venta en efectivo; no se expone la lista de clientes
    en la copia offline de un Cliente (seria filtrar datos de otros)."""
    rol = session.get(Rol, usuario.rol_id)
    if rol is None or rol.nombre not in (
        RolNombre.ADMINISTRADOR,
        RolNombre.ENCARGADO,
        RolNombre.CAJERO,
    ):
        return []
    rol_cliente = session.exec(
        select(Rol).where(Rol.nombre == RolNombre.CLIENTE)
    ).first()
    if rol_cliente is None:
        return []
    clientes = session.exec(
        select(Usuario)
        .where(
            Usuario.rol_id == rol_cliente.id,
            Usuario.empresa_id == usuario.empresa_id,
            Usuario.activo == True,  # noqa: E712
        )
        .order_by(Usuario.nombre)
        .limit(500)
    ).all()
    return [
        ClienteOfflineOut(
            id=c.id,
            nombre=c.nombre,
            apellido=c.apellido,
            email=c.email,
            telefono=c.telefono,
        )
        for c in clientes
    ]


def preparar_datos(
    session: Session, usuario: Usuario, sucursal_id: int | None
) -> DatosOfflineOut:
    if usuario.empresa_id is None:
        raise HTTPException(403, "El usuario no está asociado a una empresa")
    validar_acceso_plan(
        session,
        usuario.empresa_id,
        funcionalidad=CodigoFuncionalidad.PWA_OFFLINE,
    )
    sucursal = _sucursal_autorizada(session, usuario, sucursal_id)
    filas = session.exec(
        select(Inventario, ProductoVariante, Producto, Categoria, Talla, Color)
        .join(ProductoVariante, ProductoVariante.id == Inventario.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .join(Categoria, Categoria.id == Producto.categoria_id)
        .join(Talla, Talla.id == ProductoVariante.talla_id)
        .join(Color, Color.id == ProductoVariante.color_id)
        .where(
            Inventario.sucursal_id == sucursal.id,
            Producto.activo == True,  # noqa: E712
        )
        .order_by(Producto.nombre, ProductoVariante.id)
    ).all()

    productos: dict[int, ProductoOfflineOut] = {}
    for inventario, variante, producto, categoria, talla, color in filas:
        if producto.id not in productos:
            productos[producto.id] = ProductoOfflineOut(
                id=producto.id,
                nombre=producto.nombre,
                descripcion=producto.descripcion,
                categoria_id=categoria.id,
                categoria=categoria.nombre,
                precio_base=producto.precio_base,
                imagen_url=producto.imagen_url,
                variantes=[],
            )
        productos[producto.id].variantes.append(
            VarianteOfflineOut(
                id=variante.id,
                sku=variante.sku,
                talla_id=talla.id,
                talla=talla.valor,
                color_id=color.id,
                color=color.nombre,
                codigo_hex=color.codigo_hex,
                precio=variante.precio or producto.precio_base,
                imagen_url=variante.imagen_url or producto.imagen_url,
                cantidad_disponible=inventario.cantidad_disponible,
                cantidad_reservada=inventario.cantidad_reservada,
            )
        )

    ahora = datetime.now(timezone.utc)
    lista = list(productos.values())
    total_variantes = sum(len(item.variantes) for item in lista)
    clientes = _clientes_para_offline(session, usuario)
    resultado = DatosOfflineOut(
        version_datos=f"1-{usuario.empresa_id}-{sucursal.id}-{int(ahora.timestamp())}",
        empresa_id=usuario.empresa_id,
        sucursal_id=sucursal.id,
        sucursal_nombre=sucursal.nombre,
        generado_en=ahora,
        productos=lista,
        total_productos=len(lista),
        total_variantes=total_variantes,
        clientes=clientes,
        total_clientes=len(clientes),
    )
    # CU35 — Avisar sincronización del catálogo offline (opcional).
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=usuario.id,
        mensaje=f"Catálogo offline sincronizado ({len(lista)} productos).",
        tipo="info",
        modulo="sincronizacion",
    )
    return resultado


def _exigir_acceso_offline(session: Session, usuario: Usuario) -> None:
    if usuario.empresa_id is None:
        raise HTTPException(403, "El usuario no está asociado a una empresa")
    validar_acceso_plan(
        session, usuario.empresa_id, funcionalidad=CodigoFuncionalidad.PWA_OFFLINE
    )


def _resultado_de(op: OperacionSincronizada) -> ResultadoSincronizacionOut:
    return ResultadoSincronizacionOut(
        clave_idempotencia=op.clave_idempotencia,
        tipo=op.tipo,
        estado=op.estado,
        motivo_rechazo=op.motivo_rechazo,
        reserva_id=op.reserva_id,
        venta_id=op.venta_id,
    )


def _buscar_operacion(session: Session, clave: str) -> OperacionSincronizada | None:
    return session.exec(
        select(OperacionSincronizada).where(
            OperacionSincronizada.clave_idempotencia == clave
        )
    ).first()


def _registrar_operacion(
    session: Session,
    usuario_id: int,
    tipo: str,
    clave: str,
    estado: str,
    *,
    reserva_id: int | None = None,
    venta_id: int | None = None,
    motivo_rechazo: str | None = None,
) -> OperacionSincronizada:
    op = OperacionSincronizada(
        clave_idempotencia=clave,
        usuario_id=usuario_id,
        tipo=tipo,
        estado=estado,
        reserva_id=reserva_id,
        venta_id=venta_id,
        motivo_rechazo=motivo_rechazo,
    )
    session.add(op)
    session.commit()
    session.refresh(op)
    return op


# --------------------------------------------------------------------------- #
#  CU14/CU15 — Registro del dispositivo, para avisar por push
# --------------------------------------------------------------------------- #
def registrar_dispositivo(
    session: Session, usuario: Usuario, data: DispositivoPushIn
) -> dict:
    existente = session.exec(
        select(DispositivoPush).where(DispositivoPush.token == data.token)
    ).first()
    if existente is not None:
        existente.usuario_id = usuario.id
        existente.plataforma = data.plataforma
        session.add(existente)
    else:
        session.add(
            DispositivoPush(
                usuario_id=usuario.id, token=data.token, plataforma=data.plataforma
            )
        )
    session.commit()
    return {"registrado": True}


# --------------------------------------------------------------------------- #
#  CU11/CU14 — Sincronizar una reserva registrada sin conexión
# --------------------------------------------------------------------------- #
def sincronizar_reserva(
    session: Session, usuario: Usuario, data: SincronizarReservaIn
) -> ResultadoSincronizacionOut:
    existente = _buscar_operacion(session, data.clave_idempotencia)
    if existente is not None:
        return _resultado_de(existente)

    _exigir_acceso_offline(session, usuario)

    reserva_data = ReservaCreate(
        sucursal_id=data.sucursal_id,
        fecha=data.fecha,
        hora_inicio=data.hora_inicio,
        duracion_minutos=data.duracion_minutos,
        items=[
            ItemReservaCreate(variante_id=i.variante_id, cantidad=i.cantidad)
            for i in data.items
        ],
    )
    try:
        reserva_out = reservas_service.crear_reserva(
            session, usuario.id, reserva_data, empresa_id=usuario.empresa_id
        )
    except HTTPException as exc:
        session.rollback()
        motivo = (
            exc.detail if isinstance(exc.detail, str) else "No se pudo confirmar la reserva"
        )
        op = _registrar_operacion(
            session,
            usuario.id,
            TipoOperacionOffline.RESERVA,
            data.clave_idempotencia,
            EstadoOperacionOffline.RECHAZADO,
            motivo_rechazo=motivo,
        )
        push.enviar_push_a_usuario(
            session, usuario.id, "No se pudo confirmar tu reserva", motivo, {"tipo": "reserva"}
        )
        from app.modules.traspasos.notificacion_service import crear_notificacion_segura

        crear_notificacion_segura(
            session=session,
            usuario_id=usuario.id,
            mensaje=f"No se pudo confirmar tu reserva sincronizada: {motivo}",
            tipo="warning",
            modulo="sincronizacion",
        )
        return _resultado_de(op)

    op = _registrar_operacion(
        session,
        usuario.id,
        TipoOperacionOffline.RESERVA,
        data.clave_idempotencia,
        EstadoOperacionOffline.CONFIRMADO,
        reserva_id=reserva_out["id"],
    )
    push.enviar_push_a_usuario(
        session,
        usuario.id,
        "Reserva confirmada",
        "Tu reserva hecha sin conexión se confirmó correctamente.",
        {"tipo": "reserva", "reserva_id": str(reserva_out["id"])},
    )
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=usuario.id,
        mensaje="Tu reserva hecha sin conexión se confirmó correctamente.",
        tipo="success",
        modulo="sincronizacion",
    )
    return _resultado_de(op)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Sincronizar una venta de contado registrada sin conexión
# --------------------------------------------------------------------------- #
def _inventarios_con_stock_bloqueando(
    session: Session, variante_id: int
) -> list[Inventario]:
    filas = session.exec(
        select(Inventario)
        .join(Sucursal, Sucursal.id == Inventario.sucursal_id)  # type: ignore[arg-type]
        .where(Inventario.variante_id == variante_id, Sucursal.activa == True)  # noqa: E712
        .with_for_update()
        .execution_options(populate_existing=True)
        .order_by(Inventario.sucursal_id)
    ).all()
    return [i for i in filas if i.cantidad_disponible > 0]


def _asignar_despacho_bloqueando(
    session: Session, pedidos: list[tuple[ProductoVariante, Producto, int]]
) -> list[tuple[ProductoVariante, int, Inventario]] | None:
    """Igual que `ventas.service._asignar_despacho`, pero bloqueando las filas
    de Inventario: CU14 valida el stock recién al sincronizar, cuando puede
    haber otra operación (online u offline) compitiendo por lo mismo.
    Devuelve None (en vez de levantar) cuando no alcanza el stock."""
    inventarios = {
        v.id: _inventarios_con_stock_bloqueando(session, v.id) for v, _, _ in pedidos
    }
    completas: set[int] | None = None
    for variante, _, cantidad in pedidos:
        cubren = {
            i.sucursal_id
            for i in inventarios[variante.id]
            if i.cantidad_disponible >= cantidad
        }
        completas = cubren if completas is None else completas & cubren
    if completas:
        elegida = min(completas)
        return [
            (
                variante,
                cantidad,
                next(i for i in inventarios[variante.id] if i.sucursal_id == elegida),
            )
            for variante, _, cantidad in pedidos
        ]

    usadas: set[int] = set()
    asignacion: list[tuple[ProductoVariante, int, Inventario]] = []
    for variante, producto, cantidad in pedidos:
        restante = cantidad
        candidatos = sorted(
            inventarios[variante.id],
            key=lambda i: (i.sucursal_id not in usadas, -i.cantidad_disponible, i.sucursal_id),
        )
        for inv in candidatos:
            if restante == 0:
                break
            tomar = min(restante, inv.cantidad_disponible)
            asignacion.append((variante, tomar, inv))
            usadas.add(inv.sucursal_id)
            restante -= tomar
        if restante > 0:
            return None
    return asignacion


def _sucursal_de_referencia(session: Session, empresa_id: int | None) -> int | None:
    """Sucursal para dejar registrada una venta reembolsada que nunca llegó a
    asignarse a ninguna (no había stock en ninguna sucursal)."""
    query = select(Sucursal).where(Sucursal.activa == True)  # noqa: E712
    if empresa_id is not None:
        query = query.where(Sucursal.empresa_id == empresa_id)
    sucursal = session.exec(query.order_by(Sucursal.id)).first()
    return sucursal.id if sucursal else None


def sincronizar_venta(
    session: Session, usuario: Usuario, data: SincronizarVentaIn
) -> ResultadoSincronizacionOut:
    existente = _buscar_operacion(session, data.clave_idempotencia)
    if existente is not None:
        return _resultado_de(existente)

    _exigir_acceso_offline(session, usuario)

    def _rechazar(motivo: str) -> ResultadoSincronizacionOut:
        op = _registrar_operacion(
            session,
            usuario.id,
            TipoOperacionOffline.VENTA,
            data.clave_idempotencia,
            EstadoOperacionOffline.RECHAZADO,
            motivo_rechazo=motivo,
        )
        push.enviar_push_a_usuario(
            session, usuario.id, "No se pudo completar tu compra", motivo, {"tipo": "venta"}
        )
        from app.modules.traspasos.notificacion_service import crear_notificacion_segura

        crear_notificacion_segura(
            session=session,
            usuario_id=usuario.id,
            mensaje=f"No se pudo completar tu compra sincronizada: {motivo}",
            tipo="warning",
            modulo="sincronizacion",
        )
        return _resultado_de(op)

    if usuario.stripe_customer_id is None or usuario.stripe_payment_method_id is None:
        return _rechazar(
            "No tenés una tarjeta guardada para cobrar la compra sin conexión."
        )

    pedidos: list[tuple[ProductoVariante, Producto, int]] = []
    for item in data.items:
        variante = session.get(ProductoVariante, item.variante_id)
        if variante is None:
            return _rechazar("Una de las prendas de tu compra ya no existe.")
        producto = session.get(Producto, variante.producto_id)
        if producto is None or not producto.activo or producto.precio_base is None:
            nombre = producto.nombre if producto else variante.sku
            return _rechazar(f"'{nombre}' ya no está disponible.")
        pedidos.append((variante, producto, item.cantidad))

    precios = {v.id: ventas_service._precio_venta(session, v, p) for v, p, _ in pedidos}
    total = sum((precios[v.id][1] * cantidad for v, _, cantidad in pedidos), Decimal(0))

    # 1) Se cobra PRIMERO con la tarjeta guardada — a propósito, no se valida
    #    el stock antes: la ingeniera pidió que el ingreso y, si hace falta,
    #    el reembolso (egreso) queden visibles en el reporte de dinero.
    try:
        payment_intent = stripe_client.cobrar_tarjeta_guardada(
            customer_id=usuario.stripe_customer_id,
            payment_method_id=usuario.stripe_payment_method_id,
            monto_bs=total,
            descripcion=f"Venta offline #{data.clave_idempotencia[:12]}",
        )
    except stripe.error.CardError as exc:
        return _rechazar(f"No se pudo cobrar: {exc.user_message or 'tarjeta rechazada'}.")

    # 2) Recién ahora se valida y descuenta el stock, con las filas bloqueadas.
    asignacion = _asignar_despacho_bloqueando(session, pedidos)
    if asignacion is None:
        stripe_client.reembolsar(payment_intent.id)
        venta = Venta(
            cliente_id=usuario.id,
            sucursal_id=_sucursal_de_referencia(session, usuario.empresa_id),
            total=total,
            direccion_entrega=data.direccion_entrega,
            referencia_entrega=data.referencia_entrega,
            origen=OrigenVenta.OFFLINE,
            estado=EstadoVenta.ANULADA,
        )
        session.add(venta)
        session.flush()
        session.add(
            Pago(
                venta_id=venta.id,
                metodo=MetodoPago.STRIPE_GUARDADA,
                estado=EstadoPago.REEMBOLSADO,
                monto=total,
                stripe_payment_intent_id=payment_intent.id,
                stripe_refund_id="auto",
                fecha_reembolso=datetime.now(timezone.utc),
            )
        )
        session.commit()
        return _rechazar(
            "Otro cliente se adelantó y ya no había stock. Se te devolvió el dinero."
        )

    venta = Venta(
        cliente_id=usuario.id,
        sucursal_id=asignacion[0][2].sucursal_id,
        total=total,
        direccion_entrega=data.direccion_entrega,
        referencia_entrega=data.referencia_entrega,
        origen=OrigenVenta.OFFLINE,
        estado=EstadoVenta.PAGADA,
    )
    session.add(venta)
    session.flush()  # necesitamos venta.id para el detalle

    for variante, cantidad, inv in asignacion:
        original, precio, promocion_id = precios[variante.id]
        session.add(
            VentaDetalle(
                venta_id=venta.id,
                variante_id=variante.id,
                sucursal_id=inv.sucursal_id,
                cantidad=cantidad,
                precio_unitario=precio,
                precio_original=original,
                promocion_id=promocion_id,
                costo_unitario=inv.costo_promedio,
            )
        )
        inv.cantidad_disponible -= cantidad
        session.add(inv)
        session.add(
            MovimientoInventario(
                variante_id=variante.id,
                sucursal_id=inv.sucursal_id,
                usuario_id=usuario.id,
                tipo=TipoMovimiento.SALIDA_VENTA,
                cantidad=cantidad,
                nota=f"Venta offline #{venta.id}",
            )
        )
    session.add(
        Pago(
            venta_id=venta.id,
            metodo=MetodoPago.STRIPE_GUARDADA,
            estado=EstadoPago.APROBADO,
            monto=total,
            stripe_payment_intent_id=payment_intent.id,
        )
    )
    session.commit()
    session.refresh(venta)

    op = _registrar_operacion(
        session,
        usuario.id,
        TipoOperacionOffline.VENTA,
        data.clave_idempotencia,
        EstadoOperacionOffline.CONFIRMADO,
        venta_id=venta.id,
    )
    push.enviar_push_a_usuario(
        session,
        usuario.id,
        "Compra confirmada",
        "Tu compra hecha sin conexión se completó y ya se cobró a tu tarjeta.",
        {"tipo": "venta", "venta_id": str(venta.id)},
    )
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=usuario.id,
        mensaje="Tu compra hecha sin conexión se completó y ya se cobró a tu tarjeta.",
        tipo="success",
        modulo="sincronizacion",
    )
    return _resultado_de(op)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Sincronizar una venta de contado EN EFECTIVO del Cajero
#  (cliente presencial, sin tarjeta guardada).
# --------------------------------------------------------------------------- #
def sincronizar_venta_efectivo(
    session: Session, cajero: Usuario, data: SincronizarVentaEfectivoIn
) -> ResultadoSincronizacionOut:
    existente = _buscar_operacion(session, data.clave_idempotencia)
    if existente is not None:
        return _resultado_de(existente)

    _exigir_acceso_offline(session, cajero)
    sucursal_id = ventas_service._sucursal_del_cajero(cajero)

    def _rechazar(motivo: str, venta_id: int | None = None) -> ResultadoSincronizacionOut:
        op = _registrar_operacion(
            session,
            cajero.id,
            TipoOperacionOffline.VENTA,
            data.clave_idempotencia,
            EstadoOperacionOffline.RECHAZADO,
            venta_id=venta_id,
            motivo_rechazo=motivo,
        )
        push.enviar_push_a_usuario(
            session, cajero.id, "No se pudo registrar la venta", motivo, {"tipo": "venta"}
        )
        from app.modules.traspasos.notificacion_service import crear_notificacion_segura

        crear_notificacion_segura(
            session=session,
            usuario_id=cajero.id,
            mensaje=f"No se pudo registrar la venta en efectivo sincronizada: {motivo}",
            tipo="warning",
            modulo="sincronizacion",
        )
        return _resultado_de(op)

    rol_cliente = session.exec(select(Rol).where(Rol.nombre == RolNombre.CLIENTE)).first()
    cliente = session.get(Usuario, data.cliente_id)
    if (
        cliente is None
        or (rol_cliente and cliente.rol_id != rol_cliente.id)
        or cliente.empresa_id != cajero.empresa_id
    ):
        return _rechazar("El cliente indicado no existe.")

    pedidos: list[tuple[ProductoVariante, Producto, int]] = []
    for item in data.items:
        variante = session.get(ProductoVariante, item.variante_id)
        if variante is None:
            return _rechazar("Una de las prendas de la venta ya no existe.")
        producto = session.get(Producto, variante.producto_id)
        if producto is None or not producto.activo or producto.precio_base is None:
            nombre = producto.nombre if producto else variante.sku
            return _rechazar(f"'{nombre}' ya no está disponible.")
        pedidos.append((variante, producto, item.cantidad))

    precios = {v.id: ventas_service._precio_venta(session, v, p) for v, p, _ in pedidos}
    total = sum((precios[v.id][1] * cantidad for v, _, cantidad in pedidos), Decimal(0))

    if data.monto_recibido < total:
        return _rechazar("El monto recibido es menor al total de la venta.")

    # El efectivo ya se cobró físicamente en el mostrador, antes de que
    # volviera la conexión — a diferencia de Stripe, acá NO hay nada que
    # cobrar ahora. Lo único que falta es ver si todavía hay stock real en
    # el servidor para esta sucursal (filas bloqueadas, como en CU14).
    inventarios: dict[int, Inventario] = {}
    alcanza = True
    for variante, _, cantidad in pedidos:
        inv = session.exec(
            select(Inventario)
            .where(
                Inventario.variante_id == variante.id,
                Inventario.sucursal_id == sucursal_id,
            )
            .with_for_update()
        ).first()
        inventarios[variante.id] = inv
        if inv is None or inv.cantidad_disponible < cantidad:
            alcanza = False

    venta = Venta(
        cliente_id=cliente.id,
        sucursal_id=sucursal_id,
        cajero_id=cajero.id,
        total=total,
        origen=OrigenVenta.OFFLINE,
        estado=EstadoVenta.PAGADA if alcanza else EstadoVenta.ANULADA,
    )
    session.add(venta)
    session.flush()

    # Se deja constancia de las líneas aunque la venta se anule: es la
    # única forma de saber qué prenda(s) hay que recuperar cuando el cliente
    # vuelva a devolverlas (CU16/CU21).
    for variante, _, cantidad in pedidos:
        original, precio, promocion_id = precios[variante.id]
        inv = inventarios[variante.id]
        session.add(
            VentaDetalle(
                venta_id=venta.id,
                variante_id=variante.id,
                sucursal_id=sucursal_id,
                cantidad=cantidad,
                precio_unitario=precio,
                precio_original=original,
                promocion_id=promocion_id,
                costo_unitario=inv.costo_promedio if inv else None,
            )
        )
        if alcanza and inv is not None:
            inv.cantidad_disponible -= cantidad
            session.add(inv)
            session.add(
                MovimientoInventario(
                    variante_id=variante.id,
                    sucursal_id=sucursal_id,
                    usuario_id=cajero.id,
                    tipo=TipoMovimiento.SALIDA_VENTA,
                    cantidad=cantidad,
                    nota=f"Venta offline en efectivo #{venta.id}",
                )
            )

    vuelto = data.monto_recibido - total

    if not alcanza:
        pago = Pago(
            venta_id=venta.id,
            metodo=MetodoPago.EFECTIVO,
            estado=EstadoPago.PENDIENTE_REEMBOLSO,
            monto=total,
            monto_recibido=data.monto_recibido,
            vuelto=vuelto,
        )
        session.add(pago)
        session.flush()
        session.add(
            ReembolsoEfectivo(
                venta_id=venta.id,
                pago_id=pago.id,
                monto=total,
            )
        )
        session.commit()
        return _rechazar(
            "Otro cliente se adelantó y ya no había stock. El cliente debe "
            "devolver la prenda en la sucursal para que se le devuelva el efectivo.",
            venta_id=venta.id,
        )

    pago = Pago(
        venta_id=venta.id,
        metodo=MetodoPago.EFECTIVO,
        estado=EstadoPago.APROBADO,
        monto=total,
        monto_recibido=data.monto_recibido,
        vuelto=vuelto,
    )
    session.add(pago)
    session.commit()
    session.refresh(venta)

    op = _registrar_operacion(
        session,
        cajero.id,
        TipoOperacionOffline.VENTA,
        data.clave_idempotencia,
        EstadoOperacionOffline.CONFIRMADO,
        venta_id=venta.id,
    )
    push.enviar_push_a_usuario(
        session,
        cajero.id,
        "Venta confirmada",
        "La venta en efectivo registrada sin conexión se confirmó correctamente.",
        {"tipo": "venta", "venta_id": str(venta.id)},
    )
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=cajero.id,
        mensaje="La venta en efectivo registrada sin conexión se confirmó correctamente.",
        tipo="success",
        modulo="sincronizacion",
    )
    return _resultado_de(op)


# --------------------------------------------------------------------------- #
#  CU16 — Consultar y confirmar devoluciones de efectivo pendientes
# --------------------------------------------------------------------------- #
def listar_reembolsos_pendientes(
    session: Session,
    *,
    sucursal_permitida: int | None,
    empresa_id: int | None,
) -> list[ReembolsoEfectivoOut]:
    query = (
        select(ReembolsoEfectivo, Venta, Usuario, Sucursal)
        .join(Venta, Venta.id == ReembolsoEfectivo.venta_id)
        .join(Usuario, Usuario.id == Venta.cliente_id)
        .join(Sucursal, Sucursal.id == Venta.sucursal_id)
        .where(ReembolsoEfectivo.estado == EstadoReembolsoEfectivo.PENDIENTE)
        .order_by(ReembolsoEfectivo.fecha_creacion)
    )
    if sucursal_permitida is not None:
        query = query.where(Venta.sucursal_id == sucursal_permitida)
    elif empresa_id is not None:
        query = query.where(Sucursal.empresa_id == empresa_id)

    filas = session.exec(query).all()
    return [
        ReembolsoEfectivoOut(
            id=reembolso.id,
            venta_id=venta.id,
            cliente_nombre=f"{cliente.nombre} {cliente.apellido}",
            sucursal_id=sucursal.id,
            sucursal=sucursal.nombre,
            monto=reembolso.monto,
            estado=reembolso.estado,
            fecha_creacion=reembolso.fecha_creacion,
            fecha_entregado=reembolso.fecha_entregado,
        )
        for reembolso, venta, cliente, sucursal in filas
    ]


def confirmar_reembolso_efectivo(
    session: Session,
    cajero: Usuario,
    reembolso_id: int,
    data: ConfirmarReembolsoEfectivoIn,
    *,
    sucursal_permitida: int | None,
    empresa_id: int | None,
) -> ReembolsoEfectivoOut:
    reembolso = session.get(ReembolsoEfectivo, reembolso_id)
    if reembolso is None:
        raise HTTPException(404, "Reembolso no encontrado")
    venta = session.get(Venta, reembolso.venta_id)
    if venta is None:
        raise HTTPException(404, "Venta no encontrada")
    sucursal = session.get(Sucursal, venta.sucursal_id)
    if empresa_id is not None and (sucursal is None or sucursal.empresa_id != empresa_id):
        raise HTTPException(403, "Ese reembolso es de otra empresa")
    if sucursal_permitida is not None and venta.sucursal_id != sucursal_permitida:
        raise HTTPException(403, "Ese reembolso es de otra sucursal")
    if reembolso.estado != EstadoReembolsoEfectivo.PENDIENTE:
        raise HTTPException(409, "Este reembolso ya fue confirmado")

    pago = session.get(Pago, reembolso.pago_id)
    detalles = session.exec(
        select(VentaDetalle).where(VentaDetalle.venta_id == venta.id)
    ).all()

    ahora = datetime.now(timezone.utc)
    for detalle in detalles:
        if detalle.sucursal_id is None:
            continue
        inv = session.exec(
            select(Inventario).where(
                Inventario.variante_id == detalle.variante_id,
                Inventario.sucursal_id == detalle.sucursal_id,
            )
        ).first()
        if inv is None:
            inv = Inventario(
                variante_id=detalle.variante_id,
                sucursal_id=detalle.sucursal_id,
                cantidad_disponible=0,
                cantidad_reservada=0,
            )
        inv.cantidad_disponible += detalle.cantidad
        session.add(inv)
        session.add(
            MovimientoInventario(
                variante_id=detalle.variante_id,
                sucursal_id=detalle.sucursal_id,
                usuario_id=cajero.id,
                tipo=TipoMovimiento.DEVOLUCION_CLIENTE,
                cantidad=detalle.cantidad,
                nota=f"Devolución de efectivo #{reembolso.id} (venta #{venta.id})",
            )
        )

    reembolso.estado = EstadoReembolsoEfectivo.ENTREGADO
    reembolso.cajero_confirma_id = cajero.id
    reembolso.nota = data.nota
    reembolso.fecha_entregado = ahora
    session.add(reembolso)

    if pago is not None:
        pago.estado = EstadoPago.REEMBOLSADO
        pago.fecha_reembolso = ahora
        session.add(pago)

    session.commit()
    session.refresh(reembolso)

    cliente = session.get(Usuario, venta.cliente_id)
    return ReembolsoEfectivoOut(
        id=reembolso.id,
        venta_id=venta.id,
        cliente_nombre=f"{cliente.nombre} {cliente.apellido}" if cliente else "",
        sucursal_id=sucursal.id if sucursal else venta.sucursal_id,
        sucursal=sucursal.nombre if sucursal else "",
        monto=reembolso.monto,
        estado=reembolso.estado,
        fecha_creacion=reembolso.fecha_creacion,
        fecha_entregado=reembolso.fecha_entregado,
    )
