"""CU35 centro de notificaciones: modulo en notificacion

Revision ID: cu35notif00001
Revises: d9628f62b603
Create Date: 2026-11-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'cu35notif00001'
down_revision: Union[str, Sequence[str], None] = 'd9628f62b603'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notificacion', sa.Column('modulo', sqlmodel.sql.sqltypes.AutoString(length=30), nullable=True))
    op.create_index(op.f('ix_notificacion_modulo'), 'notificacion', ['modulo'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notificacion_modulo'), table_name='notificacion')
    op.drop_column('notificacion', 'modulo')
