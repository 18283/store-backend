"""Servicio de notificaciones para el módulo de Traspasos (CU29)."""

from datetime import datetime, timezone
from sqlmodel import Session, select

from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.traspasos.models import Notificacion


def crear_notificacion(
    session: Session,
    usuario_id: int,
    mensaje: str,
    tipo: str = "info",
    codigo_traspaso: str | None = None,
) -> Notificacion:
    """Guarda una nueva notificación en la base de datos para un usuario específico."""
    notificacion = Notificacion(
        usuario_id=usuario_id,
        mensaje=mensaje,
        tipo=tipo,
        codigo_traspaso=codigo_traspaso,
        fecha=datetime.now(timezone.utc),
    )
    session.add(notificacion)
    session.commit()
    session.refresh(notificacion)
    return notificacion


def crear_notificacion_para_roles(
    session: Session,
    roles: list[str | RolNombre],
    mensaje: str,
    tipo: str = "info",
    codigo_traspaso: str | None = None,
    empresa_id: int | None = None,
) -> list[Notificacion]:
    """Busca a los usuarios activos con los roles indicados (dentro de una empresa opcional)

    y les crea la notificación correspondiente.
    """
    # Normalizar roles a strings
    roles_str = [r.value if isinstance(r, RolNombre) else str(r) for r in roles]

    # Buscar los IDs de los roles correspondientes
    roles_db = session.exec(
        select(Rol).where(Rol.nombre.in_(roles_str))
    ).all()
    if not roles_db:
        return []

    rol_ids = [r.id for r in roles_db]

    # Buscar usuarios activos que tengan esos roles
    query = select(Usuario).where(
        Usuario.rol_id.in_(rol_ids),
        Usuario.activo == True,  # noqa: E712
    )
    if empresa_id is not None:
        query = query.where(Usuario.empresa_id == empresa_id)

    usuarios = session.exec(query).all()

    notificaciones = []
    for usuario in usuarios:
        notif = crear_notificacion(
            session=session,
            usuario_id=usuario.id,
            mensaje=mensaje,
            tipo=tipo,
            codigo_traspaso=codigo_traspaso,
        )
        notificaciones.append(notif)

    return notificaciones
