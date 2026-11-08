"""Lógica de negocio del módulo Analítica inteligente.

CU31 (dashboard analítico), CU32 (predicción de demanda, regresión lineal),
CU33 (agrupaciones y patrones de productos, K-Means) y CU34 (entrenar,
evaluar y actualizar los dos modelos anteriores). Usa scikit-learn.

Multitenencia: CU31/CU32/CU33 siempre calculan sobre los datos de UNA sola
empresa (la del administrador que consulta) — nunca mezclan empresas. CU34
es la excepción a propósito: entrena/evalúa con datos de toda la plataforma
porque lo que calibra son los hiperparámetros del algoritmo (cuántas
semanas de historial pedir, cuántos grupos usar), no una predicción
concreta para una empresa.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from fastapi import HTTPException
from sklearn.cluster import KMeans
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlmodel import Session, func, select

from app.modules.identidad.models import Usuario
from app.modules.inventario.models import Inventario
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.ventas.models import EstadoVenta, Venta, VentaDetalle

from .models import ModeloAnalitico, TipoModeloAnalitico
from .schemas import (
    AgrupacionesOut,
    DashboardAnaliticoOut,
    EntrenarModeloIn,
    GrupoProductoOut,
    ModeloAnaliticoOut,
    PrediccionDemandaOut,
    PrediccionProductoOut,
    ProductoAgrupadoOut,
    PuntoSemanaOut,
    ResumenGrupoOut,
    TopProductoOut,
)

ESTADOS_PAGADOS = (EstadoVenta.PAGADA, EstadoVenta.COMPLETADA)
_CENTAVOS = Decimal("0.01")

SEMANAS_DEFECTO = 12
DIAS_AGRUPACION_DEFECTO = 60
MIN_SEMANAS_REGRESION_DEFECTO = 4
K_DEFECTO = 3
UMBRAL_STOCK_CRITICO = 5
_ETIQUETAS_SEMANA_DASHBOARD = [
    "Hace 5 sem",
    "Hace 4 sem",
    "Hace 3 sem",
    "Hace 2 sem",
    "Sem pasada",
    "Esta semana",
]


def _dinero(valor: Decimal) -> Decimal:
    return valor.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _sin_tz(dt: datetime) -> datetime:
    """La columna `fecha_creacion` es `timestamp without time zone`: al leerla
    vuelve "naive", aunque se haya guardado en UTC. Para restar fechas en
    Python hay que quitarle el tzinfo al `ahora` tz-aware también."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _modelo_activo(session: Session, tipo: str) -> ModeloAnalitico | None:
    return session.exec(
        select(ModeloAnalitico)
        .where(ModeloAnalitico.tipo == tipo, ModeloAnalitico.activo == True)  # noqa: E712
        .order_by(ModeloAnalitico.id.desc())
    ).first()


# --------------------------------------------------------------------------- #
#  Consultas base reutilizadas por CU31/CU32/CU33/CU34
# --------------------------------------------------------------------------- #
def _ventas_semanales_por_producto(
    session: Session, empresa_id: int | None, semanas: int
) -> dict[int, dict]:
    """Por producto, una serie de `semanas` posiciones (más vieja primero,
    semana actual al final) con las unidades vendidas de cada una."""
    ahora = datetime.now(timezone.utc)
    desde_dt = ahora - timedelta(weeks=semanas)
    consulta = (
        select(
            VentaDetalle.cantidad,
            Venta.fecha_creacion,
            Producto.id,
            Producto.nombre,
        )
        .select_from(VentaDetalle)
        .join(Venta, Venta.id == VentaDetalle.venta_id)
        .join(ProductoVariante, ProductoVariante.id == VentaDetalle.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .where(
            Venta.estado.in_(ESTADOS_PAGADOS),
            Venta.fecha_creacion >= desde_dt,
        )
    )
    if empresa_id is not None:
        consulta = consulta.where(Producto.empresa_id == empresa_id)
    filas = session.exec(consulta).all()

    ahora_naive = _sin_tz(ahora)
    datos: dict[int, dict] = {}
    for cantidad, fecha, producto_id, nombre in filas:
        semanas_atras = (ahora_naive - _sin_tz(fecha)).days // 7
        if not (0 <= semanas_atras < semanas):
            continue
        info = datos.setdefault(producto_id, {"nombre": nombre, "serie": [0] * semanas})
        info["serie"][semanas - 1 - semanas_atras] += cantidad
    return datos


def _ventas_y_stock_por_producto(
    session: Session, empresa_id: int | None, dias: int
) -> dict[int, dict]:
    """Por producto: unidades vendidas e ingresos en los últimos `dias`
    días, más el stock disponible actual (sumado en todas sus sucursales)."""
    ahora = datetime.now(timezone.utc)
    desde_dt = ahora - timedelta(days=dias)
    consulta = (
        select(
            VentaDetalle.cantidad,
            VentaDetalle.precio_unitario,
            Producto.id,
            Producto.nombre,
        )
        .select_from(VentaDetalle)
        .join(Venta, Venta.id == VentaDetalle.venta_id)
        .join(ProductoVariante, ProductoVariante.id == VentaDetalle.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .where(
            Venta.estado.in_(ESTADOS_PAGADOS),
            Venta.fecha_creacion >= desde_dt,
        )
    )
    if empresa_id is not None:
        consulta = consulta.where(Producto.empresa_id == empresa_id)
    filas = session.exec(consulta).all()

    agregados: dict[int, dict] = {}
    for cantidad, precio, producto_id, nombre in filas:
        g = agregados.setdefault(
            producto_id, {"nombre": nombre, "unidades": 0, "ingresos": Decimal(0), "stock": 0}
        )
        g["unidades"] += cantidad
        g["ingresos"] += precio * cantidad

    if agregados:
        stock_consulta = (
            select(Producto.id, func.coalesce(func.sum(Inventario.cantidad_disponible), 0))
            .select_from(Producto)
            .join(ProductoVariante, ProductoVariante.producto_id == Producto.id)
            .join(Inventario, Inventario.variante_id == ProductoVariante.id, isouter=True)
            .where(Producto.id.in_(agregados.keys()))
            .group_by(Producto.id)
        )
        for producto_id, total in session.exec(stock_consulta).all():
            if producto_id in agregados:
                agregados[producto_id]["stock"] = int(total)
    return agregados


# --------------------------------------------------------------------------- #
#  CU32 — Generar predicción de demanda
# --------------------------------------------------------------------------- #
def predecir_demanda(
    session: Session, empresa_id: int, semanas: int = SEMANAS_DEFECTO
) -> PrediccionDemandaOut:
    modelo = _modelo_activo(session, TipoModeloAnalitico.DEMANDA)
    params = json.loads(modelo.parametros) if modelo else {}
    min_semanas = params.get("min_semanas_regresion", MIN_SEMANAS_REGRESION_DEFECTO)
    metodo_modelo = (
        f"Modelo entrenado v{modelo.version} (regresión lineal, scikit-learn)"
        if modelo
        else "Sin entrenar todavía — usando parámetros por defecto (ver CU34)"
    )

    datos = _ventas_semanales_por_producto(session, empresa_id, semanas)
    items: list[PrediccionProductoOut] = []
    for producto_id, info in datos.items():
        serie = info["serie"]
        semanas_con_venta = sum(1 for c in serie if c > 0)
        if semanas_con_venta == 0:
            continue
        if semanas_con_venta >= min_semanas and len(set(serie)) > 1:
            x = np.arange(len(serie)).reshape(-1, 1)
            y = np.array(serie, dtype=float)
            reg = LinearRegression().fit(x, y)
            prediccion = max(0.0, float(reg.predict([[len(serie)]])[0]))
            pendiente = float(reg.coef_[0])
            confianza = max(0.0, min(1.0, reg.score(x, y)))
            tendencia = (
                "creciendo" if pendiente > 0.05 else "bajando" if pendiente < -0.05 else "estable"
            )
            metodo = "regresion_lineal"
        else:
            prediccion = sum(serie) / semanas_con_venta
            tendencia = "estable"
            confianza = 0.3
            metodo = "promedio_simple"
        items.append(
            PrediccionProductoOut(
                producto_id=producto_id,
                producto_nombre=info["nombre"],
                semanas_con_datos=semanas_con_venta,
                promedio_semanal=round(sum(serie) / len(serie), 2),
                prediccion_proxima_semana=round(prediccion, 2),
                tendencia=tendencia,
                confianza=round(confianza, 2),
                metodo=metodo,
            )
        )
    items.sort(key=lambda i: i.prediccion_proxima_semana, reverse=True)
    return PrediccionDemandaOut(
        generado_en=datetime.now(timezone.utc),
        semanas_analizadas=semanas,
        metodo_modelo=metodo_modelo,
        items=items,
    )


# --------------------------------------------------------------------------- #
#  CU33 — Analizar agrupaciones y patrones de productos
# --------------------------------------------------------------------------- #
def _etiquetas_para(n: int) -> list[str]:
    if n <= 1:
        return ["Todos los productos"]
    if n == 2:
        return ["Alta rotación", "Baja rotación / riesgo de stock muerto"]
    if n == 3:
        return ["Alta rotación", "Rotación media", "Baja rotación / riesgo de stock muerto"]
    return [f"Grupo {i + 1} (de mayor a menor rotación)" for i in range(n)]


def _armar_grupo(indice: int, etiqueta: str, productos: list[dict]) -> GrupoProductoOut:
    n = len(productos)
    return GrupoProductoOut(
        grupo=indice,
        etiqueta=etiqueta,
        cantidad_productos=n,
        promedio_unidades=round(sum(p["unidades"] for p in productos) / n, 2),
        promedio_ingresos=_dinero(sum((p["ingresos"] for p in productos), Decimal(0)) / n),
        promedio_rotacion=round(sum(p["rotacion"] for p in productos) / n, 2),
        productos=[
            ProductoAgrupadoOut(
                producto_id=p["producto_id"],
                producto_nombre=p["nombre"],
                unidades_vendidas=p["unidades"],
                ingresos=_dinero(p["ingresos"]),
                rotacion=round(p["rotacion"], 2),
            )
            for p in sorted(productos, key=lambda x: x["unidades"], reverse=True)
        ],
    )


def agrupar_productos(
    session: Session, empresa_id: int, dias: int = DIAS_AGRUPACION_DEFECTO
) -> AgrupacionesOut:
    modelo = _modelo_activo(session, TipoModeloAnalitico.AGRUPACION)
    params = json.loads(modelo.parametros) if modelo else {}
    k = params.get("k", K_DEFECTO)
    metodo_modelo = (
        f"Modelo entrenado v{modelo.version} (K-Means, k={k}, scikit-learn)"
        if modelo
        else f"Sin entrenar todavía — usando k={k} por defecto (ver CU34)"
    )

    agregados = _ventas_y_stock_por_producto(session, empresa_id, dias)
    productos = [
        {
            "producto_id": pid,
            "nombre": g["nombre"],
            "unidades": g["unidades"],
            "ingresos": g["ingresos"],
            "rotacion": g["unidades"] / (g["stock"] + 1),
        }
        for pid, g in agregados.items()
        if g["unidades"] > 0
    ]
    if not productos:
        return AgrupacionesOut(
            generado_en=datetime.now(timezone.utc),
            dias_analizados=dias,
            k=0,
            metodo_modelo=metodo_modelo,
            grupos=[],
        )

    k_real = min(k, len(productos))
    if k_real < 2:
        grupo = _armar_grupo(0, "Sin suficientes datos todavía para agrupar", productos)
        return AgrupacionesOut(
            generado_en=datetime.now(timezone.utc),
            dias_analizados=dias,
            k=1,
            metodo_modelo=metodo_modelo,
            grupos=[grupo],
        )

    x = np.array([[p["unidades"], float(p["ingresos"]), p["rotacion"]] for p in productos])
    x_esc = StandardScaler().fit_transform(x)
    etiquetas = KMeans(n_clusters=k_real, n_init=10, random_state=42).fit_predict(x_esc)

    grupos_raw: dict[int, list[dict]] = {}
    for producto, et in zip(productos, etiquetas):
        grupos_raw.setdefault(int(et), []).append(producto)

    orden = sorted(
        grupos_raw.items(),
        key=lambda kv: sum(p["unidades"] for p in kv[1]) / len(kv[1]),
        reverse=True,
    )
    textos = _etiquetas_para(len(orden))
    resultado = [_armar_grupo(i, textos[i], lista) for i, (_, lista) in enumerate(orden)]
    return AgrupacionesOut(
        generado_en=datetime.now(timezone.utc),
        dias_analizados=dias,
        k=k_real,
        metodo_modelo=metodo_modelo,
        grupos=resultado,
    )


# --------------------------------------------------------------------------- #
#  CU31 — Consultar dashboard analítico
# --------------------------------------------------------------------------- #
def dashboard(session: Session, empresa_id: int) -> DashboardAnaliticoOut:
    ahora = datetime.now(timezone.utc)
    desde_mes = ahora - timedelta(days=30)

    filas = session.exec(
        select(
            VentaDetalle.cantidad,
            VentaDetalle.precio_unitario,
            Venta.id,
            Venta.fecha_creacion,
            Producto.nombre,
        )
        .select_from(VentaDetalle)
        .join(Venta, Venta.id == VentaDetalle.venta_id)
        .join(ProductoVariante, ProductoVariante.id == VentaDetalle.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .where(
            Venta.estado.in_(ESTADOS_PAGADOS),
            Venta.fecha_creacion >= desde_mes,
            Producto.empresa_id == empresa_id,
        )
    ).all()

    ahora_naive = _sin_tz(ahora)
    ingresos_mes = Decimal(0)
    unidades_mes = 0
    ventas_ids: set[int] = set()
    por_producto: dict[str, dict] = {}
    semanas_ingresos = [Decimal(0)] * 6
    semanas_unidades = [0] * 6
    for cantidad, precio, venta_id, fecha, nombre in filas:
        subtotal = precio * cantidad
        ingresos_mes += subtotal
        unidades_mes += cantidad
        ventas_ids.add(venta_id)
        g = por_producto.setdefault(nombre, {"unidades": 0, "ingresos": Decimal(0)})
        g["unidades"] += cantidad
        g["ingresos"] += subtotal
        semanas_atras = (ahora_naive - _sin_tz(fecha)).days // 7
        if 0 <= semanas_atras < 6:
            pos = 5 - semanas_atras
            semanas_ingresos[pos] += subtotal
            semanas_unidades[pos] += cantidad

    cantidad_ventas = len(ventas_ids)
    ticket_promedio = _dinero(ingresos_mes / cantidad_ventas) if cantidad_ventas else Decimal("0.00")
    top_productos = sorted(
        (
            {"nombre": n, "unidades": g["unidades"], "ingresos": _dinero(g["ingresos"])}
            for n, g in por_producto.items()
        ),
        key=lambda x: (x["unidades"], x["ingresos"]),
        reverse=True,
    )[:5]

    stock_critico = session.exec(
        select(func.count())
        .select_from(Inventario)
        .join(ProductoVariante, ProductoVariante.id == Inventario.variante_id)
        .join(Producto, Producto.id == ProductoVariante.producto_id)
        .where(
            Producto.empresa_id == empresa_id,
            Inventario.cantidad_disponible <= UMBRAL_STOCK_CRITICO,
        )
    ).one()

    predicciones = predecir_demanda(session, empresa_id, SEMANAS_DEFECTO)
    agrupaciones = agrupar_productos(session, empresa_id, DIAS_AGRUPACION_DEFECTO)

    return DashboardAnaliticoOut(
        generado_en=ahora,
        ingresos_mes=_dinero(ingresos_mes),
        unidades_mes=unidades_mes,
        cantidad_ventas_mes=cantidad_ventas,
        ticket_promedio=ticket_promedio,
        variantes_stock_critico=stock_critico,
        serie_semanal=[
            PuntoSemanaOut(semana=etiqueta, ingresos=_dinero(ingreso), unidades=unidad)
            for etiqueta, ingreso, unidad in zip(
                _ETIQUETAS_SEMANA_DASHBOARD, semanas_ingresos, semanas_unidades
            )
        ],
        top_productos=[TopProductoOut(**t) for t in top_productos],
        prediccion_destacada=predicciones.items[:5],
        resumen_agrupacion=[
            ResumenGrupoOut(etiqueta=g.etiqueta, cantidad_productos=g.cantidad_productos)
            for g in agrupaciones.grupos
        ],
    )


# --------------------------------------------------------------------------- #
#  CU34 — Entrenar, evaluar y actualizar modelos analíticos
# --------------------------------------------------------------------------- #
def _entrenar_demanda(session: Session) -> tuple[dict, dict, int]:
    datos = _ventas_semanales_por_producto(session, None, SEMANAS_DEFECTO)
    errores_abs: list[float] = []
    reales: list[float] = []
    predichos: list[float] = []

    for info in datos.values():
        serie = info["serie"]
        entrenamiento = serie[:-1]
        if sum(1 for c in entrenamiento if c > 0) < MIN_SEMANAS_REGRESION_DEFECTO:
            continue
        if len(set(entrenamiento)) < 2:
            continue
        x = np.arange(len(entrenamiento)).reshape(-1, 1)
        y = np.array(entrenamiento, dtype=float)
        reg = LinearRegression().fit(x, y)
        prediccion = max(0.0, float(reg.predict([[len(entrenamiento)]])[0]))
        real = float(serie[-1])
        errores_abs.append(abs(prediccion - real))
        reales.append(real)
        predichos.append(prediccion)

    if len(errores_abs) < 3:
        raise HTTPException(
            422,
            "No hay suficientes productos con historial de ventas en la plataforma "
            "todavía para entrenar/evaluar este modelo (hacen falta al menos 3 "
            "productos con ventas en varias semanas distintas).",
        )

    mae = sum(errores_abs) / len(errores_abs)
    rmse = (sum(e**2 for e in errores_abs) / len(errores_abs)) ** 0.5
    r2 = r2_score(reales, predichos) if len(set(reales)) > 1 else None
    parametros = {
        "min_semanas_regresion": MIN_SEMANAS_REGRESION_DEFECTO,
        "semanas_historial": SEMANAS_DEFECTO,
    }
    metricas = {
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "r2": round(r2, 3) if r2 is not None else None,
        "series_evaluadas": len(errores_abs),
    }
    return parametros, metricas, len(errores_abs)


def _entrenar_agrupacion(session: Session) -> tuple[dict, dict, int]:
    agregados = _ventas_y_stock_por_producto(session, None, DIAS_AGRUPACION_DEFECTO)
    productos = [
        {
            "unidades": g["unidades"],
            "ingresos": float(g["ingresos"]),
            "rotacion": g["unidades"] / (g["stock"] + 1),
        }
        for g in agregados.values()
        if g["unidades"] > 0
    ]
    if len(productos) < 6:
        raise HTTPException(
            422,
            "No hay suficientes productos con ventas en la plataforma todavía para "
            "entrenar/evaluar este modelo (hacen falta al menos 6 productos distintos "
            "con ventas).",
        )

    x = np.array([[p["unidades"], p["ingresos"], p["rotacion"]] for p in productos])
    x_esc = StandardScaler().fit_transform(x)

    puntajes: dict[int, float] = {}
    for k in (2, 3, 4):
        if k >= len(productos):
            continue
        etiquetas = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(x_esc)
        if len(set(etiquetas)) < 2:
            continue
        puntajes[k] = round(float(silhouette_score(x_esc, etiquetas)), 3)

    if not puntajes:
        raise HTTPException(
            422,
            "No se pudo evaluar ningún valor de k con los datos actuales; probá de "
            "nuevo cuando haya más variedad de productos vendidos.",
        )

    mejor_k = max(puntajes, key=puntajes.get)
    parametros = {"k": mejor_k}
    metricas = {
        "silhouette_score": puntajes[mejor_k],
        "k_evaluados": puntajes,
        "muestras": len(productos),
    }
    return parametros, metricas, len(productos)


def entrenar_modelo(
    session: Session, usuario: Usuario, data: EntrenarModeloIn
) -> ModeloAnaliticoOut:
    if data.tipo not in TipoModeloAnalitico.TODOS:
        raise HTTPException(422, "Tipo de modelo inválido.")

    if data.tipo == TipoModeloAnalitico.DEMANDA:
        parametros, metricas, muestras = _entrenar_demanda(session)
    else:
        parametros, metricas, muestras = _entrenar_agrupacion(session)

    anterior = _modelo_activo(session, data.tipo)
    if anterior is not None:
        anterior.activo = False
        session.add(anterior)

    nuevo = ModeloAnalitico(
        tipo=data.tipo,
        version=(anterior.version + 1) if anterior else 1,
        parametros=json.dumps(parametros),
        metricas=json.dumps(metricas),
        muestras_entrenamiento=muestras,
        activo=True,
        entrenado_por_id=usuario.id,
    )
    session.add(nuevo)
    session.commit()
    session.refresh(nuevo)
    return ModeloAnaliticoOut(
        id=nuevo.id,
        tipo=nuevo.tipo,
        version=nuevo.version,
        parametros=parametros,
        metricas=metricas,
        muestras_entrenamiento=nuevo.muestras_entrenamiento,
        activo=nuevo.activo,
        entrenado_por=f"{usuario.nombre} {usuario.apellido}",
        entrenado_en=nuevo.entrenado_en,
    )


def listar_modelos(session: Session) -> list[ModeloAnaliticoOut]:
    filas = session.exec(
        select(ModeloAnalitico, Usuario)
        .join(Usuario, Usuario.id == ModeloAnalitico.entrenado_por_id)
        .order_by(ModeloAnalitico.entrenado_en.desc())
    ).all()
    return [
        ModeloAnaliticoOut(
            id=m.id,
            tipo=m.tipo,
            version=m.version,
            parametros=json.loads(m.parametros),
            metricas=json.loads(m.metricas),
            muestras_entrenamiento=m.muestras_entrenamiento,
            activo=m.activo,
            entrenado_por=f"{u.nombre} {u.apellido}",
            entrenado_en=m.entrenado_en,
        )
        for m, u in filas
    ]
