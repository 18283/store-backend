"""Modelos del módulo Analítica inteligente — CU31 (dashboard analítico),
CU32 (predicción de demanda), CU33 (agrupaciones y patrones) y CU34
(entrenar, evaluar y actualizar modelos analíticos)."""

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class TipoModeloAnalitico:
    DEMANDA = "DEMANDA"
    AGRUPACION = "AGRUPACION"

    TODOS = (DEMANDA, AGRUPACION)


class ModeloAnalitico(SQLModel, table=True):
    """CU34 — una corrida de entrenamiento/evaluación de uno de los dos
    modelos de analítica (demanda o agrupación de productos).

    Se entrena a nivel plataforma (con datos de todas las empresas, no de
    una sola), porque lo que se calibra acá son los hiperparámetros del
    algoritmo (p. ej. cuántas semanas de historial pedir antes de confiar
    en una regresión, o cuántos grupos (k) usar en el clustering). CU32 y
    CU33 después aplican esos hiperparámetros sobre los datos de CADA
    empresa por separado, en el momento de la consulta — el modelo nunca
    mezcla datos de una empresa con los de otra al predecir.
    """

    __tablename__ = "modelo_analitico"

    id: int | None = Field(default=None, primary_key=True)
    tipo: str = Field(max_length=20, index=True)
    version: int = Field(default=1)
    parametros: str = Field(description="JSON con los hiperparámetros elegidos")
    metricas: str = Field(description="JSON con las métricas de evaluación")
    muestras_entrenamiento: int = Field(default=0)
    activo: bool = Field(default=True, index=True)
    entrenado_por_id: int = Field(foreign_key="usuario.id")
    entrenado_en: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
