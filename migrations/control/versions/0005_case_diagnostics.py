"""Add per-strategy search diagnostics to benchmark case runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_case_diagnostics"
down_revision: str | None = "0004_append_only_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable: baselines without agent strategies (Random, BFS) have no per-strategy
    # cause of stopping, and historical rows predate the column.
    op.add_column(
        "benchmark_case_run",
        sa.Column("strategy_diagnostics", postgresql.JSONB(), nullable=True),
        schema="control",
    )


def downgrade() -> None:
    op.drop_column("benchmark_case_run", "strategy_diagnostics", schema="control")
