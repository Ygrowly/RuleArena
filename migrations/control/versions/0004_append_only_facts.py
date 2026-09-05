"""Make trace and benchmark case facts append-only and remove cascading deletes."""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_append_only_facts"
down_revision: str | None = "0003_evaluation_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Cascading deletes would silently erase durable evidence; restrict instead.
    op.drop_constraint(
        "trace_step_attack_run_id_fkey",
        "trace_step",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trace_step_attack_run_id_fkey",
        "trace_step",
        "attack_run",
        ["attack_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "trace_step_strategy_run_id_fkey",
        "trace_step",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trace_step_strategy_run_id_fkey",
        "trace_step",
        "strategy_run",
        ["strategy_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "benchmark_case_run_benchmark_run_id_fkey",
        "benchmark_case_run",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "benchmark_case_run_benchmark_run_id_fkey",
        "benchmark_case_run",
        "benchmark_run",
        ["benchmark_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE FUNCTION control.reject_immutable_row_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER benchmark_case_run_append_only
        BEFORE UPDATE OR DELETE ON control.benchmark_case_run
        FOR EACH ROW EXECUTE FUNCTION control.reject_immutable_row_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trace_step_append_only
        BEFORE UPDATE OR DELETE ON control.trace_step
        FOR EACH ROW EXECUTE FUNCTION control.reject_immutable_row_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trace_step_append_only ON control.trace_step")
    op.execute(
        "DROP TRIGGER IF EXISTS benchmark_case_run_append_only ON control.benchmark_case_run"
    )
    op.execute("DROP FUNCTION IF EXISTS control.reject_immutable_row_mutation() CASCADE")
    op.drop_constraint(
        "benchmark_case_run_benchmark_run_id_fkey",
        "benchmark_case_run",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "benchmark_case_run_benchmark_run_id_fkey",
        "benchmark_case_run",
        "benchmark_run",
        ["benchmark_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "trace_step_strategy_run_id_fkey",
        "trace_step",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trace_step_strategy_run_id_fkey",
        "trace_step",
        "strategy_run",
        ["strategy_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "trace_step_attack_run_id_fkey",
        "trace_step",
        schema="control",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trace_step_attack_run_id_fkey",
        "trace_step",
        "attack_run",
        ["attack_run_id"],
        ["id"],
        source_schema="control",
        referent_schema="control",
        ondelete="CASCADE",
    )
