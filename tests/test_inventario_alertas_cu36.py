"""Pruebas de alertas y sugerencias de reposición — CU36."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.modules.catalogo.models import Categoria, Color, Talla
from app.modules.identidad.models import Rol, RolNombre, Usuario
from app.modules.inventario import service
from app.modules.inventario.models import Inventario
from app.modules.productos.models import Producto, ProductoVariante
from app.modules.promociones.models import Promocion, PromocionProducto, PromocionVariante
from app.modules.proveedores.models import Proveedor
from app.modules.reservas.models import Reserva, ReservaDetalle
from app.modules.saas.models import Empresa
from app.modules.sucursales.models import Sucursal
from app.modules.ventas.models import EstadoVenta, Venta, VentaDetalle


class AlertasCU36Test:
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
            Reserva.__table__,
            ReservaDetalle.__table__,
            Promocion.__table__,
            PromocionProducto.__table__,
            PromocionVariante.__table__,
            Venta.__table__,
            VentaDetalle.__table__,
        ]
        SQLModel.metadata.create_all(self.engine, tables=self.tablas)
        self.session = Session(self.engine)

        self.rol_admin = Rol(nombre=RolNombre.ADMINISTRADOR)
        self.rol_enc = Rol(nombre=RolNombre.ENCARGADO)
        self.rol_cliente = Rol(nombre=RolNombre.CLIENTE)
        self.session.add_all([self.rol_admin, self.rol_enc, self.rol_cliente])
        self.session.flush()

        self.empresa = Empresa(identificador="emp-a", nombre="Empresa A")
        self.empresa_b = Empresa(identificador="emp-b", nombre="Empresa B")
        self.session.add_all([self.empresa, self.empresa_b])
        self.session.flush()

        self.s1 = Sucursal(
            empresa_id=self.empresa.id, nombre="Centro",
            ciudad="SC", direccion="Av 1",
        )
        self.s2 = Sucursal(
            empresa_id=self.empresa.id, nombre="Norte",
            ciudad="LP", direccion="Av 2",
        )
        self.s_b = Sucursal(
            empresa_id=self.empresa_b.id, nombre="B",
            ciudad="CB", direccion="Av 3",
        )
        self.session.add_all([self.s1, self.s2, self.s_b])
        self.session.flush()

        self.admin = Usuario(
            nombre="A", apellido="A", email="a@x.com",
            password_hash="x", empresa_id=self.empresa.id, rol_id=self.rol_admin.id,
        )
        self.encargado = Usuario(
            nombre="E", apellido="E", email="e@x.com",
            password_hash="x", empresa_id=self.empresa.id,
            rol_id=self.rol_enc.id, sucursal_id=self.s1.id,
        )
        self.cliente = Usuario(
            nombre="C", apellido="C", email="c@x.com",
            password_hash="x", empresa_id=self.empresa.id, rol_id=self.rol_cliente.id,
        )
        self.session.add_all([self.admin, self.encargado, self.cliente])
        self.session.flush()

        self.cat = Categoria(empresa_id=self.empresa.id, nombre="Ropa")
        self.talla = Talla(empresa_id=self.empresa.id, valor="M", tipo="Ropa")
        self.color = Color(empresa_id=self.empresa.id, nombre="Negro", codigo_hex="#000000")
        self.prov = Proveedor(empresa_id=self.empresa.id, nombre_empresa="Prov")
        self.session.add_all([self.cat, self.talla, self.color, self.prov])
        self.session.flush()

        self.prod = Producto(
            empresa_id=self.empresa.id, nombre="Polera",
            categoria_id=self.cat.id, proveedor_id=self.prov.id,
            precio_base=Decimal("100.00"),
        )
        self.session.add(self.prod)
        self.session.flush()

        self.var_baja = ProductoVariante(
            empresa_id=self.empresa.id, producto_id=self.prod.id,
            talla_id=self.talla.id, color_id=self.color.id, sku="POL-M-NEGRO",
        )
        self.var_ok = ProductoVariante(
            empresa_id=self.empresa.id, producto_id=self.prod.id,
            talla_id=self.talla.id, color_id=self.color.id, sku="POL-M-2",
        )
        # evitar unique por talla/color: cambiamos color otra vez
        self.session.add(self.var_baja)
        self.session.flush()
        color2 = Color(empresa_id=self.empresa.id, nombre="Blanco", codigo_hex="#FFFFFF")
        self.session.add(color2)
        self.session.flush()
        self.var_ok.color_id = color2.id
        self.var_ok.sku = "POL-M-BLANCO"
        self.session.add(self.var_ok)
        self.session.flush()

        self.session.add_all([
            Inventario(variante_id=self.var_baja.id, sucursal_id=self.s1.id, cantidad_disponible=1, cantidad_reservada=0),
            Inventario(variante_id=self.var_ok.id, sucursal_id=self.s1.id, cantidad_disponible=50),
            Inventario(variante_id=self.var_baja.id, sucursal_id=self.s2.id, cantidad_disponible=0),
        ])
        self.session.flush()

        # Venta pagada hace 10 días: 30 unidades de var_baja en s1 -> demanda 1/día en 30 días
        venta = Venta(
            cliente_id=self.cliente.id, sucursal_id=self.s1.id,
            total=Decimal("3000.00"), estado=EstadoVenta.PAGADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(days=10),
        )
        self.session.add(venta)
        self.session.flush()
        self.session.add(VentaDetalle(
            venta_id=venta.id, variante_id=self.var_baja.id,
            sucursal_id=self.s1.id, cantidad=30, precio_unitario=Decimal("100.00"),
        ))
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        SQLModel.metadata.drop_all(self.engine, tables=list(reversed(self.tablas)))

    def test_alerta_stock_bajo_con_sugerencia(self) -> None:
        r = service.consultar_alertas(
            self.session, umbral=3, empresa_id=self.empresa.id,
            sucursal_permitida=None,
        )
        assert r["total"] == 2  # var_baja en s1 (1) y s2 (0), la de 50 no
        assert r["agotados"] == 1
        assert r["stock_bajo"] == 1
        primera = r["items"][0]
        assert primera["estado"] == "AGOTADO"  # agotados primero (s2)
        s1 = next(i for i in r["items"] if i["sucursal_id"] == self.s1.id)
        assert s1["rotacion_periodo"] == 30
        assert s1["demanda_diaria_estimada"] == 1.0  # 30 / 30 días
        assert s1["dias_stock_restantes"] == 1.0
        assert s1["cantidad_a_reponer"] == 29  # ceil(1*30) - 1
        assert s1["sugerencia"] == "Reponer 29 unidades"

    def test_encargado_solo_su_sucursal(self) -> None:
        r = service.consultar_alertas(
            self.session, umbral=3, empresa_id=self.empresa.id,
            sucursal_permitida=self.s1.id, sucursal_id=self.s2.id,
        )
        assert r["total"] == 1
        assert r["items"][0]["sucursal_id"] == self.s1.id

    def test_sin_historial_sin_datos_demanda(self) -> None:
        r = service.consultar_alertas(
            self.session, umbral=3, empresa_id=self.empresa.id,
            fecha_desde=None, fecha_hasta=None,
            sucursal_permitida=self.s2.id,
        )
        assert r["total"] == 1
        assert r["items"][0]["sugerencia"] == "Sin datos de demanda"
        assert r["items"][0]["cantidad_a_reponer"] is None
        assert r["sin_datos_demanda"] is True

    def test_sin_alertas_total_cero(self) -> None:
        r = service.consultar_alertas(
            self.session, umbral=0, empresa_id=self.empresa.id,
            sucursal_permitida=self.s1.id, q="POL-M-BLANCO",
        )
        assert r["total"] == 0
        assert r["items"] == []

    def test_sucursal_otra_empresa_404(self) -> None:
        try:
            service.consultar_alertas(
                self.session, sucursal_id=self.s_b.id, empresa_id=self.empresa.id,
            )
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("debió fallar")

    def test_parametros_invalidos_422(self) -> None:
        for kwargs in ({"umbral": -1}, {"fecha_desde": None}):
            try:
                if "umbral" in kwargs:
                    service.consultar_alertas(self.session, umbral=-1, empresa_id=self.empresa.id)
                else:
                    from datetime import date
                    service.consultar_alertas(
                        self.session, fecha_desde=date(2026, 11, 10),
                        fecha_hasta=date(2026, 11, 1), empresa_id=self.empresa.id,
                    )
            except HTTPException as exc:
                assert exc.status_code == 422
            else:
                raise AssertionError("debió fallar")


def test_cu36_alertas():
    t = AlertasCU36Test()
    t.setUp()
    try:
        t.test_alerta_stock_bajo_con_sugerencia()
        t.test_encargado_solo_su_sucursal()
        t.test_sin_historial_sin_datos_demanda()
        t.test_sin_alertas_total_cero()
        t.test_sucursal_otra_empresa_404()
        t.test_parametros_invalidos_422()
    finally:
        t.tearDown()
