"""Pruebas del centro de notificaciones — CU35."""

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.saas.models import Empresa
from app.modules.traspasos.models import Notificacion
from app.modules.traspasos.notificacion_service import (
    crear_notificacion,
    crear_notificacion_para_roles,
    normalizar_rol,
)


class NotificacionesCU35Test:
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tablas = [
            Empresa.__table__,
            Rol.__table__,
            Usuario.__table__,
            Notificacion.__table__,
        ]
        SQLModel.metadata.create_all(self.engine, tables=self.tablas)
        self.session = Session(self.engine)

        self.rol_cliente = Rol(nombre=RolNombre.CLIENTE)
        self.rol_encargado = Rol(nombre=RolNombre.ENCARGADO)
        self.rol_admin = Rol(nombre=RolNombre.ADMINISTRADOR)
        self.rol_cajero = Rol(nombre=RolNombre.CAJERO)
        self.session.add_all(
            [self.rol_cliente, self.rol_encargado, self.rol_admin, self.rol_cajero]
        )
        self.session.flush()

        self.empresa = Empresa(identificador="emp-1", nombre="Empresa 1")
        self.empresa2 = Empresa(identificador="emp-2", nombre="Empresa 2")
        self.session.add_all([self.empresa, self.empresa2])
        self.session.flush()

        def _u(nombre, email, rol, empresa_id):
            return Usuario(
                nombre=nombre, apellido="Test", email=email,
                password_hash="x", empresa_id=empresa_id, rol_id=rol.id,
            )

        self.cliente1 = _u("C1", "c1@x.com", self.rol_cliente, self.empresa.id)
        self.cliente2 = _u("C2", "c2@x.com", self.rol_cliente, self.empresa.id)
        self.cliente_otra = _u("C3", "c3@x.com", self.rol_cliente, self.empresa2.id)
        self.encargado = _u("E1", "e1@x.com", self.rol_encargado, self.empresa.id)
        self.cajero = _u("K1", "k1@x.com", self.rol_cajero, self.empresa.id)
        self.admin = _u("A1", "a1@x.com", self.rol_admin, self.empresa.id)
        self.session.add_all(
            [self.cliente1, self.cliente2, self.cliente_otra, self.encargado, self.cajero, self.admin]
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        SQLModel.metadata.drop_all(self.engine, tables=list(reversed(self.tablas)))

    def test_normalizar_rol_acepta_token_mayusculas(self) -> None:
        assert normalizar_rol("CLIENTE") == RolNombre.CLIENTE
        assert normalizar_rol("ENCARGADO") == RolNombre.ENCARGADO
        assert normalizar_rol("Cliente") == RolNombre.CLIENTE
        assert normalizar_rol(RolNombre.CAJERO) == RolNombre.CAJERO

    def test_normalizar_rol_invalido_422(self) -> None:
        try:
            normalizar_rol("INVENTADO")
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("debió fallar")

    def test_masiva_solo_empresa_y_roles(self) -> None:
        notifs = crear_notificacion_para_roles(
            self.session,
            roles=["CLIENTE", "ENCARGADO"],
            mensaje="20% off en remeras",
            tipo="promo",
            empresa_id=self.empresa.id,
            modulo="masiva",
        )
        assert len(notifs) == 3  # 2 clientes + 1 encargado, no el de otra empresa
        assert all(n.modulo == "masiva" for n in notifs)
        assert all(n.leida is False for n in notifs)
        destino_ids = {n.usuario_id for n in notifs}
        assert self.cliente_otra.id not in destino_ids
        assert self.admin.id not in destino_ids

    def test_masiva_sin_destinatarios_devuelve_vacio(self) -> None:
        notifs = crear_notificacion_para_roles(
            self.session,
            roles=["CAJERO"],
            mensaje="x",
            empresa_id=self.empresa2.id,
            modulo="masiva",
        )
        assert notifs == []

    def test_crear_notificacion_con_modulo(self) -> None:
        n = crear_notificacion(
            self.session, self.cliente1.id, "reserva creada",
            tipo="success", modulo="reservas",
        )
        assert n.modulo == "reservas"
        assert n.leida is False


def test_cu35_helpers():
    t = NotificacionesCU35Test()
    t.setUp()
    try:
        t.test_normalizar_rol_acepta_token_mayusculas()
        t.test_normalizar_rol_invalido_422()
        t.test_masiva_solo_empresa_y_roles()
        t.test_masiva_sin_destinatarios_devuelve_vacio()
        t.test_crear_notificacion_con_modulo()
    finally:
        t.tearDown()
