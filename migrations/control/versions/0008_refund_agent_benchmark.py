"""Store refund-agent benchmark runs, append-only like the search benchmark's.

One row per run. The search suite splits its case facts into a second table so a
multi-hour run can be resumed and its cells appended as they finish; the refund suite
takes minutes and has no resume path, so a per-ticket table would add schema without a
consumer. `raw_runs` carries every ticket fact the metrics were computed from, which is
what keeps them recomputable -- the property that actually matters.

The `mode` column is what makes the comparison a first-class fact rather than a naming
convention: `refund-verify` pairs the newest completed run of each mode and refuses to
compare two runs that were not configured the same way.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_refund_agent_benchmark"
down_revision: str | None = "0007_run_defect_axes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refund_benchmark_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("benchmark_version", sa.Text(), nullable=False),
        sa.Column("runtime_version", sa.Text(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("scenario_set_version", sa.Text(), nullable=False),
        sa.Column("sandbox_version", sa.Text(), nullable=False),
        sa.Column("oracle_version", sa.Text(), nullable=False),
        sa.Column("model_config_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("suite", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("raw_runs", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("mode IN ('BARE','GATED')", name="ck_refund_benchmark_mode"),
        sa.CheckConstraint("suite IN ('development','hidden')", name="ck_refund_suite"),
        sa.CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED')", name="ck_refund_status"
        ),
        sa.CheckConstraint("repetitions > 0", name="ck_refund_repetitions"),
        schema="control",
    )
    op.create_index(
        "ix_refund_benchmark_lookup",
        "refund_benchmark_run",
        [
            "benchmark_version",
            "runtime_version",
            "sandbox_version",
            "oracle_version",
            "model_config_hash",
            "prompt_version",
            "mode",
            "status",
            "started_at",
        ],
        schema="control",
    )
    op.execute(
        """
        CREATE FUNCTION control.reject_refund_benchmark_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'RefundBenchmarkRun is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER refund_benchmark_run_append_only
        BEFORE UPDATE OR DELETE ON control.refund_benchmark_run
        FOR EACH ROW EXECUTE FUNCTION control.reject_refund_benchmark_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS control.reject_refund_benchmark_mutation() CASCADE"
    )
    op.drop_index(
        "ix_refund_benchmark_lookup", table_name="refund_benchmark_run", schema="control"
    )
    op.drop_table("refund_benchmark_run", schema="control")
