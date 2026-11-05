"""CU24: solicitar traspaso de prenda (código anterior CU61)

Revision ID: c65ccc34ab42
Revises: 414523cfd3bb
Create Date: 2026-11-01 08:09:11.915661

Crea la tabla de solicitudes de traspaso. La solicitud nace en PENDIENTE y no
modifica inventario: el movimiento de stock se hará al ejecutar el traspaso en
un caso de uso posterior.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c65ccc34ab42'
down_revision: Union[str, Sequence[str], None] = '414523cfd3bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'solicitud_traspaso',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column(
            'codigo', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=False
        ),
        sa.Column('variante_id', sa.Integer(), nullable=False),
        sa.Column('origen_id', sa.Integer(), nullable=False),
        sa.Column('destino_id', sa.Integer(), nullable=False),
        sa.Column('cantidad', sa.Integer(), nullable=False),
        sa.Column(
            'motivo', sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True
        ),
        sa.Column(
            'estado', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False
        ),
        sa.Column('solicitante_id', sa.Integer(), nullable=False),
        sa.Column('fecha_solicitud', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresa.id'], ),
        sa.ForeignKeyConstraint(['variante_id'], ['producto_variante.id'], ),
        sa.ForeignKeyConstraint(['origen_id'], ['sucursal.id'], ),
        sa.ForeignKeyConstraint(['destino_id'], ['sucursal.id'], ),
        sa.ForeignKeyConstraint(['solicitante_id'], ['usuario.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'empresa_id', 'codigo', name='uq_traspaso_empresa_codigo'
        ),
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_empresa_id'),
        'solicitud_traspaso',
        ['empresa_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_codigo'),
        'solicitud_traspaso',
        ['codigo'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_variante_id'),
        'solicitud_traspaso',
        ['variante_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_origen_id'),
        'solicitud_traspaso',
        ['origen_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_destino_id'),
        'solicitud_traspaso',
        ['destino_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_estado'),
        'solicitud_traspaso',
        ['estado'],
        unique=False,
    )
    op.create_index(
        op.f('ix_solicitud_traspaso_solicitante_id'),
        'solicitud_traspaso',
        ['solicitante_id'],
        unique=False,
    )
    op.create_index(
        'ix_traspaso_pendiente',
        'solicitud_traspaso',
        ['empresa_id', 'variante_id', 'origen_id', 'destino_id', 'estado'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_traspaso_pendiente', table_name='solicitud_traspaso')
    op.drop_index(
        op.f('ix_solicitud_traspaso_solicitante_id'),
        table_name='solicitud_traspaso',
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_estado'), table_name='solicitud_traspaso'
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_destino_id'), table_name='solicitud_traspaso'
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_origen_id'), table_name='solicitud_traspaso'
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_variante_id'), table_name='solicitud_traspaso'
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_codigo'), table_name='solicitud_traspaso'
    )
    op.drop_index(
        op.f('ix_solicitud_traspaso_empresa_id'), table_name='solicitud_traspaso'
    )
    op.drop_table('solicitud_traspaso')
