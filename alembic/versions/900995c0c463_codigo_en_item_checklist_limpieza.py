"""codigo_en_item_checklist_limpieza

Agrega ``codigo`` (VARCHAR 20, NOT NULL) a ``item_checklist_limpieza`` para
dar identidad estable a cada ítem del catálogo de limpieza terminal sin
depender del label (texto de usuario). Los dos códigos en uso son:
  EJECUCION  — ejecuta la tercerizada (LIMPIEZA o HOTELERIA)
  SUPERVISION — control institucional (solo HOTELERIA o override ADMISION)

Revision ID: 900995c0c463
Revises: 3504e9c67f84
Create Date: 2026-06-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "900995c0c463"
down_revision: Union[str, Sequence[str], None] = "3504e9c67f84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "item_checklist_limpieza",
        sa.Column("codigo", sa.String(20), nullable=False, server_default=""),
    )
    op.alter_column("item_checklist_limpieza", "codigo", server_default=None)


def downgrade() -> None:
    op.drop_column("item_checklist_limpieza", "codigo")
