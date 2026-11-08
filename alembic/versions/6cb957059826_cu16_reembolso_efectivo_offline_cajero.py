"""cu16 reembolso efectivo offline cajero

Revision ID: 6cb957059826
Revises: cu35notif00001
Create Date: 2026-11-08 02:45:36.034805

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '6cb957059826'
down_revision: Union[str, Sequence[str], None] = 'cu35notif00001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nota: el autogenerate también detectó un cambio de tipo en
    # `notificacion.fecha` y un índice faltante en
    # `solicitud_traspaso.despachador_id` — son drift de un commit anterior
    # (CU35/36), no de este cambio. Se dejan fuera a propósito; no tocarlos
    # acá para no mezclar cosas sin probar.
    op.create_table('reembolso_efectivo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('venta_id', sa.Integer(), nullable=False),
    sa.Column('pago_id', sa.Integer(), nullable=False),
    sa.Column('monto', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('estado', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('cajero_confirma_id', sa.Integer(), nullable=True),
    sa.Column('nota', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
    sa.Column('fecha_creacion', sa.DateTime(), nullable=False),
    sa.Column('fecha_entregado', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['cajero_confirma_id'], ['usuario.id'], ),
    sa.ForeignKeyConstraint(['pago_id'], ['pago.id'], ),
    sa.ForeignKeyConstraint(['venta_id'], ['venta.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('venta_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('reembolso_efectivo')
