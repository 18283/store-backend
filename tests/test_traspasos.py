"""Pruebas del módulo Traspasos — CU24 (código anterior CU61)."""

import unittest
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.modules.catalogo.models import Categoria, Color, Talla
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.inventario.models import Inventario
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.proveedores.models import Proveedor
from app.modules.saas.models import Empresa
from app.modules.sucursales.models import Sucursal
from app.modules.traspasos import service
from app.modules.traspasos.models import EstadoTraspaso, SolicitudTraspaso
from app.modules.traspasos.schemas import (
    SolicitudTraspasoCrearIn,
    SolicitudTraspasoDecisionIn,
)


class TraspasosServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.tablas = [
            Empresa.__table__,
            Rol.__table__,
            Proveedor.__table__,
            Sucursal.__table__,
            Usuario.__table__,
            Categoria.__table__,
            Talla.__table__,
            Color.__table__,
            Producto.__table__,
            ProductoVariante.__table__,
            Inventario.__table__,
            SolicitudTraspaso.__table__,
        ]
        SQLModel.metadata.create_all(self.engine, tables=self.tablas)
        self.session = Session(self.engine)

        self.rol_cliente = Rol(nombre=RolNombre.CLIENTE)
        self.rol_encargado = Rol(nombre=RolNombre.ENCARGADO)
        self.rol_admin = Rol(nombre=RolNombre.ADMINISTRADOR)
        self.session.add_all([self.rol_cliente, self.rol_encargado, self.rol_admin])
        self.session.flush()

        self.empresa_a = Empresa(identificador="empresa-a", nombre="Empresa A")
        self.empresa_b = Empresa(identificador="empresa-b", nombre="Empresa B")
        self.session.add_all([self.empresa_a, self.empresa_b])
        self.session.flush()

        self.s1 = Sucursal(
            empresa_id=self.empresa_a.id,
            nombre="Centro",
            ciudad="Santa Cruz",
            direccion="Av. Centro 100",
        )
        self.s2 = Sucursal(
            empresa_id=self.empresa_a.id,
            nombre="Norte",
            ciudad="La Paz",
            direccion="Av. Norte 200",
        )
        self.s_b = Sucursal(
            empresa_id=self.empresa_b.id,
            nombre="Única",
            ciudad="Cochabamba",
            direccion="Calle B 300",
        )
        self.session.add_all([self.s1, self.s2, self.s_b])
        self.session.flush()

        self.cliente = Usuario(
            nombre="Carla",
            apellido="Cliente",
            email="cliente@example.com",
            password_hash="x",
            empresa_id=self.empresa_a.id,
            rol_id=self.rol_cliente.id,
        )
        self.otro_cliente = Usuario(
            nombre="Otto",
            apellido="Otro",
            email="otro@example.com",
            password_hash="x",
            empresa_id=self.empresa_a.id,
            rol_id=self.rol_cliente.id,
        )
        self.encargado = Usuario(
            nombre="Elena",
            apellido="Encargada",
            email="encargada@example.com",
            password_hash="x",
            empresa_id=self.empresa_a.id,
            rol_id=self.rol_encargado.id,
            sucursal_id=self.s1.id,
        )
        self.cliente_b = Usuario(
            nombre="Bruno",
            apellido="B",
            email="cliente-b@example.com",
            password_hash="x",
            empresa_id=self.empresa_b.id,
            rol_id=self.rol_cliente.id,
        )
        self.admin = Usuario(
            nombre="Alberto",
            apellido="Admin",
            email="admin@example.com",
            password_hash="x",
            empresa_id=self.empresa_a.id,
            rol_id=self.rol_admin.id,
        )
        self.session.add_all(
            [self.cliente, self.otro_cliente, self.encargado, self.cliente_b, self.admin]
        )
        self.session.flush()

        self.categoria_a = Categoria(empresa_id=self.empresa_a.id, nombre="Ropa")
        self.talla_a = Talla(empresa_id=self.empresa_a.id, valor="M", tipo="Ropa")
        self.talla_a2 = Talla(empresa_id=self.empresa_a.id, valor="L", tipo="Ropa")
        self.color_a = Color(
            empresa_id=self.empresa_a.id, nombre="Negro", codigo_hex="#000000"
        )
        self.color_a2 = Color(
            empresa_id=self.empresa_a.id, nombre="Blanco", codigo_hex="#FFFFFF"
        )
        self.proveedor_a = Proveedor(
            empresa_id=self.empresa_a.id, nombre_empresa="Proveedor A"
        )
        self.categoria_b = Categoria(empresa_id=self.empresa_b.id, nombre="Ropa")
        self.talla_b = Talla(empresa_id=self.empresa_b.id, valor="M", tipo="Ropa")
        self.color_b = Color(
            empresa_id=self.empresa_b.id, nombre="Azul", codigo_hex="#0000FF"
        )
        self.proveedor_b = Proveedor(
            empresa_id=self.empresa_b.id, nombre_empresa="Proveedor B"
        )
        self.session.add_all(
            [
                self.categoria_a,
                self.talla_a,
                self.talla_a2,
                self.color_a,
                self.color_a2,
                self.proveedor_a,
                self.categoria_b,
                self.talla_b,
                self.color_b,
                self.proveedor_b,
            ]
        )
        self.session.flush()

        self.producto_a = Producto(
            empresa_id=self.empresa_a.id,
            nombre="Polera básica",
            categoria_id=self.categoria_a.id,
            proveedor_id=self.proveedor_a.id,
            precio_base=Decimal("100.00"),
        )
        self.producto_b = Producto(
            empresa_id=self.empresa_b.id,
            nombre="Camisa B",
            categoria_id=self.categoria_b.id,
            proveedor_id=self.proveedor_b.id,
            precio_base=Decimal("120.00"),
        )
        self.session.add_all([self.producto_a, self.producto_b])
        self.session.flush()

        self.variante = ProductoVariante(
            empresa_id=self.empresa_a.id,
            producto_id=self.producto_a.id,
            talla_id=self.talla_a.id,
            color_id=self.color_a.id,
            sku="TRP-A-M-NEGRO",
        )
        self.variante_sin_stock = ProductoVariante(
            empresa_id=self.empresa_a.id,
            producto_id=self.producto_a.id,
            talla_id=self.talla_a2.id,
            color_id=self.color_a2.id,
            sku="TRP-A-L-BLANCO",
        )
        self.variante_b = ProductoVariante(
            empresa_id=self.empresa_b.id,
            producto_id=self.producto_b.id,
            talla_id=self.talla_b.id,
            color_id=self.color_b.id,
            sku="TRP-B-M-AZUL",
        )
        self.session.add_all(
            [self.variante, self.variante_sin_stock, self.variante_b]
        )
        self.session.flush()

        self.session.add_all(
            [
                Inventario(
                    variante_id=self.variante.id,
                    sucursal_id=self.s1.id,
                    cantidad_disponible=10,
                ),
                Inventario(
                    variante_id=self.variante.id,
                    sucursal_id=self.s2.id,
                    cantidad_disponible=2,
                ),
                Inventario(
                    variante_id=self.variante_b.id,
                    sucursal_id=self.s_b.id,
                    cantidad_disponible=5,
                ),
            ]
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        SQLModel.metadata.drop_all(self.engine, tables=list(reversed(self.tablas)))

    def _data(self, **overrides) -> SolicitudTraspasoCrearIn:
        valores = {
            "variante_id": self.variante.id,
            "origen_id": self.s1.id,
            "destino_id": self.s2.id,
            "cantidad": 3,
            "motivo": "Reposición",
        }
        valores.update(overrides)
        return SolicitudTraspasoCrearIn(**valores)

    def test_rutas_cu24_publicadas(self) -> None:
        paths = app.openapi()["paths"]
        for path in (
            "/traspasos/opciones",
            "/traspasos/variantes",
            "/traspasos",
            "/traspasos/{codigo}",
        ):
            self.assertIn(path, paths)

    def test_opciones_dependen_del_rol(self) -> None:
        opciones_cliente = service.preparar_formulario(
            self.session, self.cliente
        )
        self.assertEqual(opciones_cliente["rol"], RolNombre.CLIENTE)
        self.assertEqual(opciones_cliente["cliente_id"], self.cliente.id)
        self.assertEqual(len(opciones_cliente["sucursales"]), 2)
        self.assertEqual(len(opciones_cliente["destinos_permitidos"]), 2)

        opciones_encargado = service.preparar_formulario(
            self.session, self.encargado
        )
        self.assertEqual(opciones_encargado["rol"], RolNombre.ENCARGADO)
        self.assertEqual(
            opciones_encargado["sucursal_asignada_id"], self.s1.id
        )
        self.assertEqual(
            [s["id"] for s in opciones_encargado["destinos_permitidos"]],
            [self.s1.id],
        )

    def test_buscar_variantes_con_stock(self) -> None:
        resultado = service.buscar_variantes(
            self.session, self.cliente, q="negro"
        )
        self.assertEqual(resultado["total"], 1)
        items = resultado["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["variante_id"] == self.variante.id for item in items))
        origenes = {item["origen_id"] for item in items}
        self.assertEqual(origenes, {self.s1.id, self.s2.id})
        stock = {item["origen_id"]: item["cantidad_disponible"] for item in items}
        self.assertEqual(stock[self.s1.id], 10)
        self.assertEqual(stock[self.s2.id], 2)

        por_sku = service.buscar_variantes(
            self.session, self.cliente, q="TRP-A-M"
        )
        self.assertEqual(por_sku["total"], 1)
        self.assertEqual(len(por_sku["items"]), 2)
        # La variante sin stock no aparece como origen posible.
        sin_stock = service.buscar_variantes(
            self.session, self.cliente, q="BLANCO"
        )
        self.assertEqual(sin_stock["total"], 0)

    def test_crear_solicitud_pendiente_no_mueve_inventario(self) -> None:
        salida = service.solicitar_traspaso(
            self.session, self.cliente, self._data()
        )
        self.assertEqual(salida["estado"], EstadoTraspaso.PENDIENTE)
        self.assertTrue(salida["codigo"].startswith("TRP-"))
        self.assertEqual(salida["origen_id"], self.s1.id)
        self.assertEqual(salida["destino_id"], self.s2.id)
        self.assertIn("Polera básica", salida["resumen"])
        self.assertIn("Estado: PENDIENTE", salida["resumen"])

        inv_origen = self.session.exec(
            select(Inventario).where(
                Inventario.variante_id == self.variante.id,
                Inventario.sucursal_id == self.s1.id,
            )
        ).first()
        self.assertEqual(inv_origen.cantidad_disponible, 10)
        # El destino conserva su stock (no se suma nada hasta ejecutar).
        self.assertEqual(salida["stock_destino"], 2)

    def test_cliente_creacion_solicitud_y_solicitante_id(self) -> None:
        """Verifica que el rol Cliente puede solicitar traspasos y se registra el solicitante_id."""
        salida = service.solicitar_traspaso(
            self.session, self.cliente, self._data(origen_id=self.s1.id, destino_id=self.s2.id)
        )
        self.assertEqual(salida["solicitante_id"], self.cliente.id)
        self.assertEqual(salida["solicitante_rol"], RolNombre.CLIENTE)

        # Verificar en base de datos
        db_solicitud = self.session.exec(
            select(SolicitudTraspaso).where(SolicitudTraspaso.id == salida["id"])
        ).first()
        self.assertIsNotNone(db_solicitud)
        self.assertEqual(db_solicitud.solicitante_id, self.cliente.id)
        self.assertEqual(db_solicitud.estado, EstadoTraspaso.PENDIENTE)

    def test_encargado_solo_puede_pedir_hacia_su_sucursal(self) -> None:
        with self.assertRaises(HTTPException) as denegado:
            service.solicitar_traspaso(
                self.session,
                self.encargado,
                self._data(origen_id=self.s1.id, destino_id=self.s2.id),
            )
        self.assertEqual(denegado.exception.status_code, 403)

        permitido = service.solicitar_traspaso(
            self.session,
            self.encargado,
            self._data(
                origen_id=self.s2.id,
                destino_id=self.s1.id,
                cantidad=1,
                confirmar_stock_suficiente=True,
            ),
        )
        self.assertEqual(permitido["destino_id"], self.s1.id)
        self.assertEqual(permitido["solicitante_rol"], RolNombre.ENCARGADO)

    def test_validaciones_de_origen_destino_y_stock(self) -> None:
        with self.assertRaises(HTTPException) as iguales:
            service.solicitar_traspaso(
                self.session,
                self.cliente,
                self._data(destino_id=self.s1.id),
            )
        self.assertEqual(iguales.exception.status_code, 422)

        with self.assertRaises(HTTPException) as inexistente:
            service.solicitar_traspaso(
                self.session,
                self.cliente,
                self._data(destino_id=99999),
            )
        self.assertEqual(inexistente.exception.status_code, 404)

        with self.assertRaises(HTTPException) as insuficiente:
            service.solicitar_traspaso(
                self.session, self.cliente, self._data(cantidad=50)
            )
        self.assertEqual(insuficiente.exception.status_code, 409)
        self.assertEqual(
            insuficiente.exception.detail["codigo"], "STOCK_INSUFICIENTE"
        )
        self.assertEqual(insuficiente.exception.detail["stock_disponible"], 10)

        with self.assertRaises(HTTPException) as sin_stock:
            service.solicitar_traspaso(
                self.session,
                self.cliente,
                self._data(variante_id=self.variante_sin_stock.id),
            )
        self.assertEqual(sin_stock.exception.status_code, 409)
        self.assertEqual(
            sin_stock.exception.detail["codigo"], "SIN_STOCK_ORIGEN"
        )

    def test_confirmacion_si_el_destino_ya_tiene_stock(self) -> None:
        with self.assertRaises(HTTPException) as confirmacion:
            service.solicitar_traspaso(
                self.session, self.cliente, self._data(cantidad=2)
            )
        self.assertEqual(confirmacion.exception.status_code, 409)
        self.assertEqual(
            confirmacion.exception.detail["codigo"],
            "CONFIRMACION_STOCK_DESTINO",
        )
        self.assertTrue(confirmacion.exception.detail["requiere_confirmacion"])

        salida = service.solicitar_traspaso(
            self.session,
            self.cliente,
            self._data(cantidad=2, confirmar_stock_suficiente=True),
        )
        self.assertEqual(salida["estado"], EstadoTraspaso.PENDIENTE)

    def test_no_duplica_pendiente_y_permite_consultar(self) -> None:
        primera = service.solicitar_traspaso(
            self.session, self.cliente, self._data()
        )
        with self.assertRaises(HTTPException) as duplicada:
            service.solicitar_traspaso(
                self.session, self.cliente, self._data()
            )
        self.assertEqual(duplicada.exception.status_code, 409)
        self.assertEqual(
            duplicada.exception.detail["codigo"],
            "SOLICITUD_PENDIENTE_EXISTENTE",
        )
        self.assertEqual(
            duplicada.exception.detail["codigo_solicitud"], primera["codigo"]
        )

        consulta = service.consultar_traspaso(
            self.session, self.cliente, primera["codigo"].lower()
        )
        self.assertEqual(consulta["id"], primera["id"])

        with self.assertRaises(HTTPException) as ajeno:
            service.consultar_traspaso(
                self.session, self.otro_cliente, primera["codigo"]
            )
        self.assertEqual(ajeno.exception.status_code, 403)

        como_encargado = service.consultar_traspaso(
            self.session, self.encargado, primera["codigo"]
        )
        self.assertEqual(como_encargado["id"], primera["id"])

    def test_aislamiento_por_empresa(self) -> None:
        resultado = service.buscar_variantes(
            self.session, self.cliente_b, q="TRP-A"
        )
        self.assertEqual(resultado["total"], 0)

        with self.assertRaises(HTTPException) as cruzada:
            service.solicitar_traspaso(
                self.session,
                self.cliente,
                self._data(variante_id=self.variante_b.id),
            )
        self.assertEqual(cruzada.exception.status_code, 404)

        opciones_b = service.preparar_formulario(self.session, self.cliente_b)
        self.assertEqual(
            [s["id"] for s in opciones_b["sucursales"]], [self.s_b.id]
        )

    def test_cu25_listar_pendientes(self) -> None:
        service.solicitar_traspaso(self.session, self.cliente, self._data())
        pendientes, total = service.listar_solicitudes(
            self.session, self.admin, estado=EstadoTraspaso.PENDIENTE
        )
        self.assertEqual(total, 1)
        self.assertEqual(pendientes[0]["estado"], EstadoTraspaso.PENDIENTE)

    def test_cu25_aprobar_traspaso_exitoso(self) -> None:
        creada = service.solicitar_traspaso(
            self.session, self.cliente, self._data(cantidad=3)
        )
        decision = SolicitudTraspasoDecisionIn(
            decision="APROBAR",
            observacion_motivo="Aprobado por gerencia",
        )
        resultado = service.procesar_decision(
            self.session, self.admin, creada["id"], decision
        )
        self.assertEqual(resultado["estado"], EstadoTraspaso.APROBADA)
        self.assertEqual(resultado["admin_id"], self.admin.id)
        self.assertEqual(resultado["observacion_motivo"], "Aprobado por gerencia")

        inv = self.session.exec(
            select(Inventario).where(
                Inventario.variante_id == self.variante.id,
                Inventario.sucursal_id == self.s1.id,
            )
        ).first()
        self.assertEqual(inv.cantidad_disponible, 7)
        self.assertEqual(inv.cantidad_reservada, 3)

    def test_cu25_rechazar_traspaso_exitoso(self) -> None:
        creada = service.solicitar_traspaso(
            self.session, self.cliente, self._data(cantidad=3)
        )
        decision = SolicitudTraspasoDecisionIn(
            decision="RECHAZAR",
            observacion_motivo="No hay presupuesto para flete",
        )
        resultado = service.procesar_decision(
            self.session, self.admin, creada["id"], decision
        )
        self.assertEqual(resultado["estado"], EstadoTraspaso.RECHAZADA)
        self.assertEqual(resultado["admin_id"], self.admin.id)
        self.assertEqual(resultado["observacion_motivo"], "No hay presupuesto para flete")

        inv = self.session.exec(
            select(Inventario).where(
                Inventario.variante_id == self.variante.id,
                Inventario.sucursal_id == self.s1.id,
            )
        ).first()
        self.assertEqual(inv.cantidad_disponible, 10)
        self.assertEqual(inv.cantidad_reservada, 0)

    def test_cu25_rechazo_sin_motivo_falla(self) -> None:
        creada = service.solicitar_traspaso(self.session, self.cliente, self._data())
        decision = SolicitudTraspasoDecisionIn(decision="RECHAZAR", observacion_motivo=None)
        with self.assertRaises(HTTPException) as ctx:
            service.procesar_decision(self.session, self.admin, creada["id"], decision)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["codigo"], "MOTIVO_RECHAZO_REQUERIDO")

    def test_cu25_aprobar_sin_stock_suficiente_falla(self) -> None:
        creada = service.solicitar_traspaso(self.session, self.cliente, self._data(cantidad=3))
        inv = self.session.exec(
            select(Inventario).where(
                Inventario.variante_id == self.variante.id,
                Inventario.sucursal_id == self.s1.id,
            )
        ).first()
        inv.cantidad_disponible = 1
        self.session.add(inv)
        self.session.commit()

        decision = SolicitudTraspasoDecisionIn(decision="APROBAR")
        with self.assertRaises(HTTPException) as ctx:
            service.procesar_decision(self.session, self.admin, creada["id"], decision)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["codigo"], "SIN_STOCK_SUFICIENTE")

    def test_cu25_solicitud_ya_procesada_falla(self) -> None:
        creada = service.solicitar_traspaso(self.session, self.cliente, self._data())
        decision = SolicitudTraspasoDecisionIn(decision="APROBAR")
        service.procesar_decision(self.session, self.admin, creada["id"], decision)

        with self.assertRaises(HTTPException) as ctx:
            service.procesar_decision(self.session, self.admin, creada["id"], decision)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["codigo"], "SOLICITUD_YA_PROCESADA")

    def test_administrador_no_puede_crear_traspaso(self) -> None:
        """Verifica que un Administrador (u otro rol no autorizado) recibe 403 Forbidden al intentar crear un traspaso."""
        with self.assertRaises(HTTPException) as ctx:
            service.solicitar_traspaso(self.session, self.admin, self._data())
        self.assertEqual(ctx.exception.status_code, 403)

    def test_cu25_procesar_decision_por_codigo_string(self) -> None:
        """Verifica que el administrador puede aprobar o rechazar usando el código textual (string) de la solicitud."""
        creada = service.solicitar_traspaso(self.session, self.cliente, self._data())
        decision = SolicitudTraspasoDecisionIn(decision="APROBAR")
        resultado = service.procesar_decision(
            self.session, self.admin, creada["codigo"], decision
        )
        self.assertEqual(resultado["estado"], EstadoTraspaso.APROBADA)
        self.assertEqual(resultado["codigo"], creada["codigo"])

    def test_normalizacion_y_filtrado_estados(self) -> None:
        """Verifica que el filtrado por estado tolera plurales, singulares y case-insensitivity."""
        creada = service.solicitar_traspaso(self.session, self.cliente, self._data())
        service.procesar_decision(
            self.session, self.admin, creada["id"], SolicitudTraspasoDecisionIn(decision="APROBAR")
        )

        items, total = service.listar_solicitudes(self.session, self.admin, estado="aprobados")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["estado"], EstadoTraspaso.APROBADA)

        items2, total2 = service.listar_solicitudes(self.session, self.admin, estado="APROBADA")
        self.assertEqual(total2, 1)
        self.assertEqual(items2[0]["estado"], EstadoTraspaso.APROBADA)


if __name__ == "__main__":
    unittest.main()
