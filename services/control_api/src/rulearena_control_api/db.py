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

trace_step = sa.Table(
    "trace_step",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "attack_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("attack_run.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "strategy_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("strategy_run.id", ondelete="CASCADE"),
    ),
    sa.Column("step_id", sa.BigInteger(), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column(
        "rule_version_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("rule_version.id", ondelete="RESTRICT"),
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
        sa.ForeignKey("trace_step.id", ondelete="SET NULL"),
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("step_id >= 0", name="ck_trace_step_non_negative"),
    sa.CheckConstraint(
        "latency_ms >= 0 AND input_tokens >= 0 AND output_tokens >= 0 "
        "AND cost >= 0 AND retry_count >= 0",
        name="ck_trace_usage_non_negative",
    ),
)
sa.Index(
    "ix_trace_step_run_step",
    trace_step.c.attack_run_id,
    trace_step.c.step_id,
    trace_step.c.created_at,
)

benchmark_run = sa.Table(
    "benchmark_run",
    metadata,
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
)
sa.Index(
    "ix_benchmark_release_lookup",
    benchmark_run.c.benchmark_version,
    benchmark_run.c.runtime_version,
    benchmark_run.c.sandbox_version,
    benchmark_run.c.oracle_version,
    benchmark_run.c.model_config_hash,
    benchmark_run.c.prompt_version,
    benchmark_run.c.baseline,
    benchmark_run.c.suite,
    benchmark_run.c.started_at,
)

benchmark_case_run = sa.Table(
    "benchmark_case_run",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "benchmark_run_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("benchmark_run.id", ondelete="CASCADE"),
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
)
sa.Index(
    "ix_benchmark_case_run_source",
    benchmark_case_run.c.benchmark_run_id,
    benchmark_case_run.c.case_id,
    benchmark_case_run.c.repetition,
)
