from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "sandbox"


class Base(DeclarativeBase):
    pass


class ScenarioVersion(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scenario_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sandbox_version: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ground_truth_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunSpace(Base):
    __tablename__ = "run_spaces"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scenario_version_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.scenario_versions.id"), nullable=False
    )
    scenario_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sandbox_version: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestUser(Base):
    __tablename__ = "test_users"
    __table_args__ = (Index("ix_test_users_run_id", "run_id"), {"schema": SCHEMA})

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    points_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_new_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    membership_status: Mapped[str] = mapped_column(String(32), nullable=False, default="INACTIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (Index("ix_coupons_run_id", "run_id"), {"schema": SCHEMA})

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    coupon_type: Mapped[str] = mapped_column(String(32), nullable=False, default="FIXED_AMOUNT")
    face_value: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="AVAILABLE")
    reserved_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    used_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_run_id", "run_id"), {"schema": SCHEMA})

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4, asdecimal=True), nullable=False, default=Decimal("0")
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4, asdecimal=True), nullable=False, default=Decimal("0")
    )
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4, asdecimal=True), nullable=False, default=Decimal("0")
    )
    points_granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_PAYMENT")
    coupon_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        Index("ix_refunds_run_id", "run_id"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_refunds_run_key"),
        {"schema": SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ISSUED")
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PointsLedgerEntry(Base):
    __tablename__ = "points_ledger_entries"
    __table_args__ = (
        Index("ix_points_ledger_entries_run_id", "run_id"),
        UniqueConstraint("run_id", "idempotency_key", name="uq_points_ledger_run_key"),
        {"schema": SCHEMA},
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("ix_memberships_run_id", "run_id"), {"schema": SCHEMA})

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 4, asdecimal=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="INACTIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (Index("ix_entitlements_run_id", "run_id"), {"schema": SCHEMA})

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    membership_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entitlement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    consumed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="GRANTED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BusinessEvent(Base):
    __tablename__ = "business_events"
    __table_args__ = (
        Index("ix_business_events_run_id", "run_id"),
        UniqueConstraint("run_id", "epoch", "sequence", name="uq_events_run_epoch_sequence"),
        {"schema": SCHEMA},
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionReceiptRecord(Base):
    __tablename__ = "action_receipts"
    __table_args__ = (
        Index("ix_action_receipts_run_id", "run_id"),
        UniqueConstraint(
            "run_id",
            "epoch",
            "action_type",
            "idempotency_key",
            name="uq_receipts_run_epoch_action_key",
        ),
        {"schema": SCHEMA},
    )

    receipt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    monetary_effects_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
