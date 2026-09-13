"""Record which defect axes an attack run's environment exhibits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_run_defect_axes"
down_revision: str | None = "0006_run_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defaults to the empty array so every existing row keeps its old meaning -- an empty
    # axis list reads as "inherit the whole set the sandbox version carries", which is
    # exactly what those runs were executed under.
    op.add_column(
        "attack_run",
        sa.Column(
            "defect_axes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="control",
    )


def downgrade() -> None:
    op.drop_column("attack_run", "defect_axes", schema="control")
