"""uq_egreso_activo_por_internacion

Revision ID: 3504e9c67f84
Revises: e8546bf296d3
Create Date: 2026-06-11

Garantiza que una internación no puede tener más de un egreso activo
simultáneamente. Complementa uq_egreso_activo_por_cama (sobre cama_gestion_id)
y respalda el endpoint GET /internaciones/{id}/egreso-activo.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '3504e9c67f84'
down_revision: Union[str, Sequence[str], None] = 'e8546bf296d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_egreso_activo_por_internacion',
        'egresos',
        ['internacion_local_id'],
        unique=True,
        postgresql_where=sa.text("estado IN ('info', 'bloqueado', 'egreso_admin')"),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_egreso_activo_por_internacion',
        table_name='egresos',
        postgresql_where=sa.text("estado IN ('info', 'bloqueado', 'egreso_admin')"),
    )
