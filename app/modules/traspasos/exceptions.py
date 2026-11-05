"""Errores de negocio del módulo Traspasos — CU24.

El frontend (web y móvil) necesita distinguir una confirmación de un duplicado.
Por eso el cuerpo de la respuesta lleva los datos en el primer nivel:

    {
      "detail": "mensaje legible",
      "codigo": "STOCK_DESTINO_SUFICIENTE",
      "mensaje": "mensaje legible",
      "requiere_confirmacion": true,
      "stock_destino": 4
    }

`detail` se mantiene como string para que cualquier cliente que solo lea el
contrato estándar de FastAPI también muestre el mensaje correcto.
"""

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class TraspasoError(HTTPException):
    """Error de negocio con código estable y datos adicionales."""

    def __init__(
        self,
        status_code: int,
        codigo: str,
        mensaje: str,
        **extra: Any,
    ) -> None:
        detalle: dict[str, Any] = {
            "detail": mensaje,
            "codigo": codigo,
            "mensaje": mensaje,
            **extra,
        }
        super().__init__(status_code=status_code, detail=detalle)
        self.codigo = codigo
        self.mensaje = mensaje
        self.extra = extra


async def traspaso_error_handler(
    _request: Request, exc: TraspasoError
) -> JSONResponse:
    contenido: dict[str, Any] = {
        "detail": exc.mensaje,
        "codigo": exc.codigo,
        "mensaje": exc.mensaje,
        **exc.extra,
    }
    return JSONResponse(status_code=exc.status_code, content=contenido)


# Códigos usados por el frontend.
STOCK_DESTINO_SUFICIENTE = "STOCK_DESTINO_SUFICIENTE"
SOLICITUD_PENDIENTE_EXISTENTE = "SOLICITUD_PENDIENTE_EXISTENTE"
SIN_STOCK_ORIGEN = "SIN_STOCK_ORIGEN"
STOCK_INSUFICIENTE = "STOCK_INSUFICIENTE"
NO_SE_PUDO_REGISTRAR = "NO_SE_PUDO_REGISTRAR"
SOLICITUD_YA_PROCESADA = "SOLICITUD_YA_PROCESADA"
MOTIVO_RECHAZO_REQUERIDO = "MOTIVO_RECHAZO_REQUERIDO"
SUCURSAL_INACTIVA_TRASPASO = "SUCURSAL_INACTIVA_TRASPASO"
SIN_STOCK_SUFICIENTE = "SIN_STOCK_SUFICIENTE"

# Códigos nuevos para CU26 — Despachar traspaso.
DESPECHO_INVALIDO = "DESPECHO_INVALIDO"          # solicitud no APROBADA o ya despachada
SIN_PERMISO_DESPECHO = "SIN_PERMISO_DESPECHO"      # actor no es Encargado o no es de la sucursal origen
STOCK_INCONSISTENTE = "STOCK_INCONSISTENTE"        # cantidad_reservada insuficiente
VARIANTE_INACTIVA_DESPECHO = "VARIANTE_INACTIVA_DESPECHO"  # variante inactiva o destino inactiva
DATOS_ENVIO_INCOMPLETOS = "DATOS_ENVIO_INCOMPLETOS"  # datos de envío obligatorios faltan
CONFLICTO_DESPECHO_SIMULTANEO = "CONFLICTO_DESPECHO_SIMULTANEO"  # A7: dos usuarios simultáneos
ERROR_GUARDAR_DESPECHO = "ERROR_GUARDAR_DESPECHO"  # A9: error al guardar


def conflicto(codigo: str, mensaje: str, **extra: Any) -> TraspasoError:
    """Atajo para los conflictos de negocio (HTTP 409)."""
    return TraspasoError(status.HTTP_409_CONFLICT, codigo, mensaje, **extra)
