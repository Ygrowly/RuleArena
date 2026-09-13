"""Give each run its own set of defect axes.

A defect axis is one way the measured implementation can deviate from the frozen
RuleSpec. With a single `vulnerable` flag every case in a scenario shared an environment
exhibiting all of them, so a path could trip another case's defect and be scored against
a label it never touched. Null keeps the old meaning: inherit the version's whole set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_defect_axes"
down_revision: str | None = "0002_commerce_sandbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_spaces",
        sa.Column("defect_axes", sa.JSON(), nullable=True),
        schema="sandbox",
    )


def downgrade() -> None:
    op.drop_column("run_spaces", "defect_axes", schema="sandbox")
