"""datos_traslado en egreso

Revision ID: c7a8b9d0e1f2
Revises: 900995c0c463
Create Date: 2026-06-12

Agrega campo JSONB nullable datos_traslado en la tabla egresos.
Almacena datos logísticos del traslado (ambulancia/derivacion):
destino, prestador, requerimientos (oxígeno, médico a bordo, etc.).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c7a8b9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "900995c0c463"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "egresos",
        sa.Column("datos_traslado", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("egresos", "datos_traslado")
