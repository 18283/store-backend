"""Lógica de negocio del módulo Reportes — CU30/CU31.

Reportes calculados directamente de la base de datos (sin IA): ventas
(ingresos, costo, ganancia) e inventario (stock y valor). Cada generación
queda registrada en `reporte_generado`. CU30 agrega la exportación binaria
de esos reportes a Excel (.xlsx) o PDF.
"""

import io
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from app.modules.catalogo.models import Color, Talla
from app.modules.ia.models import FormatoSolicitud, ReporteGenerado, TipoReporte
from app.modules.inventario.models import Inventario
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.sucursales.models import Sucursal
from app.modules.ventas.models import EstadoPago, EstadoVenta, Pago, Venta, VentaDetalle

TipoExportacion = Literal["ventas", "inventario"]
FormatoExportacion = Literal["xlsx", "pdf"]

MEDIA_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIA_TYPE_PDF = "application/pdf"

ESTADOS_PAGADOS = (EstadoVenta.PAGADA, EstadoVenta.COMPLETADA)
_BOLIVIA = timezone(timedelta(hours=-4))
_LIMITE_INVENTARIO = 1000
_CENTAVOS = Decimal("0.01")


def _dinero(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _sucursal_o_error(
    session: Session, sucursal_id: int | None, empresa_id: int | None = None
) -> Sucursal | None:
    if sucursal_id is None:
        return None
    sucursal = session.get(Sucursal, sucursal_id)
    if sucursal is None or (
        empresa_id is not None and sucursal.empresa_id != empresa_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sucursal no encontrada")
    return sucursal


def _registrar(
    session: Session,
    usuario_id: int,
    tipo: str,
    parametros: dict,
    formato: str = FormatoSolicitud.FORMULARIO,
) -> None:
    session.add(
        ReporteGenerado(
            usuario_id=usuario_id,
            tipo_reporte=tipo,
            formato_solicitud=formato,
            parametros=parametros,
        )
    )
    session.commit()


# --------------------------------------------------------------------------- #
#  Reporte de ventas
# --------------------------------------------------------------------------- #
def reporte_ventas(
    session: Session,
    admin_id: int,
    fecha_desde: date | None,
    fecha_hasta: date | None,
    sucursal_id: int | None,
    *,
    empresa_id: int | None = None,
) -> dict:
    hoy = datetime.now(_BOLIVIA).date()
    hasta = fecha_hasta or hoy
    desde = fecha_desde or (hasta - timedelta(days=29))
    if hasta < desde:
        raise HTTPException(422, "La fecha final no puede ser anterior a la inicial")
    sucursal = _sucursal_o_error(session, sucursal_id, empresa_id)

    # Los días se miden en hora de Bolivia; en la base las fechas están en UTC.
    desde_dt = datetime.combine(desde, time.min, tzinfo=_BOLIVIA).astimezone(timezone.utc)
    hasta_dt = datetime.combine(hasta, time.max, tzinfo=_BOLIVIA).astimezone(timezone.utc)

    # Cada línea cuenta para la sucursal de la que salió (una compra en línea
    # puede salir de varias).
    sucursal_linea = func.coalesce(VentaDetalle.sucursal_id, Venta.sucursal_id)
    consulta = (
        select(
            VentaDetalle.venta_id,
            sucursal_linea,
            VentaDetalle.cantidad,
            VentaDetalle.precio_unitario,
            VentaDetalle.precio_original,
            VentaDetalle.costo_unitario,
            Producto.nombre,
        )
        .select_from(VentaDetalle)
        .join(Venta, Venta.id == VentaDetalle.venta_id)
        .join(ProductoVariante, ProductoVariante.id == VentaDetalle.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .where(
            Venta.estado.in_(ESTADOS_PAGADOS),
            Venta.fecha_creacion >= desde_dt,
            Venta.fecha_creacion <= hasta_dt,
        )
    )
    if sucursal_id is not None:
        consulta = consulta.where(sucursal_linea == sucursal_id)
    if empresa_id is not None:
        consulta = consulta.where(
            sucursal_linea.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    filas = session.exec(consulta).all()

    sucursales_query = select(Sucursal)
    if empresa_id is not None:
        sucursales_query = sucursales_query.where(
            Sucursal.empresa_id == empresa_id
        )
    nombres_sucursal = {
        s.id: s.nombre for s in session.exec(sucursales_query).all()
    }

    ingresos = costo = ingresos_con_costo = descuentos = Decimal(0)
    unidades = lineas_sin_costo = 0
    ventas: set[int] = set()
    por_sucursal: dict[int, dict] = {}
    por_producto: dict[str, dict] = {}

    for venta_id, suc_id, cantidad, precio, original, costo_unit, producto in filas:
        subtotal = precio * cantidad
        ingresos += subtotal
        unidades += cantidad
        ventas.add(venta_id)
        if original is not None:
            descuentos += (original - precio) * cantidad
        if costo_unit is None:
            lineas_sin_costo += 1
        else:
            costo += costo_unit * cantidad
            ingresos_con_costo += subtotal

        grupo = por_sucursal.setdefault(suc_id, {"ventas": set(), "ingresos": Decimal(0)})
        grupo["ventas"].add(venta_id)
        grupo["ingresos"] += subtotal
        prod = por_producto.setdefault(producto, {"unidades": 0, "ingresos": Decimal(0)})
        prod["unidades"] += cantidad
        prod["ingresos"] += subtotal

    # CU14: egresos por ventas offline cobradas y después reembolsadas por
    # falta de stock al sincronizar (no tienen VentaDetalle, así que no
    # aparecen arriba como ingreso).
    reembolsos_query = (
        select(Pago.monto, Venta.sucursal_id)
        .select_from(Pago)
        .join(Venta, Venta.id == Pago.venta_id)
        .where(
            Pago.estado == EstadoPago.REEMBOLSADO,
            Pago.fecha_reembolso >= desde_dt,
            Pago.fecha_reembolso <= hasta_dt,
        )
    )
    if sucursal_id is not None:
        reembolsos_query = reembolsos_query.where(Venta.sucursal_id == sucursal_id)
    if empresa_id is not None:
        reembolsos_query = reembolsos_query.where(
            Venta.sucursal_id.in_(
                select(Sucursal.id).where(Sucursal.empresa_id == empresa_id)
            )
        )
    filas_reembolso = session.exec(reembolsos_query).all()
    egresos_reembolsos = sum((monto for monto, _ in filas_reembolso), Decimal(0))

    cantidad_ventas = len(ventas)
    resultado = {
        "fecha_desde": desde,
        "fecha_hasta": hasta,
        "sucursal": sucursal.nombre if sucursal else "Todas las sucursales",
        "cantidad_ventas": cantidad_ventas,
        "unidades": unidades,
        "ingresos": _dinero(ingresos),
        "costo": _dinero(costo),
        "ganancia": _dinero(ingresos_con_costo - costo),
        "descuentos": _dinero(descuentos),
        "ticket_promedio": _dinero(ingresos / cantidad_ventas) if cantidad_ventas else Decimal("0.00"),
        "lineas_sin_costo": lineas_sin_costo,
        "egresos_reembolsos": _dinero(egresos_reembolsos),
        "cantidad_reembolsos": len(filas_reembolso),
        "por_sucursal": sorted(
            (
                {
                    "sucursal": nombres_sucursal.get(suc_id, f"Sucursal {suc_id}"),
                    "cantidad_ventas": len(datos["ventas"]),
                    "ingresos": _dinero(datos["ingresos"]),
                }
                for suc_id, datos in por_sucursal.items()
            ),
            key=lambda x: x["ingresos"],
            reverse=True,
        ),
        "top_productos": sorted(
            (
                {
                    "nombre": nombre,
                    "unidades": datos["unidades"],
                    "ingresos": _dinero(datos["ingresos"]),
                }
                for nombre, datos in por_producto.items()
            ),
            key=lambda x: (x["unidades"], x["ingresos"]),
            reverse=True,
        )[:5],
    }
    _registrar(
        session,
        admin_id,
        TipoReporte.VENTAS,
        {
            "fecha_desde": desde.isoformat(),
            "fecha_hasta": hasta.isoformat(),
            "sucursal": sucursal.nombre if sucursal else None,
        },
    )
    return resultado


# --------------------------------------------------------------------------- #
#  Reporte de inventario
# --------------------------------------------------------------------------- #
def reporte_inventario(
    session: Session,
    admin_id: int,
    sucursal_id: int | None,
    umbral: int,
    *,
    empresa_id: int | None = None,
) -> dict:
    if umbral < 0:
        raise HTTPException(422, "El umbral de stock bajo no puede ser negativo")
    sucursal = _sucursal_o_error(session, sucursal_id, empresa_id)

    consulta = (
        select(
            Producto.nombre,
            Talla.valor,
            Color.nombre,
            Sucursal.nombre,
            Inventario.cantidad_disponible,
            Inventario.cantidad_reservada,
            Inventario.costo_promedio,
        )
        .select_from(Inventario)
        .join(ProductoVariante, ProductoVariante.id == Inventario.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .join(Talla, Talla.id == ProductoVariante.talla_id)
        .join(Color, Color.id == ProductoVariante.color_id)
        .join(Sucursal, Sucursal.id == Inventario.sucursal_id)
        .where(Sucursal.activa == True)  # noqa: E712
        .order_by(Producto.nombre, Talla.valor, Sucursal.nombre)
        .limit(_LIMITE_INVENTARIO)
    )
    if sucursal_id is not None:
        consulta = consulta.where(Inventario.sucursal_id == sucursal_id)
    if empresa_id is not None:
        consulta = consulta.where(Sucursal.empresa_id == empresa_id)

    items = []
    disponibles = reservadas = bajos = 0
    valor_total = Decimal(0)
    for producto, talla, color, suc, disponible, reservada, costo in session.exec(consulta).all():
        valor = disponible * costo if costo is not None else None
        if valor is not None:
            valor_total += valor
        bajo = disponible <= umbral
        bajos += 1 if bajo else 0
        disponibles += disponible
        reservadas += reservada
        items.append(
            {
                "producto": producto,
                "talla": talla,
                "color": color,
                "sucursal": suc,
                "disponible": disponible,
                "reservada": reservada,
                "costo_promedio": costo,
                "valor": _dinero(valor) if valor is not None else None,
                "stock_bajo": bajo,
            }
        )

    resultado = {
        "sucursal": sucursal.nombre if sucursal else "Todas las sucursales",
        "umbral": umbral,
        "total_variantes": len(items),
        "unidades_disponibles": disponibles,
        "unidades_reservadas": reservadas,
        "valor_inventario": _dinero(valor_total),
        "variantes_stock_bajo": bajos,
        "items": items,
    }
    _registrar(
        session,
        admin_id,
        TipoReporte.INVENTARIO,
        {"sucursal": sucursal.nombre if sucursal else None, "umbral": umbral},
    )
    return resultado


# --------------------------------------------------------------------------- #
#  CU30 — Exportación dinámica a Excel o PDF
# --------------------------------------------------------------------------- #
def _texto(valor: object) -> str:
    if valor is None:
        return ""
    if isinstance(valor, Decimal):
        return f"{valor:.2f}"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    return str(valor)


def _encabezado_ventas(datos: dict) -> list[str]:
    return [
        "Reporte de Ventas",
        f"Sucursal: {datos.get('sucursal')}",
        f"Periodo: {datos.get('fecha_desde')} al {datos.get('fecha_hasta')}",
        f"Generado: {datetime.now(_BOLIVIA).strftime('%Y-%m-%d %H:%M')}",
    ]


def _encabezado_inventario(datos: dict) -> list[str]:
    return [
        "Reporte de Inventario",
        f"Sucursal: {datos.get('sucursal')}",
        f"Umbral stock bajo: {datos.get('umbral')}",
        f"Generado: {datetime.now(_BOLIVIA).strftime('%Y-%m-%d %H:%M')}",
    ]


def _filas_ventas(datos: dict) -> tuple[list[str], list[list[str]]]:
    header = [
        "Sucursal", "Cantidad ventas", "Ingresos",
        "Producto", "Unidades", "Ingresos producto",
    ]
    filas: list[list[str]] = []
    por_sucursal = datos.get("por_sucursal") or []
    top = datos.get("top_productos") or []
    max_filas = max(len(por_sucursal), len(top))
    if max_filas == 0:
        filas.append(["Sin resultados para los filtros seleccionados", "", "", "", "", ""])
    for i in range(max_filas):
        suc = por_sucursal[i] if i < len(por_sucursal) else {}
        prod = top[i] if i < len(top) else {}
        filas.append([
            _texto(suc.get("sucursal")),
            _texto(suc.get("cantidad_ventas")),
            _texto(suc.get("ingresos")),
            _texto(prod.get("nombre")),
            _texto(prod.get("unidades")),
            _texto(prod.get("ingresos")),
        ])
    filas.append([])
    filas.append(["Resumen", "", "", "", "", ""])
    filas.append(["Cantidad ventas", _texto(datos.get("cantidad_ventas")), "", "", "", ""])
    filas.append(["Unidades", _texto(datos.get("unidades")), "", "", "", ""])
    filas.append(["Ingresos", _texto(datos.get("ingresos")), "", "", "", ""])
    filas.append(["Costo", _texto(datos.get("costo")), "", "", "", ""])
    filas.append(["Ganancia", _texto(datos.get("ganancia")), "", "", "", ""])
    filas.append(["Descuentos", _texto(datos.get("descuentos")), "", "", "", ""])
    filas.append(["Ticket promedio", _texto(datos.get("ticket_promedio")), "", "", "", ""])
    filas.append(["Egresos reembolsos", _texto(datos.get("egresos_reembolsos")), "", "", "", ""])
    return header, filas


def _filas_inventario(datos: dict) -> tuple[list[str], list[list[str]]]:
    header = [
        "Producto", "Talla", "Color", "Sucursal",
        "Disponible", "Reservada", "Costo prom.", "Valor", "Stock bajo",
    ]
    items = datos.get("items") or []
    filas: list[list[str]] = []
    if not items:
        filas.append(["Sin resultados para los filtros seleccionados"] + [""] * 8)
    for item in items:
        filas.append([
            _texto(item.get("producto")),
            _texto(item.get("talla")),
            _texto(item.get("color")),
            _texto(item.get("sucursal")),
            _texto(item.get("disponible")),
            _texto(item.get("reservada")),
            _texto(item.get("costo_promedio")),
            _texto(item.get("valor")),
            _texto(item.get("stock_bajo")),
        ])
    filas.append([])
    filas.append(["Resumen", "", "", "", "", "", "", "", ""])
    filas.append(["Total variantes", _texto(datos.get("total_variantes")), "", "", "", "", "", "", ""])
    filas.append(["Unidades disponibles", _texto(datos.get("unidades_disponibles")), "", "", "", "", "", "", ""])
    filas.append(["Unidades reservadas", _texto(datos.get("unidades_reservadas")), "", "", "", "", "", "", ""])
    filas.append(["Valor inventario", _texto(datos.get("valor_inventario")), "", "", "", "", "", "", ""])
    filas.append(["Variantes stock bajo", _texto(datos.get("variantes_stock_bajo")), "", "", "", "", "", "", ""])
    return header, filas


def _generar_xlsx(encabezado: list[str], header: list[str], filas: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"
    for linea in encabezado:
        ws.append([linea])
    ws.append([])
    ws.append(header)
    for fila in filas:
        ws.append(fila)
    for col in ws.columns:
        max_len = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 40)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _generar_pdf(encabezado: list[str], header: list[str], filas: list[list[str]]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24,
    )
    data = [[linea] for linea in encabezado]
    data.append([])
    data.append(header)
    data.extend(filas)
    # reportlab no acepta filas vacías con distinto largo en todas las versiones
    data = [f if f else [""] * len(header) for f in data]
    table = Table(data, repeatRows=len(encabezado) + 2)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, len(encabezado) + 1), (-1, len(encabezado) + 1), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    doc.build([table])
    return buf.getvalue()


def exportar_reporte(
    session: Session,
    admin_id: int,
    tipo_reporte: str,
    formato: str,
    *,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    sucursal_id: int | None = None,
    umbral: int = 3,
    empresa_id: int | None = None,
) -> tuple[bytes, str, str]:
    """CU30 — Exporta un reporte dinámico (ventas/inventario) a Excel o PDF.

    Reutiliza los cálculos de CU31 (reporte_ventas / reporte_inventario),
    genera el archivo binario y registra la exportación en reporte_generado.

    Retorna (contenido, nombre_archivo, media_type).
    """
    tipo = (tipo_reporte or "").strip().lower()
    fmt = (formato or "").strip().lower()

    # A6 — Error de formato (incluye tipo de reporte)
    if tipo not in ("ventas", "inventario"):
        raise HTTPException(400, "Tipo de reporte inválido. Use 'ventas' o 'inventario'.")
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(400, "Formato inválido. Use 'xlsx' (Excel) o 'pdf'.")

    hoy = datetime.now(_BOLIVIA).date()
    if tipo == "ventas":
        datos = reporte_ventas(session, admin_id, fecha_desde, fecha_hasta, sucursal_id, empresa_id=empresa_id)
        encabezado = _encabezado_ventas(datos)
        header, filas = _filas_ventas(datos)
        nombre_base = f"reporte_ventas_{datos['fecha_desde']}_al_{datos['fecha_hasta']}"
        params = {
            "fecha_desde": datos["fecha_desde"].isoformat(),
            "fecha_hasta": datos["fecha_hasta"].isoformat(),
            "sucursal": datos["sucursal"],
            "formato": fmt,
        }
        tipo_registro = TipoReporte.VENTAS
    else:
        datos = reporte_inventario(session, admin_id, sucursal_id, umbral, empresa_id=empresa_id)
        encabezado = _encabezado_inventario(datos)
        header, filas = _filas_inventario(datos)
        nombre_base = "reporte_inventario"
        params = {"sucursal": datos["sucursal"], "umbral": umbral, "formato": fmt}
        tipo_registro = TipoReporte.INVENTARIO

    if fmt == "xlsx":
        contenido = _generar_xlsx(encabezado, header, filas)
        media_type = MEDIA_TYPE_XLSX
        extension = "xlsx"
    else:
        contenido = _generar_pdf(encabezado, header, filas)
        media_type = MEDIA_TYPE_PDF
        extension = "pdf"

    _registrar(
        session,
        admin_id,
        tipo_registro,
        params,
        formato="XLSX" if fmt == "xlsx" else "PDF",
    )
    nombre_archivo = f"{nombre_base}.{extension}"
    return contenido, nombre_archivo, media_type
