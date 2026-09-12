"""Let a benchmark run leave RUNNING exactly once, freezing everything else."""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_run_lifecycle"
down_revision: str | None = "0005_case_diagnostics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Case facts are now written as each cell finishes, so a run must exist before its
    # cells and must be able to leave RUNNING afterwards. The relaxation is exact: only
    # the outcome columns may be filled in, only on that one transition. Identity and
    # configuration columns stay frozen, so a finished run still cannot be rewritten or
    # erased -- which is the property the append-only trigger exists to protect.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION control.reject_benchmark_run_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE'
             AND OLD.status = 'RUNNING'
             AND NEW.status IN ('COMPLETED', 'FAILED')
             AND (to_jsonb(OLD) - 'status' - 'raw_runs' - 'metrics' - 'finished_at')
               = (to_jsonb(NEW) - 'status' - 'raw_runs' - 'metrics' - 'finished_at')
          THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'BenchmarkRun is append-only';
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION control.reject_benchmark_run_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'BenchmarkRun is append-only';
        END;
        $$
        """
    )
