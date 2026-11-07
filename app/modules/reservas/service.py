"""Lógica de negocio del módulo Reservas — CU16/CU17/CU18/CU19."""

from datetime import date, datetime, time
from decimal import Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.crud import paginate
from app.modules.catalogo.models import Color, Talla
from app.modules.identidad.models import Usuario
from app.modules.inventario.models import Inventario, MovimientoInventario, TipoMovimiento
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.reservas.models import EstadoReserva, Reserva, ReservaDetalle
from app.modules.reservas.schemas import (
    FinalizarReservaIn,
    ReservaCreate,
    SlotsDisponibilidad,
)
from app.modules.sucursales.models import Sucursal
from app.modules.sucursales.service import horario_del_dia
from app.modules.ventas import service as ventas_service
from app.modules.ventas.models import Venta, VentaDetalle

DURACIONES_VALIDAS = (30, 60)


def _a_minutos(t: time) -> int:
    return t.hour * 60 + t.minute


def _a_hora(minutos: int) -> time:
    return time(hour=minutos // 60, minute=minutos % 60)


def _get_sucursal_activa(
    session: Session, sucursal_id: int, empresa_id: int | None = None
) -> Sucursal:
    sucursal = session.get(Sucursal, sucursal_id)
    if (
        sucursal is None
        or not sucursal.activa
        or (empresa_id is not None and sucursal.empresa_id != empresa_id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sucursal no encontrada")
    return sucursal


def _reservas_activas_del_dia(
    session: Session, sucursal_id: int, fecha: date
) -> list[Reserva]:
    return session.exec(
        select(Reserva).where(
            Reserva.sucursal_id == sucursal_id,
            Reserva.fecha == fecha,
            Reserva.estado.in_(EstadoReserva.ACTIVOS),
        )
    ).all()


# --------------------------------------------------------------------------- #
#  Consultar turnos libres
# --------------------------------------------------------------------------- #
def slots_disponibles(
    session: Session,
    sucursal_id: int,
    fecha: date,
    duracion_minutos: int,
    *,
    empresa_id: int | None = None,
) -> SlotsDisponibilidad:
    if duracion_minutos not in DURACIONES_VALIDAS:
        raise HTTPException(422, "La duración del turno debe ser 30 o 60 minutos")
    _get_sucursal_activa(session, sucursal_id, empresa_id)
    if fecha < date.today():
        return SlotsDisponibilidad(
            fecha=fecha, duracion_minutos=duracion_minutos, cerrado=True, slots=[]
        )

    horario = horario_del_dia(session, sucursal_id, fecha.weekday())
    if horario.cerrado or horario.hora_apertura is None or horario.hora_cierre is None:
        return SlotsDisponibilidad(
            fecha=fecha, duracion_minutos=duracion_minutos, cerrado=True, slots=[]
        )

    apertura = _a_minutos(horario.hora_apertura)
    cierre = _a_minutos(horario.hora_cierre)

    ocupados = [
        (_a_minutos(r.hora_inicio), _a_minutos(r.hora_fin))
        for r in _reservas_activas_del_dia(session, sucursal_id, fecha)
    ]

    limite_inferior = None
    if fecha == date.today():
        limite_inferior = _a_minutos(datetime.now().time())

    libres: list[time] = []
    cursor = apertura
    while cursor + duracion_minutos <= cierre:
        fin = cursor + duracion_minutos
        pasado = limite_inferior is not None and cursor <= limite_inferior
        solapa = any(cursor < o_fin and fin > o_inicio for o_inicio, o_fin in ocupados)
        if not pasado and not solapa:
            libres.append(_a_hora(cursor))
        cursor += 30

    return SlotsDisponibilidad(
        fecha=fecha, duracion_minutos=duracion_minutos, cerrado=False, slots=libres
    )


# --------------------------------------------------------------------------- #
#  Serialización
# --------------------------------------------------------------------------- #
def _reserva_out(session: Session, r: Reserva) -> dict:
    sucursal = session.get(Sucursal, r.sucursal_id)
    detalles = session.exec(
        select(ReservaDetalle).where(ReservaDetalle.reserva_id == r.id)
    ).all()
    items = []
    for d in detalles:
        variante = session.get(ProductoVariante, d.variante_id)
        producto = session.get(Producto, variante.producto_id) if variante else None
        talla = session.get(Talla, variante.talla_id) if variante else None
        color = session.get(Color, variante.color_id) if variante else None
        items.append(
            {
                "detalle_id": d.id,
                "cantidad_llevada": d.cantidad_llevada,
                "variante_id": d.variante_id,
                "producto_id": producto.id if producto else None,
                "producto": producto.nombre if producto else None,
                "talla": talla.valor if talla else None,
                "color": color.nombre if color else None,
                "sku": variante.sku if variante else None,
                "cantidad": d.cantidad,
            }
        )
    return {
        "id": r.id,
        "sucursal_id": r.sucursal_id,
        "sucursal": sucursal.nombre if sucursal else None,
        "ciudad": sucursal.ciudad if sucursal else None,
        "fecha": r.fecha,
        "hora_inicio": r.hora_inicio,
        "hora_fin": r.hora_fin,
        "duracion_minutos": r.duracion_minutos,
        "estado": r.estado,
        "fecha_creacion": r.fecha_creacion,
        "items": items,
    }


# --------------------------------------------------------------------------- #
#  Crear reserva
# --------------------------------------------------------------------------- #
def crear_reserva(
    session: Session,
    cliente_id: int,
    data: ReservaCreate,
    *,
    empresa_id: int | None = None,
) -> dict:
    if data.duracion_minutos not in DURACIONES_VALIDAS:
        raise HTTPException(422, "La duración del turno debe ser 30 o 60 minutos")
    if data.hora_inicio.minute not in (0, 30) or data.hora_inicio.second != 0:
        raise HTTPException(422, "El turno debe empezar en punto o y media")
    if data.fecha < date.today():
        raise HTTPException(422, "No se puede reservar en una fecha pasada")

    # CU16: serializar la comprobacion de turnos de esta sucursal.
    # Se bloquea una fila existente, incluso si aun no hay reservas del dia.
    session.exec(
        select(Sucursal)
        .where(Sucursal.id == data.sucursal_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    _get_sucursal_activa(session, data.sucursal_id, empresa_id)

    horario = horario_del_dia(session, data.sucursal_id, data.fecha.weekday())
    if horario.cerrado or horario.hora_apertura is None or horario.hora_cierre is None:
        raise HTTPException(409, "La sucursal está cerrada ese día")

    inicio_min = _a_minutos(data.hora_inicio)
    fin_min = inicio_min + data.duracion_minutos
    if inicio_min < _a_minutos(horario.hora_apertura) or fin_min > _a_minutos(
        horario.hora_cierre
    ):
        raise HTTPException(409, "Ese horario está fuera de la atención de la sucursal")

    if data.fecha == date.today() and inicio_min <= _a_minutos(datetime.now().time()):
        raise HTTPException(409, "Ese horario ya pasó")

    for r in _reservas_activas_del_dia(session, data.sucursal_id, data.fecha):
        if inicio_min < _a_minutos(r.hora_fin) and fin_min > _a_minutos(r.hora_inicio):
            raise HTTPException(409, "Ese turno ya está reservado por otro cliente")

    # Si una misma variante llega repetida se suman las cantidades: así el
    # stock se valida (y se descuenta) una sola vez por variante.
    cantidades: dict[int, int] = {}
    for item in data.items:
        cantidades[item.variante_id] = cantidades.get(item.variante_id, 0) + item.cantidad

    # Validar variantes + stock disponible en esa sucursal
    inventarios: dict[int, Inventario] = {}
    for variante_id, cantidad in sorted(cantidades.items()):
        if session.get(ProductoVariante, variante_id) is None:
            raise HTTPException(422, "Una de las prendas elegidas ya no existe")
        inv = session.exec(
            select(Inventario).where(
                Inventario.variante_id == variante_id,
                Inventario.sucursal_id == data.sucursal_id,
            )
            # CU16: leer stock actualizado DESPUES de obtener el bloqueo.
            # Se conserva hasta commit/rollback; otra reserva debe esperar.
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if inv is None or inv.cantidad_disponible < cantidad:
            raise HTTPException(
                422, "No hay stock suficiente de esa prenda en la sucursal elegida"
            )
        inventarios[variante_id] = inv

    reserva = Reserva(
        cliente_id=cliente_id,
        sucursal_id=data.sucursal_id,
        fecha=data.fecha,
        hora_inicio=data.hora_inicio,
        hora_fin=_a_hora(fin_min),
        duracion_minutos=data.duracion_minutos,
    )
    session.add(reserva)
    session.flush()  # necesitamos reserva.id para el detalle

    for variante_id, cantidad in cantidades.items():
        inv = inventarios[variante_id]
        inv.cantidad_disponible -= cantidad
        inv.cantidad_reservada += cantidad
        session.add(inv)
        session.add(
            ReservaDetalle(
                reserva_id=reserva.id,
                variante_id=variante_id,
                cantidad=cantidad,
            )
        )

    session.commit()
    session.refresh(reserva)
    # CU35 — Avisar al Cliente en el centro de notificaciones.
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=cliente_id,
        mensaje=f"Tu reserva #{reserva.id} fue creada para el {reserva.fecha} a las {reserva.hora_inicio}.",
        tipo="success",
        modulo="reservas",
    )
    return _reserva_out(session, reserva)


def mis_reservas(session: Session, cliente_id: int) -> list[dict]:
    filas = session.exec(
        select(Reserva)
        .where(Reserva.cliente_id == cliente_id)
        .order_by(Reserva.fecha.desc(), Reserva.hora_inicio.desc())
    ).all()
    return [_reserva_out(session, r) for r in filas]


# --------------------------------------------------------------------------- #
#  CU17 — Cancelar reserva
# --------------------------------------------------------------------------- #
def cancelar_reserva(session: Session, cliente_id: int, reserva_id: int) -> dict:
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reserva no encontrada")
    if reserva.cliente_id != cliente_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Esta reserva es de otro cliente")
    if reserva.estado != EstadoReserva.PENDIENTE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esta reserva ya no se puede cancelar (no está pendiente).",
        )

    detalles = session.exec(
        select(ReservaDetalle).where(ReservaDetalle.reserva_id == reserva_id)
    ).all()
    for d in detalles:
        inv = session.exec(
            select(Inventario).where(
                Inventario.variante_id == d.variante_id,
                Inventario.sucursal_id == reserva.sucursal_id,
            )
        ).first()
        if inv is not None:
            inv.cantidad_disponible += d.cantidad
            inv.cantidad_reservada = max(0, inv.cantidad_reservada - d.cantidad)
            session.add(inv)
        session.add(
            MovimientoInventario(
                variante_id=d.variante_id,
                sucursal_id=reserva.sucursal_id,
                usuario_id=cliente_id,
                tipo=TipoMovimiento.LIBERACION_RESERVA,
                cantidad=d.cantidad,
                nota=f"Cancelación de la reserva #{reserva.id}",
            )
        )

    reserva.estado = EstadoReserva.CANCELADA
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    # CU35 — Avisar al Cliente de la cancelación.
    from app.modules.traspasos.notificacion_service import crear_notificacion_segura

    crear_notificacion_segura(
        session=session,
        usuario_id=cliente_id,
        mensaje=f"Tu reserva #{reserva.id} fue cancelada.",
        tipo="warning",
        modulo="reservas",
    )
    return _reserva_out(session, reserva)


# --------------------------------------------------------------------------- #
#  CU18/CU19 — Vista de la sucursal: notificar y recepcionar
# --------------------------------------------------------------------------- #
def _reserva_sucursal_out(session: Session, r: Reserva) -> dict:
    out = _reserva_out(session, r)
    cliente = session.get(Usuario, r.cliente_id)
    out["cliente_id"] = r.cliente_id
    out["cliente"] = f"{cliente.nombre} {cliente.apellido}" if cliente else None
    out["cliente_telefono"] = cliente.telefono if cliente else None
    return out


def _reserva_de_sucursal(
    session: Session,
    reserva_id: int,
    sucursal_permitida: int | None,
    empresa_id: int | None = None,
) -> Reserva:
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Reserva no encontrada")
    if sucursal_permitida is not None and reserva.sucursal_id != sucursal_permitida:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Esta reserva es de otra sucursal"
        )
    if empresa_id is not None:
        sucursal = session.get(Sucursal, reserva.sucursal_id)
        if sucursal is None or sucursal.empresa_id != empresa_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Esta reserva pertenece a otra empresa"
            )
    return reserva


def listar_por_sucursal(
    session: Session,
    *,
    sucursal_id: int | None = None,
    estado: str | None = None,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    filtros = []
    if empresa_id is not None:
        filtros.append(
            Reserva.sucursal_id.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    if sucursal_permitida is not None:
        filtros.append(Reserva.sucursal_id == sucursal_permitida)
    elif sucursal_id is not None:
        filtros.append(Reserva.sucursal_id == sucursal_id)
    if estado is not None:
        filtros.append(Reserva.estado == estado)

    items, total = paginate(
        session,
        Reserva,
        filters=filtros,
        order_by=(Reserva.fecha, Reserva.hora_inicio),
        page=page,
        size=size,
    )
    return [_reserva_sucursal_out(session, r) for r in items], total


def notificar_reserva(
    session: Session,
    reserva_id: int,
    sucursal_permitida: int | None = None,
    *,
    empresa_id: int | None = None,
) -> dict:
    """CU18 — la sucursal toma conocimiento de la reserva y avisa al cliente
    que ya puede pasar a probarse la prenda."""
    reserva = _reserva_de_sucursal(
        session, reserva_id, sucursal_permitida, empresa_id
    )
    if reserva.estado != EstadoReserva.PENDIENTE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Solo se pueden notificar reservas pendientes"
        )
    reserva.estado = EstadoReserva.NOTIFICADA
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    return _reserva_sucursal_out(session, reserva)


def recepcionar_reserva(
    session: Session,
    reserva_id: int,
    sucursal_permitida: int | None = None,
    *,
    empresa_id: int | None = None,
) -> dict:
    """CU19 — el cliente llegó a la sucursal y se le entregó la prenda para
    probarse; queda "atendida" a la espera de la compra (CU21+)."""
    reserva = _reserva_de_sucursal(
        session, reserva_id, sucursal_permitida, empresa_id
    )
    if reserva.estado != EstadoReserva.NOTIFICADA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo se pueden recepcionar reservas ya notificadas",
        )
    reserva.estado = EstadoReserva.ATENDIDA
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    return _reserva_sucursal_out(session, reserva)


def finalizar_reserva(
    session: Session,
    encargado: Usuario,
    reserva_id: int,
    data: FinalizarReservaIn,
    sucursal_permitida: int | None = None,
    *,
    empresa_id: int | None = None,
) -> dict:
    """El cliente ya se probó las prendas: el Encargado indica cuántas unidades
    de cada una se lleva. Lo que devuelve vuelve al stock disponible; lo que se
    lleva genera una venta pendiente que el Cajero cobra en caja.

    CU17 — si el cliente quiere otra talla/color de la misma prenda, el
    Encargado puede indicar una `variante_sustituta_id`: se libera entera la
    variante reservada (vuelve a disponible) y se descuenta la sustituta en
    su lugar, con el precio de ESA variante (si cuesta más, el total de la
    venta pendiente ya sale recalculado; como la venta todavía no se cobró,
    no hace falta ningún reembolso acá)."""
    reserva = _reserva_de_sucursal(
        session, reserva_id, sucursal_permitida, empresa_id
    )
    if reserva.estado != EstadoReserva.ATENDIDA:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo se pueden finalizar reservas ya recepcionadas",
        )

    detalles = session.exec(
        select(ReservaDetalle).where(ReservaDetalle.reserva_id == reserva.id)
    ).all()
    por_id = {d.id: d for d in detalles}
    items_por_id = {i.detalle_id: i for i in data.items}
    if len(items_por_id) != len(data.items) or set(items_por_id) != set(por_id):
        raise HTTPException(422, "Indicá qué pasa con cada prenda de la reserva")
    for detalle_id, item in items_por_id.items():
        if item.cantidad_llevada > por_id[detalle_id].cantidad:
            raise HTTPException(422, "No se puede llevar más de lo que se reservó")
        if item.variante_sustituta_id is not None and item.cantidad_llevada == 0:
            raise HTTPException(422, "Si no se lleva nada, no corresponde elegir otra talla")

    inventarios: dict[int, Inventario | None] = {}
    # Variante/inventario/precio EFECTIVOS que se venden por cada detalle:
    # puede ser la reservada original, o la sustituta elegida (CU17).
    variantes_venta: dict[int, ProductoVariante] = {}
    inventarios_venta: dict[int, Inventario] = {}
    precios: dict[int, tuple[Decimal, Decimal, int | None]] = {}
    es_sustitucion: dict[int, bool] = {}

    for d in detalles:
        inventarios[d.id] = session.exec(
            select(Inventario).where(
                Inventario.variante_id == d.variante_id,
                Inventario.sucursal_id == reserva.sucursal_id,
            )
        ).first()

        item = items_por_id[d.id]
        if item.cantidad_llevada == 0:
            continue

        sustituta_id = item.variante_sustituta_id
        es_sustitucion[d.id] = sustituta_id is not None and sustituta_id != d.variante_id
        if es_sustitucion[d.id]:
            variante_original = session.get(ProductoVariante, d.variante_id)
            variante_venta = session.get(ProductoVariante, sustituta_id)
            if variante_venta is None:
                raise HTTPException(422, "La talla/color elegido ya no existe")
            if (
                variante_original is None
                or variante_venta.producto_id != variante_original.producto_id
            ):
                raise HTTPException(
                    422, "Solo se puede cambiar de talla o color de la misma prenda"
                )
            inv_venta = session.exec(
                select(Inventario)
                .where(
                    Inventario.variante_id == variante_venta.id,
                    Inventario.sucursal_id == reserva.sucursal_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if inv_venta is None or inv_venta.cantidad_disponible < item.cantidad_llevada:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "No hay stock de esa talla/color en esta sucursal",
                )
        else:
            variante_venta = session.get(ProductoVariante, d.variante_id)
            inv_venta = inventarios[d.id]

        producto_venta = (
            session.get(Producto, variante_venta.producto_id) if variante_venta else None
        )
        precio_disponible = (
            variante_venta.precio if variante_venta and variante_venta.precio is not None
            else (producto_venta.precio_base if producto_venta else None)
        )
        if precio_disponible is None:
            raise HTTPException(
                422, "Una de las prendas que se lleva no tiene precio de venta"
            )

        variantes_venta[d.id] = variante_venta
        inventarios_venta[d.id] = inv_venta
        precios[d.id] = ventas_service._precio_venta(session, variante_venta, producto_venta)

    llevadas = {did: items_por_id[did].cantidad_llevada for did in por_id}
    unidades_llevadas = sum(llevadas.values())
    unidades_devueltas = sum(d.cantidad for d in detalles) - unidades_llevadas

    venta: Venta | None = None
    if unidades_llevadas > 0:
        total = sum(precios[did][1] * llevadas[did] for did in precios)
        venta = Venta(
            cliente_id=reserva.cliente_id,
            sucursal_id=reserva.sucursal_id,
            reserva_id=reserva.id,
            total=total,
        )
        session.add(venta)
        session.flush()  # necesitamos venta.id para el detalle

    for d in detalles:
        llevada = llevadas[d.id]
        sustitucion = es_sustitucion.get(d.id, False)
        # Lo reservado de la variante ORIGINAL se libera entero si hubo
        # sustitución (no se vende nada de lo reservado); si no, se libera
        # solo lo que no se llevó.
        devuelta_original = d.cantidad if sustitucion else (d.cantidad - llevada)
        inv_original = inventarios[d.id]
        d.cantidad_llevada = llevada
        session.add(d)
        if inv_original is not None:
            # Al reservar, todo pasó de disponible a reservado. Ahora sale de
            # "reservado": lo devuelto vuelve a disponible, lo llevado se vende.
            inv_original.cantidad_reservada = max(
                0, inv_original.cantidad_reservada - d.cantidad
            )
            inv_original.cantidad_disponible += devuelta_original
            session.add(inv_original)
        if devuelta_original > 0:
            session.add(
                MovimientoInventario(
                    variante_id=d.variante_id,
                    sucursal_id=reserva.sucursal_id,
                    usuario_id=encargado.id,
                    tipo=TipoMovimiento.LIBERACION_RESERVA,
                    cantidad=devuelta_original,
                    nota=f"Devolución al finalizar la reserva #{reserva.id}",
                )
            )
        if llevada > 0 and venta is not None:
            variante_venta = variantes_venta[d.id]
            inv_venta = inventarios_venta[d.id]
            original, precio, promocion_id = precios[d.id]
            session.add(
                VentaDetalle(
                    venta_id=venta.id,
                    variante_id=variante_venta.id,
                    sucursal_id=reserva.sucursal_id,
                    cantidad=llevada,
                    precio_unitario=precio,
                    precio_original=original,
                    promocion_id=promocion_id,
                    costo_unitario=inv_venta.costo_promedio if inv_venta else None,
                )
            )
            if sustitucion:
                # Esta sí era stock disponible normal (nunca estuvo reservada).
                inv_venta.cantidad_disponible -= llevada
                session.add(inv_venta)
            session.add(
                MovimientoInventario(
                    variante_id=variante_venta.id,
                    sucursal_id=reserva.sucursal_id,
                    usuario_id=encargado.id,
                    tipo=TipoMovimiento.SALIDA_VENTA,
                    cantidad=llevada,
                    nota=f"Venta #{venta.id} de la reserva #{reserva.id}"
                    + (" (cambio de talla/color al recoger)" if sustitucion else ""),
                )
            )

    reserva.estado = EstadoReserva.COMPLETADA
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    return {
        "reserva": _reserva_sucursal_out(session, reserva),
        "venta_id": venta.id if venta else None,
        "unidades_llevadas": unidades_llevadas,
        "unidades_devueltas": unidades_devueltas,
        "total": venta.total if venta else Decimal(0),
    }
