"""Create the control migration baseline without business tables."""

from collections.abc import Sequence

revision: str = "0001_control"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

