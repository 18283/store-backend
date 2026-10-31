"""Lógica de negocio del módulo Ventas — CU21 (carrito), CU22 (compra web),
CU27/CU28 (pago electrónico Stripe/QR y su confirmación)."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, func, or_, select

from app.core.config import settings
from app.modules.catalogo.models import Color, Talla
from app.modules.identidad import service as identidad_service
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.identidad.schemas import ClienteRegistroIn
from app.modules.inventario.models import Inventario, MovimientoInventario, TipoMovimiento
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.promociones import service as promociones_service
from app.modules.promociones.models import Promocion
from app.modules.sucursales.models import Sucursal
from app.modules.ventas import stripe_client
from app.modules.ventas.models import (
    Carrito,
    CarritoDetalle,
    EstadoCarrito,
    EstadoPago,
    EstadoVenta,
    MetodoPago,
    Pago,
    Venta,
    VentaDetalle,
)
from app.modules.ventas.schemas import (
    CheckoutCreate,
    ItemCarritoCreate,
    ItemCarritoUpdate,
    ItemVentaPresencialIn,
    PagoCajaCreate,
    VentaPresencialCreate,
)


def _get_o_crear_carrito(session: Session, cliente_id: int) -> Carrito:
    carrito = session.exec(
        select(Carrito).where(
            Carrito.cliente_id == cliente_id, Carrito.estado == EstadoCarrito.ACTIVO
        )
    ).first()
    if carrito is None:
        carrito = Carrito(cliente_id=cliente_id)
        session.add(carrito)
        session.commit()
        session.refresh(carrito)
    return carrito


def _hay_stock(session: Session, variante_id: int) -> bool:
    total = session.exec(
        select(func.coalesce(func.sum(Inventario.cantidad_disponible), 0))
        .join(Sucursal, Inventario.sucursal_id == Sucursal.id)  # type: ignore[arg-type]
        .where(Inventario.variante_id == variante_id, Sucursal.activa == True)  # noqa: E712
    ).one()
    return total > 0


def _precio_venta(
    session: Session, variante: ProductoVariante, producto: Producto
) -> tuple[Decimal, Decimal, int | None]:
    """(precio original, precio final con la mejor promoción vigente, id de
    la promoción aplicada o None) — CU33."""
    original = variante.precio if variante.precio is not None else producto.precio_base
    final, promo = promociones_service.mejor_precio(
        original,
        promociones_service.promos_de_variante(session, variante.id, producto.id),
    )
    return original, final, promo.id if promo else None


def _item_out(session: Session, d: CarritoDetalle) -> dict:
    variante = session.get(ProductoVariante, d.variante_id)
    producto = session.get(Producto, variante.producto_id) if variante else None
    talla = session.get(Talla, variante.talla_id) if variante else None
    color = session.get(Color, variante.color_id) if variante else None

    precio = None
    precio_original = None
    promocion = None
    if variante is not None and producto is not None and producto.precio_base is not None:
        precio_original, precio, promocion_id = _precio_venta(session, variante, producto)
        if promocion_id is not None:
            promocion = session.get(Promocion, promocion_id).nombre
    subtotal = precio * d.cantidad if precio is not None else None

    return {
        "id": d.id,
        "variante_id": d.variante_id,
        "producto_id": producto.id if producto else None,
        "producto": producto.nombre if producto else None,
        "talla": talla.valor if talla else None,
        "color": color.nombre if color else None,
        "sku": variante.sku if variante else None,
        "imagen_efectivo": (variante.imagen_url if variante else None)
        or (producto.imagen_url if producto else None),
        "precio_unitario": precio,
        "precio_original": precio_original,
        "promocion": promocion,
        "cantidad": d.cantidad,
        "subtotal": subtotal,
        "disponible": _hay_stock(session, d.variante_id) if variante else False,
    }


def _carrito_out(session: Session, carrito: Carrito) -> dict:
    detalles = session.exec(
        select(CarritoDetalle)
        .where(CarritoDetalle.carrito_id == carrito.id)
        .order_by(CarritoDetalle.id)
    ).all()
    items = [_item_out(session, d) for d in detalles]
    total = sum((i["subtotal"] or Decimal(0)) for i in items) if items else Decimal(0)
    return {
        "id": carrito.id,
        "estado": carrito.estado,
        "items": items,
        "cantidad_items": sum(i["cantidad"] for i in items),
        "total": total,
    }


def mi_carrito(session: Session, cliente_id: int) -> dict:
    carrito = _get_o_crear_carrito(session, cliente_id)
    return _carrito_out(session, carrito)


def agregar_item(session: Session, cliente_id: int, data: ItemCarritoCreate) -> dict:
    variante = session.get(ProductoVariante, data.variante_id)
    if variante is None:
        raise HTTPException(422, "Esa prenda ya no existe")
    producto = session.get(Producto, variante.producto_id)
    if producto is None or not producto.activo or producto.precio_base is None:
        raise HTTPException(422, "Esa prenda ya no está disponible")
    if not _hay_stock(session, data.variante_id):
        raise HTTPException(422, "No hay stock de esa prenda por ahora")

    carrito = _get_o_crear_carrito(session, cliente_id)
    existente = session.exec(
        select(CarritoDetalle).where(
            CarritoDetalle.carrito_id == carrito.id,
            CarritoDetalle.variante_id == data.variante_id,
        )
    ).first()
    if existente:
        existente.cantidad = min(existente.cantidad + data.cantidad, 20)
        session.add(existente)
    else:
        session.add(
            CarritoDetalle(
                carrito_id=carrito.id,
                variante_id=data.variante_id,
                cantidad=data.cantidad,
            )
        )
    carrito.fecha_actualizacion = datetime.now(timezone.utc)
    session.add(carrito)
    session.commit()
    return mi_carrito(session, cliente_id)


def _item_del_cliente(
    session: Session, item_id: int, cliente_id: int
) -> CarritoDetalle:
    item = session.get(CarritoDetalle, item_id)
    if item is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Ese ítem no está en tu carrito"
        )
    carrito = session.get(Carrito, item.carrito_id)
    if carrito is None or carrito.cliente_id != cliente_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Ese ítem no está en tu carrito"
        )
    return item


def actualizar_item(
    session: Session, cliente_id: int, item_id: int, data: ItemCarritoUpdate
) -> dict:
    item = _item_del_cliente(session, item_id, cliente_id)
    item.cantidad = data.cantidad
    session.add(item)
    session.commit()
    return mi_carrito(session, cliente_id)


def quitar_item(session: Session, cliente_id: int, item_id: int) -> dict:
    item = _item_del_cliente(session, item_id, cliente_id)
    session.delete(item)
    session.commit()
    return mi_carrito(session, cliente_id)


def vaciar_carrito(session: Session, cliente_id: int) -> dict:
    carrito = _get_o_crear_carrito(session, cliente_id)
    for d in session.exec(
        select(CarritoDetalle).where(CarritoDetalle.carrito_id == carrito.id)
    ).all():
        session.delete(d)
    session.commit()
    return mi_carrito(session, cliente_id)


# --------------------------------------------------------------------------- #
#  CU22 — Comprar desde Plataforma Web
# --------------------------------------------------------------------------- #
def _venta_out(session: Session, venta: Venta) -> dict:
    detalles = session.exec(
        select(VentaDetalle).where(VentaDetalle.venta_id == venta.id)
    ).all()
    sucursales: dict[int, Sucursal | None] = {}

    def _sucursal(sucursal_id: int) -> Sucursal | None:
        if sucursal_id not in sucursales:
            sucursales[sucursal_id] = session.get(Sucursal, sucursal_id)
        return sucursales[sucursal_id]

    items = []
    for d in detalles:
        variante = session.get(ProductoVariante, d.variante_id)
        producto = session.get(Producto, variante.producto_id) if variante else None
        talla = session.get(Talla, variante.talla_id) if variante else None
        color = session.get(Color, variante.color_id) if variante else None
        origen = _sucursal(d.sucursal_id or venta.sucursal_id)
        items.append(
            {
                "id": d.id,
                "variante_id": d.variante_id,
                "producto_id": producto.id if producto else None,
                "producto": producto.nombre if producto else None,
                "talla": talla.valor if talla else None,
                "color": color.nombre if color else None,
                "sku": variante.sku if variante else None,
                "cantidad": d.cantidad,
                "precio_unitario": d.precio_unitario,
                "precio_original": d.precio_original
                if d.precio_original is not None
                else d.precio_unitario,
                "subtotal": d.precio_unitario * d.cantidad,
                "sucursal": origen.nombre if origen else None,
            }
        )
    varias = len({d.sucursal_id or venta.sucursal_id for d in detalles}) > 1
    principal = _sucursal(venta.sucursal_id)
    return {
        "id": venta.id,
        "sucursal_id": venta.sucursal_id,
        "sucursal": "Varias sucursales"
        if varias
        else (principal.nombre if principal else None),
        "ciudad": None if varias else (principal.ciudad if principal else None),
        "varias_sucursales": varias,
        "reserva_id": venta.reserva_id,
        "direccion_entrega": venta.direccion_entrega,
        "referencia_entrega": venta.referencia_entrega,
        "estado": venta.estado,
        "total": venta.total,
        "fecha_creacion": venta.fecha_creacion,
        "items": items,
    }


def _venta_del_cliente(session: Session, venta_id: int, cliente_id: int) -> Venta:
    venta = session.get(Venta, venta_id)
    if venta is None or venta.cliente_id != cliente_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Venta no encontrada")
    return venta


def _inventarios_con_stock(session: Session, variante_id: int) -> list[Inventario]:
    return session.exec(
        select(Inventario)
        .join(Sucursal, Sucursal.id == Inventario.sucursal_id)  # type: ignore[arg-type]
        .where(
            Inventario.variante_id == variante_id,
            Sucursal.activa == True,  # noqa: E712
            Inventario.cantidad_disponible > 0,
        )
        .order_by(Inventario.sucursal_id)
    ).all()


def _asignar_despacho(
    session: Session, pedidos: list[tuple[ProductoVariante, Producto, int]]
) -> list[tuple[ProductoVariante, int, Inventario]]:
    """Decide de qué sucursal(es) sale cada prenda de una compra en línea.

    1) Si una sola sucursal puede cubrir todo el pedido, sale todo de ahí.
    2) Si no, se reparte: cada prenda se toma de las sucursales ya usadas
       primero (para no abrir envíos de más) y luego de la que más stock
       tenga; una misma prenda puede salir de dos sucursales si hace falta.
    """
    inventarios = {v.id: _inventarios_con_stock(session, v.id) for v, _, _ in pedidos}

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
            raise HTTPException(
                422,
                f"No hay stock suficiente de '{producto.nombre}'. Ajustá la cantidad.",
            )
    return asignacion

#logica de venta desde el carrito
def crear_checkout(session: Session, cliente_id: int, data: CheckoutCreate) -> dict:
    direccion = data.direccion_entrega.strip()
    if len(direccion) < 5:
        raise HTTPException(422, "Ingresá una dirección de entrega válida")

    carrito = _get_o_crear_carrito(session, cliente_id)
    detalles_carrito = session.exec(
        select(CarritoDetalle).where(CarritoDetalle.carrito_id == carrito.id)
    ).all()
    if not detalles_carrito:
        raise HTTPException(422, "Tu carrito está vacío")

    pedidos = []
    for d in detalles_carrito:
        variante = session.get(ProductoVariante, d.variante_id)
        if variante is None:
            raise HTTPException(422, "Una de las prendas de tu carrito ya no existe")
        producto = session.get(Producto, variante.producto_id)
        if producto is None or not producto.activo or producto.precio_base is None:
            raise HTTPException(
                422,
                f"'{producto.nombre if producto else variante.sku}' ya no está disponible",
            )
        pedidos.append((variante, producto, d.cantidad))

    precios = {v.id: _precio_venta(session, v, p) for v, p, _ in pedidos}
    lineas = []
    for variante, cantidad, inv in _asignar_despacho(session, pedidos):
        original, precio, promocion_id = precios[variante.id]
        lineas.append((variante, cantidad, precio, inv, original, promocion_id))

    total = sum(precio * cantidad for _, cantidad, precio, *_ in lineas)

    monto_por_sucursal: dict[int, Decimal] = {}
    for _, cantidad, precio, inv, *_ in lineas:
        monto_por_sucursal[inv.sucursal_id] = (
            monto_por_sucursal.get(inv.sucursal_id, Decimal(0)) + precio * cantidad
        )
    sucursal_principal = max(monto_por_sucursal, key=lambda s: monto_por_sucursal[s])

    venta = Venta(
        cliente_id=cliente_id,
        sucursal_id=sucursal_principal,
        total=total,
        direccion_entrega=direccion,
        referencia_entrega=(data.referencia_entrega or "").strip() or None,
    )
    session.add(venta)
    session.flush()  # necesitamos venta.id para el detalle

    for variante, cantidad, precio, inv, original, promocion_id in lineas:
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
                usuario_id=cliente_id,
                tipo=TipoMovimiento.SALIDA_VENTA,
                cantidad=cantidad,
                nota=f"Venta #{venta.id}",
            )
        )

    for d in detalles_carrito:
        session.delete(d)
    carrito.estado = EstadoCarrito.CONVERTIDO
    carrito.fecha_actualizacion = datetime.now(timezone.utc)
    session.add(carrito)

    session.commit()
    session.refresh(venta)
    return _venta_out(session, venta)


# --------------------------------------------------------------------------- #
#  CU12/CU14 — Tarjeta guardada (cobro offline sin que el cliente esté presente)
# --------------------------------------------------------------------------- #
def iniciar_guardar_tarjeta(session: Session, usuario: Usuario) -> dict:
    if usuario.stripe_customer_id is None:
        customer_id = stripe_client.crear_customer(
            email=usuario.email, nombre=f"{usuario.nombre} {usuario.apellido}"
        )
        usuario.stripe_customer_id = customer_id
        session.add(usuario)
        session.commit()
        session.refresh(usuario)

    base = settings.frontend_url.rstrip("/")
    stripe_session = stripe_client.crear_setup_session(
        customer_id=usuario.stripe_customer_id,
        success_url=f"{base}/mi-cuenta?tarjeta=exito",
        cancel_url=f"{base}/mi-cuenta?tarjeta=cancelado",
    )
    return {"checkout_url": stripe_session.url}


def confirmar_tarjeta_guardada(session: Session, usuario: Usuario) -> dict:
    if usuario.stripe_customer_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Todavía no iniciaste el guardado de una tarjeta"
        )
    exitoso = stripe_client.ultimo_setup_intent_exitoso(usuario.stripe_customer_id)
    if exitoso is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Todavía no se confirmó el guardado de la tarjeta"
        )
    usuario.stripe_payment_method_id = exitoso.payment_method
    session.add(usuario)
    session.commit()
    return {"tiene_tarjeta_guardada": True}


def estado_tarjeta_guardada(usuario: Usuario) -> dict:
    return {"tiene_tarjeta_guardada": usuario.stripe_payment_method_id is not None}


def eliminar_tarjeta_guardada(session: Session, usuario: Usuario) -> dict:
    usuario.stripe_payment_method_id = None
    session.add(usuario)
    session.commit()
    return {"tiene_tarjeta_guardada": False}


def mis_ventas(session: Session, cliente_id: int) -> list[dict]:
    ventas = session.exec(
        select(Venta)
        .where(Venta.cliente_id == cliente_id)
        .order_by(Venta.fecha_creacion.desc())
    ).all()
    return [_venta_out(session, v) for v in ventas]


def _devolver_stock_de_venta(
    session: Session, venta: Venta, usuario_id: int, nota: str
) -> None:
    """Devuelve al stock disponible (de la sucursal de origen de cada línea)
    lo que una venta pendiente había descontado, y la deja ANULADA."""
    detalles = session.exec(
        select(VentaDetalle).where(VentaDetalle.venta_id == venta.id)
    ).all()
    for d in detalles:
        origen_id = d.sucursal_id or venta.sucursal_id
        inv = session.exec(
            select(Inventario).where(
                Inventario.variante_id == d.variante_id,
                Inventario.sucursal_id == origen_id,
            )
        ).first()
        if inv is not None:
            inv.cantidad_disponible += d.cantidad
            session.add(inv)
        session.add(
            MovimientoInventario(
                variante_id=d.variante_id,
                sucursal_id=origen_id,
                usuario_id=usuario_id,
                tipo=TipoMovimiento.ANULACION_VENTA,
                cantidad=d.cantidad,
                nota=nota,
            )
        )
    venta.estado = EstadoVenta.ANULADA
    session.add(venta)


def cancelar_venta(session: Session, cliente_id: int, venta_id: int) -> dict:
    venta = _venta_del_cliente(session, venta_id, cliente_id)
    if venta.reserva_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esta compra viene de una reserva y se cobra en caja.",
        )
    if venta.estado != EstadoVenta.PENDIENTE_PAGO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esta venta ya no se puede cancelar"
        )

    _devolver_stock_de_venta(
        session, venta, cliente_id, f"Cancelación de la venta #{venta.id}"
    )
    session.commit()
    return _venta_out(session, venta)


# --------------------------------------------------------------------------- #
#  CU27 — Procesar Pago Electrónico (QR / Stripe)
# --------------------------------------------------------------------------- #
def _pago_out(
    pago: Pago, *, checkout_url: str | None = None, qr_data_url: str | None = None
) -> dict:
    return {
        "id": pago.id,
        "venta_id": pago.venta_id,
        "metodo": pago.metodo,
        "estado": pago.estado,
        "monto": pago.monto,
        "checkout_url": checkout_url,
        "qr_data_url": qr_data_url,
        "fecha_creacion": pago.fecha_creacion,
    }

#pago mediante stripe que genera un qr y checkout
def iniciar_pago(session: Session, cliente_id: int, venta_id: int) -> dict:
    venta = _venta_del_cliente(session, venta_id, cliente_id)
    if venta.reserva_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esta compra viene de una reserva y se paga en caja.",
        )
    if venta.estado != EstadoVenta.PENDIENTE_PAGO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esta venta ya no está pendiente de pago"
        )

    base = settings.frontend_url.rstrip("/")
    stripe_session = stripe_client.crear_checkout_session(
        venta_id=venta.id,
        monto_bs=venta.total,
        descripcion=f"FashionStore - Pedido #{venta.id}",
        success_url=f"{base}/checkout/resultado?venta_id={venta.id}&resultado=exito",
        cancel_url=f"{base}/checkout/resultado?venta_id={venta.id}&resultado=cancelado",
    )

    pago = Pago(
        venta_id=venta.id,
        metodo=MetodoPago.STRIPE,
        monto=venta.total,
        stripe_session_id=stripe_session.id,
    )
    session.add(pago)
    session.commit()
    session.refresh(pago)

    qr_data_url = stripe_client.generar_qr_data_url(stripe_session.url)
    return _pago_out(pago, checkout_url=stripe_session.url, qr_data_url=qr_data_url)


# --------------------------------------------------------------------------- #
#  CU28 — Confirmar / Rechazar Transacción
# --------------------------------------------------------------------------- #
def consultar_estado_pago(session: Session, cliente_id: int, venta_id: int) -> dict:
    """CU28: en vez de depender de un webhook (que necesita una URL pública
    que no existe en localhost), consultamos activamente el estado en Stripe.
    Funciona igual en local y una vez desplegado."""
    venta = _venta_del_cliente(session, venta_id, cliente_id)
    ultimo_pago = session.exec(
        select(Pago).where(Pago.venta_id == venta.id).order_by(Pago.id.desc())
    ).first()

    if (
        ultimo_pago is not None
        and ultimo_pago.stripe_session_id
        and ultimo_pago.estado in (EstadoPago.PENDIENTE, EstadoPago.PROCESANDO)
    ):
        stripe_session = stripe_client.obtener_session(ultimo_pago.stripe_session_id)
        if stripe_session.payment_status == "paid":
            ultimo_pago.estado = EstadoPago.APROBADO
            ultimo_pago.fecha_actualizacion = datetime.now(timezone.utc)
            session.add(ultimo_pago)
            # El despacho ocurre fuera del sistema (no hay actor de delivery):
            # pagar online ya completa la venta, igual que en caja.
            venta.estado = EstadoVenta.COMPLETADA
            session.add(venta)
            session.commit()
        elif stripe_session.status == "expired":
            ultimo_pago.estado = EstadoPago.RECHAZADO
            ultimo_pago.fecha_actualizacion = datetime.now(timezone.utc)
            session.add(ultimo_pago)
            session.commit()

    return {
        "venta_id": venta.id,
        "venta_estado": venta.estado,
        "pago_id": ultimo_pago.id if ultimo_pago else None,
        "pago_estado": ultimo_pago.estado if ultimo_pago else None,
    }


# --------------------------------------------------------------------------- #
#  CU24/25/26 — Venta presencial, pago en caja, comprobante (rol Cajero)
# --------------------------------------------------------------------------- #
def _sucursal_del_cajero(cajero: Usuario) -> int:
    if cajero.sucursal_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tu cuenta no está vinculada a ninguna sucursal. Contactá al administrador.",
        )
    return cajero.sucursal_id


def buscar_clientes(
    session: Session, q: str, *, empresa_id: int | None = None
) -> list[Usuario]:
    rol_cliente = session.exec(
        select(Rol).where(Rol.nombre == RolNombre.CLIENTE)
    ).first()
    if rol_cliente is None:
        return []

    q = q.strip()
    consulta = select(Usuario).where(
        Usuario.rol_id == rol_cliente.id,
        Usuario.activo == True,  # noqa: E712
    )
    if empresa_id is not None:
        consulta = consulta.where(Usuario.empresa_id == empresa_id)
    if q:
        patron = f"%{q.lower()}%"
        consulta = consulta.where(
            or_(
                func.lower(Usuario.nombre).like(patron),
                func.lower(Usuario.apellido).like(patron),
                func.lower(Usuario.email).like(patron),
                func.lower(func.coalesce(Usuario.telefono, "")).like(patron),
            )
        ).order_by(Usuario.nombre)
    else:
        # Sin texto: mostrar los últimos clientes registrados, como ayuda
        # antes de escribir nada (igual que con la lista de prendas).
        consulta = consulta.order_by(Usuario.fecha_registro.desc())

    return session.exec(consulta.limit(15)).all()


def registrar_cliente_rapido(
    session: Session, data: ClienteRegistroIn, *, empresa_id: int | None = None
) -> Usuario:
    """El cajero da de alta a un cliente nuevo desde el mostrador, con las
    mismas reglas que el registro público (CU1), para poder ligarle la venta."""
    return identidad_service.registrar_cliente(
        session, data, empresa_id=empresa_id
    )


def _resolver_lineas_presencial(
    session: Session, items: list[ItemVentaPresencialIn], sucursal_id: int
) -> list[tuple[ProductoVariante, int, Decimal, Inventario, Decimal, int | None]]:
    lineas = []
    for item in items:
        variante = session.get(ProductoVariante, item.variante_id)
        if variante is None:
            raise HTTPException(422, "Una de las prendas ya no existe")
        producto = session.get(Producto, variante.producto_id)
        if producto is None or not producto.activo or producto.precio_base is None:
            raise HTTPException(
                422,
                f"'{producto.nombre if producto else variante.sku}' ya no está disponible",
            )
        inv = session.exec(
            select(Inventario).where(
                Inventario.variante_id == item.variante_id,
                Inventario.sucursal_id == sucursal_id,
            )
        ).first()
        if inv is None or inv.cantidad_disponible < item.cantidad:
            raise HTTPException(
                422, f"No hay stock suficiente de '{producto.nombre}' en tu sucursal."
            )
        original, precio, promocion_id = _precio_venta(session, variante, producto)
        lineas.append((variante, item.cantidad, precio, inv, original, promocion_id))
    return lineas


def crear_venta_presencial(
    session: Session, cajero: Usuario, data: VentaPresencialCreate
) -> dict:
    sucursal_id = _sucursal_del_cajero(cajero)

    rol_cliente = session.exec(
        select(Rol).where(Rol.nombre == RolNombre.CLIENTE)
    ).first()
    cliente = session.get(Usuario, data.cliente_id)
    if (
        cliente is None
        or (rol_cliente and cliente.rol_id != rol_cliente.id)
        or cliente.empresa_id != cajero.empresa_id
    ):
        raise HTTPException(422, "Ese cliente no existe")

    lineas = _resolver_lineas_presencial(session, data.items, sucursal_id)
    total = sum(precio * cantidad for _, cantidad, precio, *_ in lineas)

    venta = Venta(
        cliente_id=cliente.id,
        sucursal_id=sucursal_id,
        cajero_id=cajero.id,
        total=total,
    )
    session.add(venta)
    session.flush()  # necesitamos venta.id para el detalle

    for variante, cantidad, precio, inv, original, promocion_id in lineas:
        session.add(
            VentaDetalle(
                venta_id=venta.id,
                variante_id=variante.id,
                sucursal_id=sucursal_id,
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
                sucursal_id=sucursal_id,
                usuario_id=cajero.id,
                tipo=TipoMovimiento.SALIDA_VENTA,
                cantidad=cantidad,
                nota=f"Venta presencial #{venta.id}",
            )
        )

    session.commit()
    session.refresh(venta)
    return _venta_out(session, venta)


def _venta_presencial_de_la_sucursal(
    session: Session, venta_id: int, sucursal_id: int
) -> Venta:
    venta = session.get(Venta, venta_id)
    # Son de caja las ventas con cajero (presenciales) y las que nacieron de
    # una reserva (todavía sin cajero hasta que se cobran).
    es_de_caja = venta is not None and (
        venta.cajero_id is not None or venta.reserva_id is not None
    )
    if venta is None or venta.sucursal_id != sucursal_id or not es_de_caja:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Venta no encontrada")
    return venta


def procesar_pago_caja(
    session: Session, cajero: Usuario, venta_id: int, data: PagoCajaCreate
) -> dict:
    sucursal_id = _sucursal_del_cajero(cajero)
    venta = _venta_presencial_de_la_sucursal(session, venta_id, sucursal_id)
    if venta.estado != EstadoVenta.PENDIENTE_PAGO:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esta venta ya no está pendiente de pago"
        )
    if data.metodo not in (MetodoPago.EFECTIVO, MetodoPago.TARJETA_CAJA):
        raise HTTPException(422, "Método de pago inválido para una venta en caja")

    monto_recibido = None
    vuelto = None
    if data.metodo == MetodoPago.EFECTIVO:
        if data.monto_recibido is None or data.monto_recibido < venta.total:
            raise HTTPException(
                422, "El monto recibido es menor al total de la venta"
            )
        monto_recibido = data.monto_recibido
        vuelto = monto_recibido - venta.total

    pago = Pago(
        venta_id=venta.id,
        metodo=data.metodo,
        estado=EstadoPago.APROBADO,
        monto=venta.total,
        monto_recibido=monto_recibido,
        vuelto=vuelto,
    )
    session.add(pago)

    # A diferencia de la compra web (donde pagar y retirar son dos momentos
    # distintos), en una venta presencial el cliente se lleva la prenda ahí
    # mismo: pagar ya implica la venta completa, no queda un paso pendiente.
    venta.estado = EstadoVenta.COMPLETADA
    if venta.cajero_id is None:  # venta de reserva: la cobra este cajero
        venta.cajero_id = cajero.id
    session.add(venta)
    session.commit()
    return _venta_out(session, venta)


def ventas_por_cobrar(session: Session, cajero: Usuario) -> list[dict]:
    """Ventas de reservas finalizadas en la sucursal, esperando el cobro."""
    sucursal_id = _sucursal_del_cajero(cajero)
    ventas = session.exec(
        select(Venta)
        .where(
            Venta.sucursal_id == sucursal_id,
            Venta.reserva_id.is_not(None),
            Venta.estado == EstadoVenta.PENDIENTE_PAGO,
        )
        .order_by(Venta.fecha_creacion.desc())
    ).all()
    return [_venta_caja_out(session, v) for v in ventas]


def anular_venta_por_cobrar(session: Session, cajero: Usuario, venta_id: int) -> dict:
    """El cliente decidió no llevarse lo que iba a pagar: vuelve al stock."""
    sucursal_id = _sucursal_del_cajero(cajero)
    venta = _venta_presencial_de_la_sucursal(session, venta_id, sucursal_id)
    if venta.reserva_id is None or venta.estado != EstadoVenta.PENDIENTE_PAGO:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo se pueden anular las ventas de reserva pendientes de cobro",
        )
    _devolver_stock_de_venta(
        session,
        venta,
        cajero.id,
        f"Venta #{venta.id} de la reserva #{venta.reserva_id} no cobrada",
    )
    session.commit()
    return _venta_out(session, venta)


def _venta_caja_out(session: Session, venta: Venta) -> dict:
    base = _venta_out(session, venta)
    cliente = session.get(Usuario, venta.cliente_id)
    cajero = session.get(Usuario, venta.cajero_id) if venta.cajero_id else None
    base["cliente_id"] = venta.cliente_id
    base["cliente_nombre"] = f"{cliente.nombre} {cliente.apellido}" if cliente else "—"
    base["cajero_nombre"] = f"{cajero.nombre} {cajero.apellido}" if cajero else None
    return base


def historial_caja(session: Session, cajero: Usuario) -> list[dict]:
    sucursal_id = _sucursal_del_cajero(cajero)
    ventas = session.exec(
        select(Venta)
        .where(Venta.sucursal_id == sucursal_id, Venta.cajero_id.is_not(None))
        .order_by(Venta.fecha_creacion.desc())
        .limit(100)
    ).all()
    return [_venta_caja_out(session, v) for v in ventas]


def emitir_comprobante(session: Session, cajero: Usuario, venta_id: int) -> dict:
    sucursal_id = _sucursal_del_cajero(cajero)
    venta = _venta_presencial_de_la_sucursal(session, venta_id, sucursal_id)
    if venta.estado not in (EstadoVenta.COMPLETADA, EstadoVenta.PAGADA):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Esta venta todavía no tiene un pago aprobado"
        )

    sucursal = session.get(Sucursal, venta.sucursal_id)
    cliente = session.get(Usuario, venta.cliente_id)
    cajero_venta = session.get(Usuario, venta.cajero_id) if venta.cajero_id else None
    pago = session.exec(
        select(Pago)
        .where(Pago.venta_id == venta.id, Pago.estado == EstadoPago.APROBADO)
        .order_by(Pago.id.desc())
    ).first()

    return {
        "venta_id": venta.id,
        "fecha_creacion": venta.fecha_creacion,
        "sucursal": sucursal.nombre if sucursal else "—",
        "ciudad": sucursal.ciudad if sucursal else "—",
        "direccion": sucursal.direccion if sucursal else "—",
        "cliente_nombre": f"{cliente.nombre} {cliente.apellido}" if cliente else "—",
        "cliente_email": cliente.email if cliente else "—",
        "cajero_nombre": (
            f"{cajero_venta.nombre} {cajero_venta.apellido}" if cajero_venta else None
        ),
        "items": _venta_out(session, venta)["items"],
        "total": venta.total,
        "metodo_pago": pago.metodo if pago else "—",
        "monto_recibido": pago.monto_recibido if pago else None,
        "vuelto": pago.vuelto if pago else None,
    }


# --------------------------------------------------------------------------- #
#  CU36/CU37 — Consultar Ventas (Global para Admin, de la sucursal para Encargado)
# --------------------------------------------------------------------------- #
def listar_ventas_sucursal(
    session: Session,
    *,
    sucursal_id: int | None,
    estado: str | None,
    sucursal_permitida: int | None,
    page: int,
    size: int,
    empresa_id: int | None = None,
) -> tuple[list[dict], int]:
    """sucursal_permitida viene del router: None = admin (puede filtrar por
    cualquier sucursal o ver todas), un id = encargado (forzado a la suya)."""
    def _sale_de(sucursal: int):
        # Una compra en línea puede salir de varias sucursales: cada una la ve.
        return or_(
            Venta.sucursal_id == sucursal,
            Venta.id.in_(
                select(VentaDetalle.venta_id).where(VentaDetalle.sucursal_id == sucursal)
            ),
        )

    base = select(Venta)
    if empresa_id is not None:
        sucursales_empresa = select(Sucursal.id).where(
            Sucursal.empresa_id == empresa_id
        )
        base = base.where(
            or_(
                Venta.sucursal_id.in_(sucursales_empresa),
                Venta.id.in_(
                    select(VentaDetalle.venta_id).where(
                        VentaDetalle.sucursal_id.in_(sucursales_empresa)
                    )
                ),
            )
        )
    if sucursal_permitida is not None:
        base = base.where(_sale_de(sucursal_permitida))
    elif sucursal_id is not None:
        base = base.where(_sale_de(sucursal_id))
    if estado:
        base = base.where(Venta.estado == estado)

    total = session.exec(select(func.count()).select_from(base.subquery())).one()
    ventas = session.exec(
        base.order_by(Venta.fecha_creacion.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return [_venta_caja_out(session, v) for v in ventas], total
