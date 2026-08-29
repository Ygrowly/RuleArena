"""Create the Commerce Sandbox domain tables and built-in scenario versions."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002_commerce_sandbox"
down_revision: str | None = "0001_sandbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sandbox"


def upgrade() -> None:
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("scenario_type", sa.String(64), nullable=False),
        sa.Column("sandbox_version", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("initial_state_json", sa.JSON(), nullable=False),
        sa.Column("ground_truth_ref", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "run_spaces",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "scenario_version_id",
            sa.String(128),
            sa.ForeignKey("sandbox.scenario_versions.id"),
            nullable=False,
        ),
        sa.Column("scenario_type", sa.String(64), nullable=False),
        sa.Column("sandbox_version", sa.String(32), nullable=False),
        sa.Column("initial_state_json", sa.JSON(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "test_users",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("balance", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("points_balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_new_user", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("membership_status", sa.String(32), nullable=False, server_default="INACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "coupons",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("coupon_type", sa.String(32), nullable=False, server_default="FIXED_AMOUNT"),
        sa.Column("face_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("threshold", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="AVAILABLE"),
        sa.Column("reserved_order_id", sa.String(128), nullable=True),
        sa.Column("used_order_id", sa.String(128), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "orders",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("original_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("discount_amount", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("paid_amount", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("refunded_amount", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("points_granted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_PAYMENT"),
        sa.Column("coupon_id", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "refunds",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("order_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ISSUED"),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_refunds_run_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "points_ledger_entries",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("order_id", sa.String(128), nullable=True),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_points_ledger_run_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "memberships",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("paid_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="INACTIVE"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "entitlements",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("membership_id", sa.String(128), nullable=False),
        sa.Column("entitlement_type", sa.String(64), nullable=False),
        sa.Column("granted_quantity", sa.Integer(), nullable=False),
        sa.Column("consumed_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="GRANTED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "business_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "epoch", "sequence", name="uq_events_run_epoch_sequence"),
        schema=SCHEMA,
    )
    op.create_table(
        "action_receipts",
        sa.Column("receipt_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("monetary_effects_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "run_id",
            "epoch",
            "action_type",
            "idempotency_key",
            name="uq_receipts_run_epoch_action_key",
        ),
        schema=SCHEMA,
    )

    for table in (
        "test_users",
        "coupons",
        "orders",
        "refunds",
        "points_ledger_entries",
        "memberships",
        "entitlements",
        "business_events",
        "action_receipts",
    ):
        op.create_index(f"ix_{table}_run_id", table, ["run_id"], schema=SCHEMA)

    now = datetime.now(UTC)
    scenario_rows = [
        {
            "id": f"{scenario}-fixed-v1",
            "scenario_type": scenario,
            "sandbox_version": "fixed",
            "version": "v1",
            "initial_state_json": {},
            "ground_truth_ref": None,
            "created_at": now,
        }
        for scenario in ("PROMOTION", "REFUND_POINTS", "MEMBERSHIP_ENTITLEMENT")
    ] + [
        {
            "id": f"{scenario}-vulnerable-v1",
            "scenario_type": scenario,
            "sandbox_version": "vulnerable",
            "version": "v1",
            "initial_state_json": {},
            "ground_truth_ref": f"internal:{scenario.lower()}:v1",
            "created_at": now,
        }
        for scenario in ("PROMOTION", "REFUND_POINTS", "MEMBERSHIP_ENTITLEMENT")
    ]
    op.bulk_insert(
        sa.table(
            "scenario_versions",
            sa.column("id", sa.String),
            sa.column("scenario_type", sa.String),
            sa.column("sandbox_version", sa.String),
            sa.column("version", sa.String),
            sa.column("initial_state_json", sa.JSON),
            sa.column("ground_truth_ref", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            schema=SCHEMA,
        ),
        scenario_rows,
    )


def downgrade() -> None:
    for table in (
        "action_receipts",
        "business_events",
        "entitlements",
        "memberships",
        "points_ledger_entries",
        "refunds",
        "orders",
        "coupons",
        "test_users",
        "run_spaces",
        "scenario_versions",
    ):
        op.drop_table(table, schema=SCHEMA)
