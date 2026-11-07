"""Centralización de avisos del centro de notificaciones — CU29 / CU35.

El mismo servicio crea las notificaciones de traspasos (CU29), reservas,
cambios/devoluciones, suscripción, sincronización offline y difusiones
masivas del Administrador (CU35), todas persistidas en la tabla
`notificacion` e identificadas por el campo `modulo`.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.traspasos.models import ModuloNotificacion, Notificacion

# Acepta tanto el nombre exacto del rol ("Cliente") como el token en
# mayúsculas que usa la especificación ("CLIENTE", "ENCARGADO", ...).
_ALIAS_ROLES: dict[str, str] = {
    "CLIENTE": RolNombre.CLIENTE,
    "ADMINISTRADOR": RolNombre.ADMINISTRADOR,
    "ADMIN": RolNombre.ADMINISTRADOR,
    "ENCARGADO": RolNombre.ENCARGADO,
    "ENCARGADOSUCURSAL": RolNombre.ENCARGADO,
    "ENCARGADO_SUCURSAL": RolNombre.ENCARGADO,
    "CAJERO": RolNombre.CAJERO,
    "PROVEEDOR": RolNombre.PROVEEDOR,
    "SUPERADMINISTRADOR": RolNombre.SUPERADMINISTRADOR,
    "SUPERADMIN": RolNombre.SUPERADMINISTRADOR,
}


def normalizar_rol(rol: str | RolNombre) -> str:
    """Devuelve el nombre exacto de rol en BD o levanta 422 si es inválido."""
    texto = str(rol).strip()
    if texto in RolNombre.TODOS:
        return texto
    clave = texto.upper()
    if clave in _ALIAS_ROLES:
        return _ALIAS_ROLES[clave]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Rol destino inválido: {rol!r}. Use uno de: {sorted(RolNombre.TODOS)} o {sorted(_ALIAS_ROLES)}",
    )


def crear_notificacion(
    session: Session,
    usuario_id: int,
    mensaje: str,
    tipo: str = "info",
    codigo_traspaso: str | None = None,
    modulo: str | None = None,
) -> Notificacion:
    """Guarda una nueva notificación en la base de datos para un usuario específico."""
    notificacion = Notificacion(
        usuario_id=usuario_id,
        mensaje=mensaje,
        tipo=tipo,
        codigo_traspaso=codigo_traspaso,
        modulo=modulo,
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
    modulo: str | None = None,
) -> list[Notificacion]:
    """Crea una notificación para cada usuario activo con los roles indicados.

    Acepta roles exactos ("Cliente") o tokens ("CLIENTE", "ENCARGADO", ...).
    Si se indica `empresa_id`, solo notifica usuarios de esa empresa.
    """
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="roles_destino no puede estar vacío",
        )
    roles_str = [normalizar_rol(r) for r in roles]

    roles_db = session.exec(
        select(Rol).where(Rol.nombre.in_(roles_str))
    ).all()
    if not roles_db:
        return []

    rol_ids = [r.id for r in roles_db]

    query = select(Usuario).where(
        Usuario.rol_id.in_(rol_ids),
        Usuario.activo == True,  # noqa: E712
    )
    if empresa_id is not None:
        query = query.where(Usuario.empresa_id == empresa_id)

    usuarios = session.exec(query).all()

    ahora = datetime.now(timezone.utc)
    notificaciones = [
        Notificacion(
            usuario_id=usuario.id,
            mensaje=mensaje,
            tipo=tipo,
            codigo_traspaso=codigo_traspaso,
            modulo=modulo,
            fecha=ahora,
        )
        for usuario in usuarios
    ]
    if notificaciones:
        session.add_all(notificaciones)
        session.commit()
        for notif in notificaciones:
            session.refresh(notif)
    return notificaciones


def _segura(func, *args, **kwargs):
    """Ejecuta una creación de notificación sin romper el flujo principal.

    Debe llamarse DESPUÉS del commit de la operación principal: ante un
    fallo (p. ej. tabla ausente en un test mínimo) hace rollback solo de
    la notificación y devuelve None/[] sin propagar la excepción (A8).
    """
    try:
        return func(*args, **kwargs)
    except Exception:  # noqa: BLE001 - el aviso nunca debe tumbar el flujo
        session = kwargs.get("session") or (args[0] if args else None)
        if session is not None:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None


def crear_notificacion_segura(*args, **kwargs):
    return _segura(crear_notificacion, *args, **kwargs)


def crear_notificacion_para_roles_segura(*args, **kwargs):
    resultado = _segura(crear_notificacion_para_roles, *args, **kwargs)
    return resultado or []
