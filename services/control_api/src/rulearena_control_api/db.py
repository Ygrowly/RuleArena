from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

policy_pack = sa.Table(
    "policy_pack",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("template_id", sa.Text(), nullable=False),
    sa.Column("source_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

llm_call = sa.Table(
    "llm_call",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "policy_pack_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("policy_pack.id", ondelete="CASCADE"),
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
)

rule_version = sa.Table(
    "rule_version",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "policy_pack_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("policy_pack.id", ondelete="RESTRICT"),
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
)

policy_compile = sa.Table(
    "policy_compile",
    metadata,
    sa.Column(
        "policy_pack_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("policy_pack.id", ondelete="CASCADE"),
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
        sa.ForeignKey("llm_call.id", ondelete="SET NULL"),
    ),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

attack_run = sa.Table(
    "attack_run",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("job_key", sa.Text(), nullable=False, unique=True),
    sa.Column(
        "rule_version_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("rule_version.id", ondelete="RESTRICT"),
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
    sa.CheckConstraint(
        "status IN ('DRAFT','NEEDS_CONFIRMATION','READY','SEARCHING','REPLAYING',"
        "'RECOVERING','CANCEL_REQUESTED','CANCELLED','FAILED','COMPLETED')",
        name="ck_attack_run_status",
    ),
    sa.CheckConstraint(
        "outcome IS NULL OR outcome IN ('CONFIRMED_VIOLATION','UNCONFIRMED_CANDIDATE',"
        "'NO_VIOLATION_WITHIN_BUDGET','AMBIGUOUS_POLICY','UNSUPPORTED_RULE','INFRA_FAILED',"
        "'CANCELLED')",
        name="ck_attack_run_outcome",
    ),
)

strategy_run = sa.Table(
    "strategy_run",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "attack_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("attack_run.id", ondelete="CASCADE"),
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
)

checkpoint = sa.Table(
    "checkpoint",
    metadata,
    sa.Column(
        "strategy_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("strategy_run.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("version", sa.BigInteger(), nullable=False),
    sa.Column("state", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

runtime_event = sa.Table(
    "runtime_event",
    metadata,
    sa.Column("cursor", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column(
        "attack_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("attack_run.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("event_type", sa.Text(), nullable=False),
    sa.Column("data", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
sa.Index("ix_runtime_event_run_cursor", runtime_event.c.attack_run_id, runtime_event.c.cursor)

counterexample = sa.Table(
    "counterexample",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "attack_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("attack_run.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("candidate_key", sa.String(128), nullable=False),
    sa.Column("invariant_id", sa.Text(), nullable=False),
    sa.Column("original_actions", postgresql.JSONB(), nullable=False),
    sa.Column("minimized_actions", postgresql.JSONB(), nullable=False),
    sa.Column("replay_run_id", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("attack_run_id", "candidate_key"),
)
