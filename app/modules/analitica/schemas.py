"""Esquemas del módulo Analítica inteligente — CU31/CU32/CU33/CU34."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  CU32 — Generar predicción de demanda
# --------------------------------------------------------------------------- #
class PrediccionProductoOut(BaseModel):
    producto_id: int
    producto_nombre: str
    semanas_con_datos: int
    promedio_semanal: float
    prediccion_proxima_semana: float
    tendencia: str  # creciendo | estable | bajando
    confianza: float  # 0..1
    metodo: str  # regresion_lineal | promedio_simple


class PrediccionDemandaOut(BaseModel):
    generado_en: datetime
    semanas_analizadas: int
    metodo_modelo: str
    items: list[PrediccionProductoOut]


# --------------------------------------------------------------------------- #
#  CU33 — Analizar agrupaciones y patrones de productos
# --------------------------------------------------------------------------- #
class ProductoAgrupadoOut(BaseModel):
    producto_id: int
    producto_nombre: str
    unidades_vendidas: int
    ingresos: Decimal
    rotacion: float


class GrupoProductoOut(BaseModel):
    grupo: int
    etiqueta: str
    cantidad_productos: int
    promedio_unidades: float
    promedio_ingresos: Decimal
    promedio_rotacion: float
    productos: list[ProductoAgrupadoOut]


class AgrupacionesOut(BaseModel):
    generado_en: datetime
    dias_analizados: int
    k: int
    metodo_modelo: str
    grupos: list[GrupoProductoOut]


# --------------------------------------------------------------------------- #
#  CU31 — Consultar dashboard analítico
# --------------------------------------------------------------------------- #
class PuntoSemanaOut(BaseModel):
    semana: str  # etiqueta "Sem -3", "Sem -2", ... "Sem actual"
    ingresos: Decimal
    unidades: int


class TopProductoOut(BaseModel):
    nombre: str
    unidades: int
    ingresos: Decimal


class ResumenGrupoOut(BaseModel):
    etiqueta: str
    cantidad_productos: int


class DashboardAnaliticoOut(BaseModel):
    generado_en: datetime
    ingresos_mes: Decimal
    unidades_mes: int
    cantidad_ventas_mes: int
    ticket_promedio: Decimal
    variantes_stock_critico: int
    serie_semanal: list[PuntoSemanaOut]
    top_productos: list[TopProductoOut]
    prediccion_destacada: list[PrediccionProductoOut]
    resumen_agrupacion: list[ResumenGrupoOut]


# --------------------------------------------------------------------------- #
#  CU34 — Entrenar, evaluar y actualizar modelos analíticos
# --------------------------------------------------------------------------- #
class EntrenarModeloIn(BaseModel):
    tipo: str = Field(description="DEMANDA o AGRUPACION")


class ModeloAnaliticoOut(BaseModel):
    id: int
    tipo: str
    version: int
    parametros: dict
    metricas: dict
    muestras_entrenamiento: int
    activo: bool
    entrenado_por: str
    entrenado_en: datetime
