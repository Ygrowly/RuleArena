"""Add immutable policy versions and durable agent runtime state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_agent_runtime"
down_revision: str | None = "0001_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

status_values = (
    "DRAFT",
    "NEEDS_CONFIRMATION",
    "READY",
    "SEARCHING",
    "REPLAYING",
    "RECOVERING",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "FAILED",
    "COMPLETED",
)
outcome_values = (
    "CONFIRMED_VIOLATION",
    "UNCONFIRMED_CANDIDATE",
    "NO_VIOLATION_WITHIN_BUDGET",
    "AMBIGUOUS_POLICY",
    "UNSUPPORTED_RULE",
    "INFRA_FAILED",
    "CANCELLED",
)


def upgrade() -> None:
    op.create_table(
        "policy_pack",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="control",
    )
    op.create_table(
        "llm_call",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.policy_pack.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("seed", sa.BigInteger()),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("cost", sa.Numeric(18, 8), nullable=False),
        sa.Column("response_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="control",
    )
    op.create_table(
        "rule_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.policy_pack.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Text(), nullable=False),
        sa.Column("rule_spec", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_pack_id", "version"),
        sa.UniqueConstraint("policy_pack_id", "content_hash"),
        schema="control",
    )
    op.create_table(
        "policy_compile",
        sa.Column(
            "policy_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.policy_pack.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("template_id", sa.Text(), nullable=False),
        sa.Column("rule_spec", postgresql.JSONB()),
        sa.Column("questions", postgresql.JSONB(), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False),
        sa.Column(
            "llm_call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.llm_call.id", ondelete="SET NULL"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="control",
    )
    op.create_table(
        "attack_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_key", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "rule_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.rule_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scenario_version_id", sa.Text(), nullable=False),
        sa.Column("sandbox_version", sa.Text(), nullable=False),
        sa.Column("oracle_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text()),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(f"status IN {status_values!r}", name="ck_attack_run_status"),
        sa.CheckConstraint(
            f"outcome IS NULL OR outcome IN {outcome_values!r}", name="ck_attack_run_outcome"
        ),
        schema="control",
    )
    op.create_table(
        "strategy_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attack_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.attack_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("strategy_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("attack_run_id", "strategy_type"),
        sa.CheckConstraint(
            "strategy_type IN ('VALUE_FLOW','LIFECYCLE','BOUNDARY')",
            name="ck_strategy_type",
        ),
        schema="control",
    )
    op.create_table(
        "checkpoint",
        sa.Column(
            "strategy_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.strategy_run.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="control",
    )
    op.create_table(
        "runtime_event",
        sa.Column("cursor", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "attack_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.attack_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="control",
    )
    op.create_index(
        "ix_runtime_event_run_cursor",
        "runtime_event",
        ["attack_run_id", "cursor"],
        schema="control",
    )
    op.create_table(
        "counterexample",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attack_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("control.attack_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(128), nullable=False),
        sa.Column("invariant_id", sa.Text(), nullable=False),
        sa.Column("original_actions", postgresql.JSONB(), nullable=False),
        sa.Column("minimized_actions", postgresql.JSONB(), nullable=False),
        sa.Column("replay_run_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attack_run_id", "candidate_key"),
        schema="control",
    )
    op.execute(
        """
        CREATE FUNCTION control.reject_rule_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'RuleVersion is immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER rule_version_immutable
        BEFORE UPDATE OR DELETE ON control.rule_version
        FOR EACH ROW EXECUTE FUNCTION control.reject_rule_version_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS control.reject_rule_version_mutation() CASCADE")
    op.drop_table("counterexample", schema="control")
    op.drop_index("ix_runtime_event_run_cursor", table_name="runtime_event", schema="control")
    op.drop_table("runtime_event", schema="control")
    op.drop_table("checkpoint", schema="control")
    op.drop_table("strategy_run", schema="control")
    op.drop_table("attack_run", schema="control")
    op.drop_table("policy_compile", schema="control")
    op.drop_table("rule_version", schema="control")
    op.drop_table("llm_call", schema="control")
    op.drop_table("policy_pack", schema="control")
