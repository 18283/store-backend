"""Lógica de negocio del módulo Inventario — CU12/CU13/CU14/CU36."""

import math
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, func, or_, select

from app.core.crud import paginate
from app.modules.catalogo.models import Color, Talla
from app.modules.identidad.models import Usuario
from app.modules.inventario.models import Inventario, MovimientoInventario, TipoMovimiento
from app.modules.inventario.schemas import InventarioAjuste, MovimientoInventarioCreate
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.sucursales.models import Sucursal


def _inventario_out(session: Session, inv: Inventario) -> dict:
    variante = session.get(ProductoVariante, inv.variante_id)
    producto = session.get(Producto, variante.producto_id) if variante else None
    talla = session.get(Talla, variante.talla_id) if variante else None
    color = session.get(Color, variante.color_id) if variante else None
    sucursal = session.get(Sucursal, inv.sucursal_id)

    return {
        "id": inv.id,
        "variante_id": inv.variante_id,
        "producto_id": producto.id if producto else None,
        "producto": producto.nombre if producto else None,
        "talla": talla.valor if talla else None,
        "color": color.nombre if color else None,
        "color_hex": color.codigo_hex if color else None,
        "sku": variante.sku if variante else None,
        "sucursal_id": inv.sucursal_id,
        "sucursal": sucursal.nombre if sucursal else None,
        "ciudad": sucursal.ciudad if sucursal else None,
        "cantidad_disponible": inv.cantidad_disponible,
        "cantidad_reservada": inv.cantidad_reservada,
        "costo_promedio": inv.costo_promedio,
        "estado": "DISPONIBLE" if inv.cantidad_disponible > 0 else "AGOTADO",
    }


def list_inventario(
    session: Session,
    *,
    sucursal_id: int | None = None,
    q: str | None = None,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    filtros = []
    if empresa_id is not None:
        filtros.append(
            Inventario.sucursal_id.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    if sucursal_permitida is not None:
        filtros.append(Inventario.sucursal_id == sucursal_permitida)
    elif sucursal_id is not None:
        filtros.append(Inventario.sucursal_id == sucursal_id)
    if q:
        patron = f"%{q.strip().lower()}%"
        subq = (
            select(ProductoVariante.id)
            .join(Producto, ProductoVariante.producto_id == Producto.id)
            .where(
                or_(
                    func.lower(Producto.nombre).like(patron),
                    func.lower(ProductoVariante.sku).like(patron),
                )
            )
        )
        filtros.append(Inventario.variante_id.in_(subq))

    items, total = paginate(
        session,
        Inventario,
        filters=filtros,
        order_by=Inventario.id.desc(),
        page=page,
        size=size,
    )
    return [_inventario_out(session, i) for i in items], total


def ajustar_stock(
    session: Session,
    data: InventarioAjuste,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> dict:
    if session.get(ProductoVariante, data.variante_id) is None:
        raise HTTPException(422, "La variante no existe")
    sucursal = session.get(Sucursal, data.sucursal_id)
    if sucursal is None or (
        empresa_id is not None and sucursal.empresa_id != empresa_id
    ):
        raise HTTPException(422, "La sucursal no existe")
    if sucursal_permitida is not None and data.sucursal_id != sucursal_permitida:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo podés ajustar el stock de tu propia sucursal",
        )

    inv = session.exec(
        select(Inventario).where(
            Inventario.variante_id == data.variante_id,
            Inventario.sucursal_id == data.sucursal_id,
        )
    ).first()
    if inv is None:
        inv = Inventario(
            variante_id=data.variante_id,
            sucursal_id=data.sucursal_id,
            cantidad_disponible=data.cantidad_disponible,
        )
    else:
        inv.cantidad_disponible = data.cantidad_disponible

    session.add(inv)
    session.commit()
    session.refresh(inv)
    return _inventario_out(session, inv)


def disponibilidad_variante(
    session: Session, variante_id: int, *, empresa_id: int
) -> list[dict]:
    """CU12 — sucursales con stock de una variante concreta."""
    filas = session.exec(
        select(Inventario, Sucursal)
        .join(Sucursal, Inventario.sucursal_id == Sucursal.id)  # type: ignore[arg-type]
        .where(
            Inventario.variante_id == variante_id,
            Inventario.cantidad_disponible > 0,
            Sucursal.activa == True,  # noqa: E712
            Sucursal.empresa_id == empresa_id,
        )
    ).all()
    return [
        {
            "sucursal_id": suc.id,
            "sucursal": suc.nombre,
            "ciudad": suc.ciudad,
            "cantidad_disponible": inv.cantidad_disponible,
        }
        for inv, suc in filas
    ]


# --------------------------------------------------------------------------- #
#  CU14 — Movimientos de inventario
# --------------------------------------------------------------------------- #
def _movimiento_out(session: Session, m: MovimientoInventario) -> dict:
    variante = session.get(ProductoVariante, m.variante_id)
    producto = session.get(Producto, variante.producto_id) if variante else None
    talla = session.get(Talla, variante.talla_id) if variante else None
    color = session.get(Color, variante.color_id) if variante else None
    sucursal = session.get(Sucursal, m.sucursal_id)
    usuario = session.get(Usuario, m.usuario_id)

    return {
        "id": m.id,
        "variante_id": m.variante_id,
        "producto_id": producto.id if producto else None,
        "producto": producto.nombre if producto else None,
        "talla": talla.valor if talla else None,
        "color": color.nombre if color else None,
        "sku": variante.sku if variante else None,
        "sucursal_id": m.sucursal_id,
        "sucursal": sucursal.nombre if sucursal else None,
        "ciudad": sucursal.ciudad if sucursal else None,
        "tipo": m.tipo,
        "cantidad": m.cantidad,
        "costo_unitario": m.costo_unitario,
        "nota": m.nota,
        "usuario_id": m.usuario_id,
        "usuario": f"{usuario.nombre} {usuario.apellido}" if usuario else None,
        "fecha": m.fecha,
    }


def registrar_ingreso(
    session: Session,
    data: MovimientoInventarioCreate,
    usuario_id: int,
    *,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
) -> dict:
    """CU14: registra la recepción de mercadería del proveedor.

    Suma la cantidad al stock actual (CU13/CU15) y recalcula el costo
    promedio ponderado de esa variante+sucursal.
    """
    if session.get(ProductoVariante, data.variante_id) is None:
        raise HTTPException(422, "La variante no existe")
    sucursal = session.get(Sucursal, data.sucursal_id)
    if sucursal is None or (
        empresa_id is not None and sucursal.empresa_id != empresa_id
    ):
        raise HTTPException(422, "La sucursal no existe")
    if sucursal_permitida is not None and data.sucursal_id != sucursal_permitida:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo podés registrar movimientos de tu propia sucursal",
        )

    inv = session.exec(
        select(Inventario).where(
            Inventario.variante_id == data.variante_id,
            Inventario.sucursal_id == data.sucursal_id,
        )
    ).first()
    if inv is None:
        inv = Inventario(
            variante_id=data.variante_id,
            sucursal_id=data.sucursal_id,
            cantidad_disponible=0,
        )

    stock_previo = inv.cantidad_disponible
    costo_previo = inv.costo_promedio if inv.costo_promedio is not None else data.costo_unitario
    nuevo_total = stock_previo + data.cantidad
    promedio = (stock_previo * costo_previo + data.cantidad * data.costo_unitario) / nuevo_total

    inv.costo_promedio = promedio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    inv.cantidad_disponible = nuevo_total

    session.add(inv)
    session.flush()  # que el UPDATE de inventario quede antes del INSERT del movimiento

    mov = MovimientoInventario(
        variante_id=data.variante_id,
        sucursal_id=data.sucursal_id,
        usuario_id=usuario_id,
        tipo=TipoMovimiento.INGRESO,
        cantidad=data.cantidad,
        costo_unitario=data.costo_unitario,
        nota=data.nota,
    )
    session.add(mov)
    session.commit()
    session.refresh(mov)
    return _movimiento_out(session, mov)


def list_movimientos(
    session: Session,
    *,
    sucursal_id: int | None = None,
    variante_id: int | None = None,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict], int]:
    filtros = []
    if empresa_id is not None:
        filtros.append(
            MovimientoInventario.sucursal_id.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    if sucursal_permitida is not None:
        filtros.append(MovimientoInventario.sucursal_id == sucursal_permitida)
    elif sucursal_id is not None:
        filtros.append(MovimientoInventario.sucursal_id == sucursal_id)
    if variante_id is not None:
        filtros.append(MovimientoInventario.variante_id == variante_id)

    items, total = paginate(
        session,
        MovimientoInventario,
        filters=filtros,
        order_by=MovimientoInventario.fecha.desc(),
        page=page,
        size=size,
    )
    return [_movimiento_out(session, m) for m in items], total


# --------------------------------------------------------------------------- #
#  CU36 — Alertas y sugerencias de reposición (inventario inteligente)
# --------------------------------------------------------------------------- #
ESTADOS_PAGADOS_ALERTAS = ("PAGADA", "COMPLETADA")


def _rotacion_por_variante_sucursal(
    session: Session,
    sucursal_efectiva_id: int | None,
    empresa_id: int | None,
    desde_dt: datetime,
    hasta_dt: datetime,
) -> dict[tuple[int, int], int]:
    """Suma vendida por (variante_id, sucursal_id) en ventas pagadas del período."""
    try:
        from app.modules.ventas.models import EstadoVenta, Venta, VentaDetalle

        sucursal_linea = func.coalesce(VentaDetalle.sucursal_id, Venta.sucursal_id)
        consulta = (
            select(VentaDetalle.variante_id, sucursal_linea, func.sum(VentaDetalle.cantidad))
            .select_from(VentaDetalle)
            .join(Venta, Venta.id == VentaDetalle.venta_id)
            .where(
                Venta.estado.in_((EstadoVenta.PAGADA, EstadoVenta.COMPLETADA)),
                Venta.fecha_creacion >= desde_dt,
                Venta.fecha_creacion <= hasta_dt,
            )
            .group_by(VentaDetalle.variante_id, sucursal_linea)
        )
        if sucursal_efectiva_id is not None:
            consulta = consulta.where(sucursal_linea == sucursal_efectiva_id)
        if empresa_id is not None:
            consulta = consulta.where(
                sucursal_linea.in_(
                    select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
                )
            )
        return {
            (int(variante_id), int(suc_id)): int(total or 0)
            for variante_id, suc_id, total in session.exec(consulta).all()
            if suc_id is not None
        }
    except Exception:  # noqa: BLE001 - sin tablas de ventas (tests mínimos) no hay predicción
        session.rollback()
        return {}


def consultar_alertas(
    session: Session,
    *,
    sucursal_id: int | None = None,
    umbral: int = 3,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    horizonte_dias: int = 30,
    q: str | None = None,
    sucursal_permitida: int | None = None,
    empresa_id: int | None = None,
    page: int = 1,
    size: int = 20,
    hoy: date | None = None,
) -> dict:
    """CU36 — Alertas de stock bajo + sugerencias de reposición (solo lectura).

    - Encargado (`sucursal_permitida`): forzado a su sucursal.
    - Admin: `sucursal_id` opcional (None = todas las sucursales de su empresa).
    - Alerta: `cantidad_disponible <= umbral` (`AGOTADO` si es 0).
    - Rotación: unidades vendidas en el período; demanda diaria = rotación/días.
    - Sugerencia: ceil(demanda_diaria * horizonte_dias) - disponible (si hay demanda).
    """
    if umbral < 0:
        raise HTTPException(422, "El umbral de stock bajo no puede ser negativo")
    if horizonte_dias < 1:
        raise HTTPException(422, "El horizonte de reposición debe ser mayor que cero")

    hoy = hoy or date.today()
    hasta = fecha_hasta or hoy
    desde = fecha_desde or (hoy - timedelta(days=29))
    if hasta < desde:
        raise HTTPException(422, "La fecha final no puede ser anterior a la inicial")
    dias_periodo = max((hasta - desde).days + 1, 1)
    desde_dt = datetime.combine(desde, time.min, tzinfo=timezone.utc)
    hasta_dt = datetime.combine(hasta, time.max, tzinfo=timezone.utc)

    # Sucursal efectiva: Encargado siempre la suya; Admin puede elegir o todas.
    sucursal_efectiva_id = sucursal_permitida if sucursal_permitida is not None else sucursal_id
    if sucursal_efectiva_id is not None:
        sucursal = session.get(Sucursal, sucursal_efectiva_id)
        if (
            sucursal is None
            or not sucursal.activa
            or (empresa_id is not None and sucursal.empresa_id != empresa_id)
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sucursal no encontrada")

    # Existencias con alerta: solo disponible <= umbral (A1: total=0 si no hay).
    consulta = (
        select(Inventario, ProductoVariante, Producto, Talla, Color, Sucursal)
        .join(ProductoVariante, ProductoVariante.id == Inventario.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .join(Talla, Talla.id == ProductoVariante.talla_id)
        .join(Color, Color.id == ProductoVariante.color_id)
        .join(Sucursal, Sucursal.id == Inventario.sucursal_id)
        .where(
            Inventario.cantidad_disponible <= umbral,
            Sucursal.activa == True,  # noqa: E712
        )
        .order_by(Inventario.cantidad_disponible.asc(), Producto.nombre.asc())
    )
    if sucursal_efectiva_id is not None:
        consulta = consulta.where(Inventario.sucursal_id == sucursal_efectiva_id)
    if empresa_id is not None:
        consulta = consulta.where(Sucursal.empresa_id == empresa_id)
    if q and q.strip():
        patron = f"%{q.strip().lower()}%"
        consulta = consulta.where(
            or_(
                func.lower(Producto.nombre).like(patron),
                func.lower(ProductoVariante.sku).like(patron),
            )
        )

    filas = session.exec(consulta).all()
    rotacion = _rotacion_por_variante_sucursal(
        session, sucursal_efectiva_id, empresa_id, desde_dt, hasta_dt
    )

    items: list[dict] = []
    sin_demanda = False
    for inv, variante, producto, talla, color, sucursal in filas:
        vendido = rotacion.get((inv.variante_id, inv.sucursal_id), 0)
        disponible = inv.cantidad_disponible
        if vendido > 0:
            demanda_diaria: float | None = vendido / dias_periodo
            dias_restantes: float | None = (
                round(disponible / demanda_diaria, 2) if demanda_diaria else None
            )
            cantidad_a_reponer: int | None = max(
                0, math.ceil(demanda_diaria * horizonte_dias) - disponible
            )
            sugerencia = f"Reponer {cantidad_a_reponer} unidades"
        else:
            sin_demanda = True
            demanda_diaria = None
            dias_restantes = None
            cantidad_a_reponer = None
            sugerencia = "Sin datos de demanda"
        items.append(
            {
                "variante_id": inv.variante_id,
                "producto_id": producto.id if producto else None,
                "producto": producto.nombre if producto else None,
                "talla": talla.valor if talla else None,
                "color": color.nombre if color else None,
                "color_hex": color.codigo_hex if color else None,
                "sku": variante.sku if variante else None,
                "sucursal_id": inv.sucursal_id,
                "sucursal": sucursal.nombre if sucursal else None,
                "ciudad": sucursal.ciudad if sucursal else None,
                "cantidad_disponible": disponible,
                "cantidad_reservada": inv.cantidad_reservada,
                "estado": "AGOTADO" if disponible == 0 else "STOCK_BAJO",
                "rotacion_periodo": vendido,
                "demanda_diaria_estimada": round(demanda_diaria, 4) if demanda_diaria is not None else None,
                "dias_stock_restantes": dias_restantes,
                "cantidad_a_reponer": cantidad_a_reponer,
                "sugerencia": sugerencia,
            }
        )

    # Prioridad: agotados primero, luego menor días restantes, luego menor stock.
    def _prioridad(item: dict) -> tuple:
        dias = item["dias_stock_restantes"]
        return (
            0 if item["estado"] == "AGOTADO" else 1,
            dias is None,
            dias if dias is not None else float("inf"),
            item["cantidad_disponible"],
        )

    items.sort(key=_prioridad)
    total = len(items)
    offset = (page - 1) * size
    pagina = items[offset : offset + size]

    return {
        "items": pagina,
        "total": total,
        "page": page,
        "size": size,
        "umbral": umbral,
        "fecha_desde": desde.isoformat(),
        "fecha_hasta": hasta.isoformat(),
        "horizonte_dias": horizonte_dias,
        "agotados": sum(1 for i in items if i["estado"] == "AGOTADO"),
        "stock_bajo": sum(1 for i in items if i["estado"] == "STOCK_BAJO"),
        "sin_datos_demanda": sin_demanda and total > 0,
    }
