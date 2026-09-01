"""Add durable trace and append-only benchmark facts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_evaluation_observability"
down_revision: str | None = "0002_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_step",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attack_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.attack_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "strategy_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.strategy_run.id", ondelete="CASCADE"),
        ),
        sa.Column("step_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "rule_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.rule_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_config_hash", sa.String(64)),
        sa.Column("prompt_version", sa.Text()),
        sa.Column("action_summary", postgresql.JSONB(), nullable=False),
        sa.Column("tool_result_summary", postgresql.JSONB(), nullable=False),
        sa.Column("before_state_hash", sa.String(64)),
        sa.Column("after_state_hash", sa.String(64)),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost", sa.Numeric(18, 8), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_type", sa.Text()),
        sa.Column(
            "parent_trace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.trace_step.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("step_id >= 0", name="ck_trace_step_non_negative"),
        sa.CheckConstraint(
            "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
            "AND cost >= 0 AND retry_count >= 0",
            name="ck_trace_usage_non_negative",
        ),
        schema="control",
    )
    op.create_index(
        "ix_trace_step_run_step",
        "trace_step",
        ["attack_run_id", "step_id", "created_at"],
        schema="control",
    )
    op.create_table(
        "benchmark_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("benchmark_version", sa.Text(), nullable=False),
        sa.Column("runtime_version", sa.Text(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("scenario_set_version", sa.Text(), nullable=False),
        sa.Column("sandbox_version", sa.Text(), nullable=False),
        sa.Column("oracle_version", sa.Text(), nullable=False),
        sa.Column("model_config_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("baseline", sa.Text(), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("repetitions", sa.Integer(), nullable=False),
        sa.Column("suite", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("raw_runs", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "baseline IN ('RANDOM','BFS','SINGLE_AGENT','MULTI_STRATEGY')",
            name="ck_benchmark_baseline",
        ),
        sa.CheckConstraint("suite IN ('development','hidden')", name="ck_benchmark_suite"),
        sa.CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED')", name="ck_benchmark_status"
        ),
        sa.CheckConstraint("repetitions > 0", name="ck_benchmark_repetitions"),
        schema="control",
    )
    op.create_index(
        "ix_benchmark_release_lookup",
        "benchmark_run",
        [
            "benchmark_version",
            "runtime_version",
            "sandbox_version",
            "oracle_version",
            "model_config_hash",
            "prompt_version",
            "baseline",
            "suite",
            "started_at",
        ],
        schema="control",
    )
    op.create_table(
        "benchmark_case_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "benchmark_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.benchmark_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("baseline", sa.Text(), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("attack_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("failure_kind", sa.Text(), nullable=False),
        sa.Column("confirmed_invariant_ids", postgresql.JSONB(), nullable=False),
        sa.Column("replayed_candidates", sa.Integer(), nullable=False),
        sa.Column("confirmed_candidates", sa.Integer(), nullable=False),
        sa.Column("replay_attempts", sa.Integer(), nullable=False),
        sa.Column("replay_successes", sa.Integer(), nullable=False),
        sa.Column("compile_attempted", sa.Boolean(), nullable=False),
        sa.Column("rule_spec_schema_valid", sa.Boolean()),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("benchmark_run_id", "case_id", "repetition"),
        sa.CheckConstraint("repetition > 0", name="ck_benchmark_case_repetition"),
        sa.CheckConstraint(
            "replayed_candidates >= 0 AND confirmed_candidates >= 0 "
            "AND replay_attempts >= 0 AND replay_successes >= 0",
            name="ck_benchmark_case_counts",
        ),
        schema="control",
    )
    op.create_index(
        "ix_benchmark_case_run_source",
        "benchmark_case_run",
        ["benchmark_run_id", "case_id", "repetition"],
        schema="control",
    )
    op.execute(
        """
        CREATE FUNCTION control.reject_benchmark_run_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'BenchmarkRun is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_run_append_only
        BEFORE UPDATE OR DELETE ON control.benchmark_run
        FOR EACH ROW EXECUTE FUNCTION control.reject_benchmark_run_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS control.reject_benchmark_run_mutation() CASCADE")
    op.drop_index(
        "ix_benchmark_case_run_source",
        table_name="benchmark_case_run",
        schema="control",
    )
    op.drop_table("benchmark_case_run", schema="control")
    op.drop_index("ix_benchmark_release_lookup", table_name="benchmark_run", schema="control")
    op.drop_table("benchmark_run", schema="control")
    op.drop_index("ix_trace_step_run_step", table_name="trace_step", schema="control")
    op.drop_table("trace_step", schema="control")
