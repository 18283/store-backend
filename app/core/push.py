"""Notificaciones push (Firebase Cloud Messaging) — CU14/CU15.

Se usan solo para avisar el resultado de sincronizar una reserva o venta
registrada sin conexión. Si Firebase no está configurado (archivo de
credenciales ausente) las funciones no hacen nada: el resto del sistema
sigue funcionando igual, el cliente solo no recibe el aviso push.
"""

import json
import logging
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.modules.offline.models import DispositivoPush

logger = logging.getLogger("fashionstore.push")

_firebase_app = None
_firebase_disponible: bool | None = None


def _inicializar() -> bool:
    """Lee la credencial desde `FIREBASE_CREDENTIALS_JSON` (el contenido del
    JSON pegado directo en una variable de entorno — útil en Render, donde no
    hay disco persistente propio) o, si no está, desde el archivo en
    `firebase_credentials_path` (lo que se usa en desarrollo local)."""
    global _firebase_app, _firebase_disponible
    if _firebase_disponible is not None:
        return _firebase_disponible

    try:
        import firebase_admin
        from firebase_admin import credentials

        if settings.firebase_credentials_json:
            cred = credentials.Certificate(
                json.loads(settings.firebase_credentials_json)
            )
        else:
            ruta = Path(settings.firebase_credentials_path)
            if not ruta.is_file():
                logger.warning(
                    "Firebase no configurado (no existe %s) — no se enviarán push.",
                    ruta,
                )
                _firebase_disponible = False
                return False
            cred = credentials.Certificate(str(ruta))

        _firebase_app = firebase_admin.initialize_app(cred)
        _firebase_disponible = True
    except Exception:  # noqa: BLE001 - un error de Firebase no debe tumbar el sync
        logger.exception("No se pudo inicializar Firebase Admin")
        _firebase_disponible = False
    return _firebase_disponible


def enviar_push_a_usuario(
    session: Session,
    usuario_id: int,
    titulo: str,
    cuerpo: str,
    data: dict[str, str] | None = None,
) -> None:
    if not _inicializar():
        return

    dispositivos = session.exec(
        select(DispositivoPush).where(DispositivoPush.usuario_id == usuario_id)
    ).all()
    if not dispositivos:
        return

    from firebase_admin import messaging

    tokens_invalidos: list[str] = []
    for dispositivo in dispositivos:
        mensaje = messaging.Message(
            notification=messaging.Notification(title=titulo, body=cuerpo),
            data=data or {},
            token=dispositivo.token,
        )
        try:
            messaging.send(mensaje)
        except messaging.UnregisteredError:
            tokens_invalidos.append(dispositivo.token)
        except Exception:  # noqa: BLE001 - nunca romper el flujo de sincronización
            logger.exception("Error enviando push al token %s", dispositivo.token)

    if tokens_invalidos:
        for dispositivo in dispositivos:
            if dispositivo.token in tokens_invalidos:
                session.delete(dispositivo)
        session.commit()
