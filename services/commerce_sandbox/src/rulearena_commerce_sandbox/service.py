from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from rulearena_domain_contracts import (
    ActionReceipt,
    ActionStatus,
    ActionType,
    ApiError,
    BusinessEventType,
)
from rulearena_policy_schema import Currency, Money, ScenarioType
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .errors import DomainError
from .models import (
    ActionReceiptRecord,
    BusinessEvent,
    Coupon,
    Entitlement,
    Membership,
    Order,
    PointsLedgerEntry,
    Refund,
    RunSpace,
    ScenarioVersion,
    TestUser,
)
from .profiles import SandboxProfile
from .schemas import ActionCommand, ActionName, CreateRunRequest, RunResponse, SandboxVersion

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


class SandboxService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def create_run(self, request: CreateRunRequest) -> RunResponse:
        async with self.sessions() as session:
            async with session.begin():
                scenario = await session.scalar(
                    select(ScenarioVersion).where(
                        ScenarioVersion.scenario_type == request.scenario_type.value,
                        ScenarioVersion.sandbox_version == request.sandbox_version.value,
                    )
                )
                if scenario is None:
                    raise DomainError(
                        "SCENARIO_NOT_FOUND",
                        "the requested scenario version is not available",
                    )
                now = datetime.now(UTC)
                run = RunSpace(
                    id=str(uuid4()),
                    scenario_version_id=scenario.id,
                    scenario_type=scenario.scenario_type,
                    sandbox_version=scenario.sandbox_version,
                    initial_state_json=dict(scenario.initial_state_json),
                    epoch=0,
                    snapshot_version=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(run)
                await session.flush()
                snapshot = await self._snapshot(session, run)
                response = RunResponse(
                    run_id=run.id,
                    scenario_type=ScenarioType(run.scenario_type),
                    scenario_version=scenario.version,
                    snapshot=snapshot,
                )
            return response

    async def reset_run(self, run_id: str) -> dict[str, Any]:
        async with self.sessions() as session:
            async with session.begin():
                run = await self._get_run(session, run_id, lock=True)
                await self._delete_state(session, run_id)
                run.epoch += 1
                run.snapshot_version = 0
                run.updated_at = datetime.now(UTC)
                return await self._snapshot(session, run)

    async def execute(self, run_id: str, command: ActionCommand) -> ActionReceipt:
        if command.requires_idempotency_key() and not command.idempotency_key:
            raise DomainError("IDEMPOTENCY_KEY_REQUIRED", "write actions require idempotency_key")

        async with self.sessions() as session:
            async with session.begin():
                run = await self._get_run(session, run_id, lock=True)
                key = command.idempotency_key or f"inspect:{uuid4()}"
                existing = await session.scalar(
                    select(ActionReceiptRecord)
                    .where(
                        ActionReceiptRecord.run_id == run.id,
                        ActionReceiptRecord.epoch == run.epoch,
                        ActionReceiptRecord.action_type == command.action.value,
                        ActionReceiptRecord.idempotency_key == key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    return self._receipt_from_record(existing, command.action)

                status = ActionStatus.SUCCEEDED
                result: dict[str, Any] = {}
                effects: list[Money] = []
                error: DomainError | None = None
                try:
                    async with session.begin_nested():
                        result, effects = await self._dispatch(session, run, command, key)
                        if command.action is not ActionName.INSPECT_STATE:
                            run.snapshot_version += 1
                            run.updated_at = datetime.now(UTC)
                except DomainError as caught:
                    status = ActionStatus.REJECTED
                    error = caught

                occurred_at = datetime.now(UTC)
                record = ActionReceiptRecord(
                    receipt_id=str(uuid4()),
                    run_id=run.id,
                    epoch=run.epoch,
                    idempotency_key=key,
                    action_type=command.action.value,
                    status=status.value,
                    monetary_effects_json=[self._money_json(effect) for effect in effects],
                    result_json=result,
                    error_json=self._error_json(error) if error else None,
                    occurred_at=occurred_at,
                )
                session.add(record)
                await session.flush()
                return self._receipt_from_record(record, command.action)

    async def get_snapshot(self, run_id: str) -> dict[str, Any]:
        async with self.sessions() as session:
            async with session.begin():
                run = await self._get_run(session, run_id)
                return await self._snapshot(session, run)

    async def get_events(self, run_id: str) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            async with session.begin():
                run = await self._get_run(session, run_id)
                events = list(
                    (
                        await session.scalars(
                            select(BusinessEvent)
                            .where(
                                BusinessEvent.run_id == run.id,
                                BusinessEvent.epoch == run.epoch,
                            )
                            .order_by(BusinessEvent.sequence)
                        )
                    ).all()
                )
                return [self._event_json(event) for event in events]

    async def get_receipt(self, run_id: str, key: str) -> ActionReceipt:
        async with self.sessions() as session:
            async with session.begin():
                run = await self._get_run(session, run_id)
                record = await session.scalar(
                    select(ActionReceiptRecord)
                    .where(
                        ActionReceiptRecord.run_id == run.id,
                        ActionReceiptRecord.epoch == run.epoch,
                        ActionReceiptRecord.idempotency_key == key,
                    )
                    .order_by(ActionReceiptRecord.occurred_at)
                )
                if record is None:
                    raise DomainError("RECEIPT_NOT_FOUND", "receipt was not found")
                return self._receipt_from_record(record, ActionName(record.action_type))

    async def _dispatch(
        self,
        session: AsyncSession,
        run: RunSpace,
        command: ActionCommand,
        key: str,
    ) -> tuple[dict[str, Any], list[Money]]:
        handlers = {
            ActionName.CREATE_USER: self._create_user,
            ActionName.ISSUE_COUPON: self._issue_coupon,
            ActionName.CREATE_ORDER: self._create_order,
            ActionName.APPLY_COUPON: self._apply_coupon,
            ActionName.PAY_ORDER: self._pay_order,
            ActionName.CANCEL_ORDER: self._cancel_order,
            ActionName.REFUND_ORDER: self._refund_order,
            ActionName.REDEEM_POINTS: self._redeem_points,
            ActionName.ACTIVATE_MEMBERSHIP: self._activate_membership,
            ActionName.CONSUME_ENTITLEMENT: self._consume_entitlement,
            ActionName.CANCEL_MEMBERSHIP: self._cancel_membership,
            ActionName.INSPECT_STATE: self._inspect_state,
        }
        return await handlers[command.action](session, run, command, key)

    async def _create_user(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"initial_balance"}, {"initial_balance"})
        amount, currency = self._money(command.arguments["initial_balance"], "initial_balance")
        if amount < ZERO:
            raise DomainError("INVALID_AMOUNT", "initial_balance cannot be negative")
        existing = await session.scalar(select(TestUser).where(TestUser.run_id == run.id))
        if existing is not None:
            raise DomainError("USER_ALREADY_EXISTS", "a run can contain only one test user")
        user = TestUser(
            run_id=run.id,
            id="user-1",
            balance=amount,
            currency=currency.value,
            points_balance=0,
            is_new_user=True,
            membership_status="INACTIVE",
            version=0,
        )
        session.add(user)
        await self._event(
            session,
            run,
            "USER",
            user.id,
            BusinessEventType.USER_CREATED,
            {"user_id": user.id},
            key,
        )
        return {"user_id": user.id, "balance": self._amount_text(amount)}, [
            Money(currency=currency, amount=amount)
        ]

    async def _issue_coupon(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"value", "threshold"}, {"value", "threshold"})
        owner_id = command.target_id or command.actor_id
        user = await self._user(session, run.id, owner_id)
        value, currency = self._money(command.arguments["value"], "value")
        threshold, threshold_currency = self._money(command.arguments["threshold"], "threshold")
        if value <= ZERO or threshold < ZERO:
            raise DomainError("INVALID_AMOUNT", "coupon value must be positive")
        if currency is not threshold_currency or currency.value != user.currency:
            raise DomainError("CURRENCY_MISMATCH", "all monetary values must use one currency")
        coupon_id = await self._next_id(session, Coupon, run.id, "coupon")
        coupon = Coupon(
            run_id=run.id,
            id=coupon_id,
            owner_id=user.id,
            face_value=value,
            threshold=threshold,
            currency=currency.value,
            status="AVAILABLE",
            usage_count=0,
            version=0,
        )
        session.add(coupon)
        await self._event(
            session,
            run,
            "COUPON",
            coupon.id,
            BusinessEventType.COUPON_ISSUED,
            {"coupon_id": coupon.id, "owner_id": coupon.owner_id},
            key,
        )
        return {
            "coupon_id": coupon.id,
            "value": self._amount_text(value),
            "threshold": self._amount_text(threshold),
        }, [Money(currency=currency, amount=value)]

    async def _create_order(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"amount"}, {"amount"})
        user = await self._user(session, run.id, command.target_id or command.actor_id)
        amount, currency = self._money(command.arguments["amount"], "amount")
        if amount <= ZERO:
            raise DomainError("INVALID_AMOUNT", "order amount must be positive")
        if currency.value != user.currency:
            raise DomainError("CURRENCY_MISMATCH", "order currency differs from user currency")
        order_id = await self._next_id(session, Order, run.id, "order")
        order = Order(
            run_id=run.id,
            id=order_id,
            user_id=user.id,
            original_amount=amount,
            discount_amount=ZERO,
            paid_amount=ZERO,
            refunded_amount=ZERO,
            points_granted=0,
            currency=currency.value,
            status="PENDING_PAYMENT",
            version=0,
        )
        session.add(order)
        await self._event(
            session,
            run,
            "ORDER",
            order.id,
            BusinessEventType.ORDER_CREATED,
            {"order_id": order.id, "user_id": user.id},
            key,
        )
        return {"order_id": order.id, "amount": self._amount_text(amount)}, [
            Money(currency=currency, amount=amount)
        ]

    async def _apply_coupon(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"coupon_id"}, {"coupon_id"})
        order_id = self._target(command, "order_id")
        order = await self._order(session, run.id, order_id, lock=True)
        if order.coupon_id is not None:
            raise DomainError("COUPON_ALREADY_APPLIED", "an order can use only one coupon")
        coupon_id = self._string(command.arguments["coupon_id"], "coupon_id")
        coupon = await self._coupon(session, run.id, coupon_id, lock=True)
        if order.status != "PENDING_PAYMENT":
            raise DomainError("ORDER_NOT_PENDING", "coupon can only be applied to a pending order")
        if coupon.status not in {"AVAILABLE", "RESTORED"}:
            raise DomainError("COUPON_NOT_AVAILABLE", "coupon is not available")
        if coupon.owner_id != order.user_id:
            raise DomainError("COUPON_OWNER_MISMATCH", "coupon belongs to another user")
        if coupon.currency != order.currency or order.original_amount < coupon.threshold:
            raise DomainError("COUPON_CONDITION_FAILED", "order does not meet coupon conditions")
        discount = min(coupon.face_value, order.original_amount)
        order.discount_amount = discount
        order.coupon_id = coupon.id
        order.version += 1
        coupon.status = "RESERVED"
        coupon.reserved_order_id = order.id
        coupon.version += 1
        await self._event(
            session,
            run,
            "COUPON",
            coupon.id,
            BusinessEventType.COUPON_RESERVED,
            {"coupon_id": coupon.id, "order_id": order.id},
            key,
        )
        return {
            "order_id": order.id,
            "coupon_id": coupon.id,
            "discount": self._amount_text(discount),
        }, [Money(currency=Currency(order.currency), amount=discount)]

    async def _pay_order(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, set(), set())
        order = await self._order(session, run.id, self._target(command, "order_id"), lock=True)
        if order.status != "PENDING_PAYMENT":
            if order.status == "CANCELLED":
                raise DomainError("ORDER_CANCELLED", "cancelled orders cannot be paid")
            raise DomainError("ORDER_NOT_PENDING", "order is not awaiting payment")
        user = await self._user(session, run.id, order.user_id, lock=True)
        paid = self._quantize(order.original_amount - order.discount_amount)
        if paid <= ZERO:
            raise DomainError("INVALID_PAYMENT", "paid amount must be positive")
        if user.balance < paid:
            raise DomainError("INSUFFICIENT_BALANCE", "user balance is insufficient")
        user.balance = self._quantize(user.balance - paid)
        user.version += 1
        order.paid_amount = paid
        order.status = "PAID"
        order.version += 1
        if order.coupon_id is not None:
            coupon = await self._coupon(session, run.id, order.coupon_id, lock=True)
            if coupon.status != "RESERVED" or coupon.reserved_order_id != order.id:
                raise DomainError("COUPON_STATE_INVALID", "reserved coupon does not match order")
            coupon.status = "USED"
            coupon.reserved_order_id = None
            coupon.used_order_id = order.id
            coupon.usage_count += 1
            coupon.version += 1
            await self._event(
                session,
                run,
                "COUPON",
                coupon.id,
                BusinessEventType.COUPON_USED,
                {"coupon_id": coupon.id, "order_id": order.id},
                key,
            )
        await self._event(
            session,
            run,
            "ORDER",
            order.id,
            BusinessEventType.PAYMENT_CAPTURED,
            {"order_id": order.id, "paid_amount": self._amount_text(paid)},
            key,
        )
        effects = [Money(currency=Currency(order.currency), amount=paid)]
        result: dict[str, Any] = {"order_id": order.id, "paid_amount": self._amount_text(paid)}
        if run.scenario_type == ScenarioType.REFUND_POINTS.value:
            points = self._points_for(paid)
            order.points_granted = points
            user.points_balance += points
            await self._ledger(
                session, run, user.id, order.id, "GRANT", points, f"{key}:points-grant", key
            )
            await self._event(
                session,
                run,
                "USER",
                user.id,
                BusinessEventType.POINTS_GRANTED,
                {"user_id": user.id, "order_id": order.id, "amount": points},
                key,
            )
            result["points_granted"] = points
        return result, effects

    async def _cancel_order(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, set(), set())
        order = await self._order(session, run.id, self._target(command, "order_id"), lock=True)
        if order.status not in {"DRAFT", "PENDING_PAYMENT"}:
            raise DomainError("ORDER_NOT_CANCELLABLE", "order cannot be cancelled")
        if order.coupon_id is not None:
            coupon = await self._coupon(session, run.id, order.coupon_id, lock=True)
            if coupon.status == "RESERVED" and coupon.reserved_order_id == order.id:
                coupon.status = "RESTORED"
                coupon.reserved_order_id = None
                coupon.version += 1
                await self._event(
                    session,
                    run,
                    "COUPON",
                    coupon.id,
                    BusinessEventType.COUPON_RESTORED,
                    {"coupon_id": coupon.id, "order_id": order.id},
                    key,
                )
        order.status = "CANCELLED"
        order.version += 1
        await self._event(
            session,
            run,
            "ORDER",
            order.id,
            BusinessEventType.ORDER_CANCELLED,
            {"order_id": order.id},
            key,
        )
        return {"order_id": order.id, "status": order.status}, []

    async def _refund_order(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"amount"}, {"amount"})
        order = await self._order(session, run.id, self._target(command, "order_id"), lock=True)
        if order.status not in {"PAID", "PARTIALLY_REFUNDED"}:
            raise DomainError("ORDER_NOT_REFUNDABLE", "order is not refundable")
        amount, currency = self._money(command.arguments["amount"], "amount")
        if amount <= ZERO or currency.value != order.currency:
            raise DomainError("INVALID_REFUND", "refund amount is invalid")
        remaining = self._quantize(order.paid_amount - order.refunded_amount)
        max_amount = (
            order.paid_amount
            if (
                SandboxProfile(
                    SandboxVersion(run.sandbox_version)
                ).allows_refund_against_original_amount
                and run.scenario_type == ScenarioType.PROMOTION.value
            )
            else remaining
        )
        if amount > max_amount:
            raise DomainError("REFUND_EXCEEDS_PAID", "refunds cannot exceed the paid amount")
        user = await self._user(session, run.id, order.user_id, lock=True)
        user.balance = self._quantize(user.balance + amount)
        user.version += 1
        order.refunded_amount = self._quantize(order.refunded_amount + amount)
        order.status = (
            "REFUNDED" if order.refunded_amount >= order.paid_amount else "PARTIALLY_REFUNDED"
        )
        order.version += 1
        refund_id = await self._next_id(session, Refund, run.id, "refund")
        session.add(
            Refund(
                run_id=run.id,
                id=refund_id,
                order_id=order.id,
                amount=amount,
                status="ISSUED",
                idempotency_key=key,
                created_at=datetime.now(UTC),
            )
        )
        await self._event(
            session,
            run,
            "ORDER",
            order.id,
            BusinessEventType.REFUND_ISSUED,
            {"order_id": order.id, "amount": self._amount_text(amount)},
            key,
        )

        profile = SandboxProfile(SandboxVersion(run.sandbox_version))
        if run.scenario_type == ScenarioType.REFUND_POINTS.value:
            if profile.grants_points_again_on_refund:
                extra_points = self._points_for(amount)
                user.points_balance += extra_points
                await self._ledger(
                    session,
                    run,
                    user.id,
                    order.id,
                    "GRANT",
                    extra_points,
                    f"{key}:refund-points-grant",
                    key,
                )
                await self._event(
                    session,
                    run,
                    "USER",
                    user.id,
                    BusinessEventType.POINTS_GRANTED,
                    {"user_id": user.id, "order_id": order.id, "amount": extra_points},
                    key,
                )
            else:
                await self._revoke_points(
                    session, run, user, order, key, order.refunded_amount >= order.paid_amount
                )

        if (
            run.scenario_type == ScenarioType.PROMOTION.value
            and order.status == "REFUNDED"
            and order.coupon_id is not None
            and profile.restores_coupon_after_full_refund
        ):
            coupon = await self._coupon(session, run.id, order.coupon_id, lock=True)
            coupon.status = "AVAILABLE"
            coupon.used_order_id = None
            coupon.version += 1
            await self._event(
                session,
                run,
                "COUPON",
                coupon.id,
                BusinessEventType.COUPON_RESTORED,
                {"coupon_id": coupon.id, "order_id": order.id},
                key,
            )
        return {
            "refund_id": refund_id,
            "order_id": order.id,
            "refunded_amount": self._amount_text(amount),
            "order_refunded_total": self._amount_text(order.refunded_amount),
        }, [Money(currency=currency, amount=amount)]

    async def _redeem_points(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"amount"}, {"amount"})
        amount = self._positive_int(command.arguments["amount"], "amount")
        user = await self._user(session, run.id, command.target_id or command.actor_id, lock=True)
        if user.points_balance < amount:
            raise DomainError("INSUFFICIENT_POINTS", "points balance is insufficient")
        user.points_balance -= amount
        user.version += 1
        await self._ledger(
            session, run, user.id, None, "REDEEM", -amount, f"{key}:points-redeem", key
        )
        await self._event(
            session,
            run,
            "USER",
            user.id,
            BusinessEventType.POINTS_REDEEMED,
            {"user_id": user.id, "amount": amount},
            key,
        )
        return {"user_id": user.id, "redeemed_points": amount}, []

    async def _activate_membership(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"paid_amount", "quantity"}, {"paid_amount", "quantity"})
        user = await self._user(session, run.id, command.target_id or command.actor_id, lock=True)
        paid_amount, currency = self._money(command.arguments["paid_amount"], "paid_amount")
        quantity = self._positive_int(command.arguments["quantity"], "quantity")
        if paid_amount <= ZERO or currency.value != user.currency:
            raise DomainError("INVALID_MEMBERSHIP", "membership price is invalid")
        if user.membership_status != "INACTIVE":
            raise DomainError("MEMBERSHIP_ALREADY_ACTIVE", "user already has a membership")
        if user.balance < paid_amount:
            raise DomainError("INSUFFICIENT_BALANCE", "user balance is insufficient")
        user.balance = self._quantize(user.balance - paid_amount)
        user.membership_status = "ACTIVE"
        user.version += 1
        membership_id = await self._next_id(session, Membership, run.id, "membership")
        entitlement_id = await self._next_id(session, Entitlement, run.id, "entitlement")
        session.add(
            Membership(
                run_id=run.id,
                id=membership_id,
                user_id=user.id,
                paid_amount=paid_amount,
                currency=currency.value,
                status="ACTIVE",
                version=0,
            )
        )
        session.add(
            Entitlement(
                run_id=run.id,
                id=entitlement_id,
                membership_id=membership_id,
                entitlement_type="USAGE",
                granted_quantity=quantity,
                consumed_quantity=0,
                revoked_quantity=0,
                status="GRANTED",
                version=0,
            )
        )
        await self._event(
            session,
            run,
            "MEMBERSHIP",
            membership_id,
            BusinessEventType.MEMBERSHIP_ACTIVATED,
            {"membership_id": membership_id, "user_id": user.id},
            key,
        )
        await self._event(
            session,
            run,
            "ENTITLEMENT",
            entitlement_id,
            BusinessEventType.ENTITLEMENT_GRANTED,
            {"entitlement_id": entitlement_id, "quantity": quantity},
            key,
        )
        return {
            "membership_id": membership_id,
            "entitlement_id": entitlement_id,
            "quantity": quantity,
        }, [Money(currency=currency, amount=paid_amount)]

    async def _consume_entitlement(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"quantity"}, {"quantity"})
        quantity = self._positive_int(command.arguments["quantity"], "quantity")
        entitlement = await self._entitlement(
            session, run.id, self._target(command, "entitlement_id"), lock=True
        )
        if entitlement.status in {"REVOKED", "CONSUMED"}:
            raise DomainError("ENTITLEMENT_NOT_AVAILABLE", "entitlement is not available")
        available = (
            entitlement.granted_quantity
            - entitlement.consumed_quantity
            - entitlement.revoked_quantity
        )
        profile = SandboxProfile(SandboxVersion(run.sandbox_version))
        if not profile.allows_entitlement_overconsumption and quantity > available:
            raise DomainError("INSUFFICIENT_ENTITLEMENT", "entitlement quantity is insufficient")
        entitlement.consumed_quantity += quantity
        entitlement.status = (
            "CONSUMED"
            if entitlement.consumed_quantity >= entitlement.granted_quantity
            else "PARTIALLY_CONSUMED"
        )
        entitlement.version += 1
        await self._event(
            session,
            run,
            "ENTITLEMENT",
            entitlement.id,
            BusinessEventType.ENTITLEMENT_CONSUMED,
            {"entitlement_id": entitlement.id, "quantity": quantity},
            key,
        )
        return {
            "entitlement_id": entitlement.id,
            "consumed_quantity": entitlement.consumed_quantity,
        }, []

    async def _cancel_membership(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"refund_requested"}, {"refund_requested"})
        refund_requested = self._strict_bool(
            command.arguments["refund_requested"], "refund_requested"
        )
        membership = await self._membership(
            session, run.id, self._target(command, "membership_id"), lock=True
        )
        if membership.status != "ACTIVE":
            raise DomainError("MEMBERSHIP_NOT_ACTIVE", "membership is not active")
        user = await self._user(session, run.id, membership.user_id, lock=True)
        entitlement = await self._entitlement_by_membership(
            session, run.id, membership.id, lock=True
        )
        profile = SandboxProfile(SandboxVersion(run.sandbox_version))
        available = (
            entitlement.granted_quantity
            - entitlement.consumed_quantity
            - entitlement.revoked_quantity
        )
        if refund_requested:
            if (
                entitlement.consumed_quantity > 0
                and not profile.allows_full_membership_refund_after_consumption
            ):
                raise DomainError(
                    "ENTITLEMENT_CONSUMED",
                    "a membership with consumed entitlement cannot receive a full refund",
                )
            user.balance = self._quantize(user.balance + membership.paid_amount)
            membership.status = "REFUNDED"
            user.membership_status = "INACTIVE"
            membership.version += 1
            if not profile.leaves_entitlement_after_membership_refund and available > 0:
                entitlement.revoked_quantity += available
                entitlement.status = "REVOKED"
                entitlement.version += 1
                await self._event(
                    session,
                    run,
                    "ENTITLEMENT",
                    entitlement.id,
                    BusinessEventType.ENTITLEMENT_REVOKED,
                    {"entitlement_id": entitlement.id, "quantity": available},
                    key,
                )
            await self._event(
                session,
                run,
                "MEMBERSHIP",
                membership.id,
                BusinessEventType.MEMBERSHIP_REFUNDED,
                {
                    "membership_id": membership.id,
                    "amount": self._amount_text(membership.paid_amount),
                },
                key,
            )
            return {
                "membership_id": membership.id,
                "status": membership.status,
                "refunded_amount": self._amount_text(membership.paid_amount),
            }, [Money(currency=Currency(membership.currency), amount=membership.paid_amount)]

        membership.status = "CANCELLED"
        user.membership_status = "INACTIVE"
        membership.version += 1
        if not profile.leaves_entitlement_after_membership_refund and available > 0:
            entitlement.revoked_quantity += available
            entitlement.status = "REVOKED"
            entitlement.version += 1
            await self._event(
                session,
                run,
                "ENTITLEMENT",
                entitlement.id,
                BusinessEventType.ENTITLEMENT_REVOKED,
                {"entitlement_id": entitlement.id, "quantity": available},
                key,
            )
        await self._event(
            session,
            run,
            "MEMBERSHIP",
            membership.id,
            BusinessEventType.MEMBERSHIP_CANCELLED,
            {"membership_id": membership.id},
            key,
        )
        return {"membership_id": membership.id, "status": membership.status}, []

    async def _inspect_state(
        self, session: AsyncSession, run: RunSpace, command: ActionCommand, key: str
    ) -> tuple[dict[str, Any], list[Money]]:
        self._args(command, {"scope"}, set())
        scope = command.arguments.get("scope", "RUN")
        if scope not in {"RUN", "USER", "ORDER", "MEMBERSHIP"}:
            raise DomainError("INVALID_SCOPE", "inspect scope is invalid")
        snapshot = await self._snapshot(session, run)
        return {"scope": scope, "snapshot": snapshot}, []

    async def _revoke_points(
        self,
        session: AsyncSession,
        run: RunSpace,
        user: TestUser,
        order: Order,
        key: str,
        full_refund: bool,
    ) -> None:
        current_revoke = await session.scalar(
            select(func.coalesce(func.sum(PointsLedgerEntry.amount), 0)).where(
                PointsLedgerEntry.run_id == run.id,
                PointsLedgerEntry.order_id == order.id,
                PointsLedgerEntry.entry_type == "REVOKE",
            )
        )
        already_revoked = -int(current_revoke or 0)
        target_revoke = (
            order.points_granted
            if full_refund
            else int(
                (
                    Decimal(order.points_granted) * order.refunded_amount / order.paid_amount
                ).to_integral_value(rounding=ROUND_FLOOR)
            )
        )
        delta = max(target_revoke - already_revoked, 0)
        if delta == 0:
            return
        if user.points_balance < delta:
            raise DomainError(
                "POINTS_ALREADY_REDEEMED",
                "refund cannot revoke points that have already been redeemed",
            )
        user.points_balance -= delta
        user.version += 1
        await self._ledger(
            session, run, user.id, order.id, "REVOKE", -delta, f"{key}:points-revoke", key
        )
        await self._event(
            session,
            run,
            "USER",
            user.id,
            BusinessEventType.POINTS_REVOKED,
            {"user_id": user.id, "order_id": order.id, "amount": delta},
            key,
        )

    async def _ledger(
        self,
        session: AsyncSession,
        run: RunSpace,
        user_id: str,
        order_id: str | None,
        entry_type: str,
        amount: int,
        ledger_key: str,
        event_key: str,
    ) -> None:
        session.add(
            PointsLedgerEntry(
                run_id=run.id,
                id=str(uuid4()),
                user_id=user_id,
                order_id=order_id,
                entry_type=entry_type,
                amount=amount,
                idempotency_key=ledger_key,
                created_at=datetime.now(UTC),
            )
        )

    async def _event(
        self,
        session: AsyncSession,
        run: RunSpace,
        aggregate_type: str,
        aggregate_id: str,
        event_type: BusinessEventType,
        payload: dict[str, Any],
        key: str,
    ) -> None:
        last = await session.scalar(
            select(func.max(BusinessEvent.sequence)).where(
                BusinessEvent.run_id == run.id,
                BusinessEvent.epoch == run.epoch,
            )
        )
        session.add(
            BusinessEvent(
                event_id=str(uuid4()),
                run_id=run.id,
                epoch=run.epoch,
                sequence=int(last or 0) + 1,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type.value,
                payload_json=payload,
                idempotency_key=key,
                occurred_at=datetime.now(UTC),
            )
        )
        await session.flush()

    async def _snapshot(self, session: AsyncSession, run: RunSpace) -> dict[str, Any]:
        users = list(
            (
                await session.scalars(
                    select(TestUser).where(TestUser.run_id == run.id).order_by(TestUser.id)
                )
            ).all()
        )
        coupons = list(
            (
                await session.scalars(
                    select(Coupon).where(Coupon.run_id == run.id).order_by(Coupon.id)
                )
            ).all()
        )
        orders = list(
            (
                await session.scalars(
                    select(Order).where(Order.run_id == run.id).order_by(Order.id)
                )
            ).all()
        )
        memberships = list(
            (
                await session.scalars(
                    select(Membership).where(Membership.run_id == run.id).order_by(Membership.id)
                )
            ).all()
        )
        entitlements = list(
            (
                await session.scalars(
                    select(Entitlement).where(Entitlement.run_id == run.id).order_by(Entitlement.id)
                )
            ).all()
        )
        state: dict[str, Any] = {
            "users": [
                {
                    "id": user.id,
                    "balance": self._amount_text(user.balance),
                    "currency": user.currency,
                    "points_balance": user.points_balance,
                    "is_new_user": user.is_new_user,
                    "membership_status": user.membership_status,
                }
                for user in users
            ],
            "coupons": [
                {
                    "id": coupon.id,
                    "owner_id": coupon.owner_id,
                    "face_value": self._amount_text(coupon.face_value),
                    "threshold": self._amount_text(coupon.threshold),
                    "currency": coupon.currency,
                    "status": coupon.status,
                    "reserved_order_id": coupon.reserved_order_id,
                    "used_order_id": coupon.used_order_id,
                    "usage_count": coupon.usage_count,
                }
                for coupon in coupons
            ],
            "orders": [
                {
                    "id": order.id,
                    "user_id": order.user_id,
                    "original_amount": self._amount_text(order.original_amount),
                    "discount_amount": self._amount_text(order.discount_amount),
                    "paid_amount": self._amount_text(order.paid_amount),
                    "refunded_amount": self._amount_text(order.refunded_amount),
                    "points_granted": order.points_granted,
                    "currency": order.currency,
                    "status": order.status,
                    "coupon_id": order.coupon_id,
                }
                for order in orders
            ],
            "memberships": [
                {
                    "id": membership.id,
                    "user_id": membership.user_id,
                    "paid_amount": self._amount_text(membership.paid_amount),
                    "currency": membership.currency,
                    "status": membership.status,
                }
                for membership in memberships
            ],
            "entitlements": [
                {
                    "id": entitlement.id,
                    "membership_id": entitlement.membership_id,
                    "entitlement_type": entitlement.entitlement_type,
                    "granted_quantity": entitlement.granted_quantity,
                    "consumed_quantity": entitlement.consumed_quantity,
                    "revoked_quantity": entitlement.revoked_quantity,
                    "status": entitlement.status,
                }
                for entitlement in entitlements
            ],
        }
        canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {
            "schema_version": "1.0",
            "run_id": run.id,
            "snapshot_version": run.snapshot_version,
            "state_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "state": state,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    async def _delete_state(self, session: AsyncSession, run_id: str) -> None:
        for model in (
            Entitlement,
            Membership,
            PointsLedgerEntry,
            Refund,
            Order,
            Coupon,
            TestUser,
        ):
            await session.execute(delete(model).where(model.run_id == run_id))

    async def _get_run(self, session: AsyncSession, run_id: str, *, lock: bool = False) -> RunSpace:
        statement = select(RunSpace).where(RunSpace.id == run_id)
        if lock:
            statement = statement.with_for_update()
        run = await session.scalar(statement)
        if run is None:
            raise DomainError("RUN_NOT_FOUND", "run space was not found")
        return run

    async def _user(
        self, session: AsyncSession, run_id: str, user_id: str, *, lock: bool = False
    ) -> TestUser:
        statement = select(TestUser).where(TestUser.run_id == run_id, TestUser.id == user_id)
        if lock:
            statement = statement.with_for_update()
        user = await session.scalar(statement)
        if user is None:
            raise DomainError("USER_NOT_FOUND", "user was not found")
        return user

    async def _order(
        self, session: AsyncSession, run_id: str, order_id: str, *, lock: bool = False
    ) -> Order:
        statement = select(Order).where(Order.run_id == run_id, Order.id == order_id)
        if lock:
            statement = statement.with_for_update()
        order = await session.scalar(statement)
        if order is None:
            raise DomainError("ORDER_NOT_FOUND", "order was not found")
        return order

    async def _coupon(
        self, session: AsyncSession, run_id: str, coupon_id: str, *, lock: bool = False
    ) -> Coupon:
        statement = select(Coupon).where(Coupon.run_id == run_id, Coupon.id == coupon_id)
        if lock:
            statement = statement.with_for_update()
        coupon = await session.scalar(statement)
        if coupon is None:
            raise DomainError("COUPON_NOT_FOUND", "coupon was not found")
        return coupon

    async def _membership(
        self, session: AsyncSession, run_id: str, membership_id: str, *, lock: bool = False
    ) -> Membership:
        statement = select(Membership).where(
            Membership.run_id == run_id, Membership.id == membership_id
        )
        if lock:
            statement = statement.with_for_update()
        membership = await session.scalar(statement)
        if membership is None:
            raise DomainError("MEMBERSHIP_NOT_FOUND", "membership was not found")
        return membership

    async def _entitlement(
        self, session: AsyncSession, run_id: str, entitlement_id: str, *, lock: bool = False
    ) -> Entitlement:
        statement = select(Entitlement).where(
            Entitlement.run_id == run_id, Entitlement.id == entitlement_id
        )
        if lock:
            statement = statement.with_for_update()
        entitlement = await session.scalar(statement)
        if entitlement is None:
            raise DomainError("ENTITLEMENT_NOT_FOUND", "entitlement was not found")
        return entitlement

    async def _entitlement_by_membership(
        self, session: AsyncSession, run_id: str, membership_id: str, *, lock: bool = False
    ) -> Entitlement:
        statement = select(Entitlement).where(
            Entitlement.run_id == run_id, Entitlement.membership_id == membership_id
        )
        if lock:
            statement = statement.with_for_update()
        entitlement = await session.scalar(statement)
        if entitlement is None:
            raise DomainError("ENTITLEMENT_NOT_FOUND", "membership entitlement was not found")
        return entitlement

    async def _next_id(
        self, session: AsyncSession, model: type[Any], run_id: str, prefix: str
    ) -> str:
        rows = await session.scalars(select(model.id).where(model.run_id == run_id))
        return f"{prefix}-{len(list(rows.all())) + 1}"

    @staticmethod
    def _target(command: ActionCommand, name: str) -> str:
        if not command.target_id:
            raise DomainError("TARGET_REQUIRED", f"{name} is required")
        return command.target_id

    @staticmethod
    def _args(command: ActionCommand, allowed: set[str], required: set[str]) -> None:
        unknown = set(command.arguments) - allowed
        if unknown:
            raise DomainError(
                "UNKNOWN_ARGUMENT", "unknown action argument", {"fields": sorted(unknown)}
            )
        missing = required - set(command.arguments)
        if missing:
            raise DomainError(
                "MISSING_ARGUMENT",
                "required action argument is missing",
                {"fields": sorted(missing)},
            )

    @staticmethod
    def _string(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value:
            raise DomainError("INVALID_ARGUMENT", f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _strict_bool(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise DomainError("INVALID_ARGUMENT", f"{field} must be boolean")
        return value

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise DomainError("INVALID_ARGUMENT", f"{field} must be a positive integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise DomainError("INVALID_ARGUMENT", f"{field} must be a positive integer") from None
        if parsed <= 0 or str(value) not in {str(parsed), parsed.__str__()}:
            raise DomainError("INVALID_ARGUMENT", f"{field} must be a positive integer")
        return parsed

    @classmethod
    def _money(cls, value: Any, field: str) -> tuple[Decimal, Currency]:
        currency_value: Any = "CNY"
        raw_amount = value
        if isinstance(value, dict):
            unknown = set(value) - {"amount", "currency"}
            if unknown or "amount" not in value:
                raise DomainError("INVALID_MONEY", f"{field} must contain amount and currency")
            raw_amount = value["amount"]
            currency_value = value.get("currency", "CNY")
        if isinstance(raw_amount, float | bool) or not isinstance(raw_amount, str | int | Decimal):
            raise DomainError("INVALID_MONEY", f"{field} must use a decimal string amount")
        try:
            amount = cls._quantize(Decimal(raw_amount))
            currency = Currency(currency_value)
        except (InvalidOperation, ValueError):
            raise DomainError("INVALID_MONEY", f"{field} is not a valid monetary value") from None
        if not amount.is_finite() or amount < ZERO:
            raise DomainError("INVALID_MONEY", f"{field} must be non-negative")
        return amount, currency

    @staticmethod
    def _quantize(value: Decimal) -> Decimal:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)

    @staticmethod
    def _amount_text(value: Decimal) -> str:
        return f"{SandboxService._quantize(value):.2f}"

    @staticmethod
    def _money_json(money: Money) -> dict[str, str]:
        return {
            "currency": money.currency.value,
            "amount": SandboxService._amount_text(money.amount),
        }

    @staticmethod
    def _points_for(amount: Decimal) -> int:
        return int(amount.to_integral_value(rounding=ROUND_FLOOR))

    @staticmethod
    def _error_json(error: DomainError) -> dict[str, Any]:
        return ApiError(code=error.code, message=error.message, details=error.details).model_dump(
            mode="json"
        )

    @staticmethod
    def _receipt_from_record(record: ActionReceiptRecord, action: ActionName) -> ActionReceipt:
        error = ApiError.model_validate(record.error_json) if record.error_json else None
        return ActionReceipt(
            receipt_id=UUID(record.receipt_id),
            run_id=record.run_id,
            idempotency_key=None
            if record.idempotency_key.startswith("inspect:")
            else record.idempotency_key,
            action_type=ActionType(action.value.upper()),
            status=ActionStatus(record.status),
            monetary_effects=tuple(
                Money(
                    currency=Currency(item["currency"]),
                    amount=Decimal(str(item["amount"])),
                )
                for item in record.monetary_effects_json
            ),
            result=record.result_json,
            error=error,
            occurred_at=record.occurred_at,
        )

    @staticmethod
    def _event_json(event: BusinessEvent) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "event_id": event.event_id,
            "run_id": event.run_id,
            "sequence": event.sequence,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "event_type": event.event_type,
            "payload": event.payload_json,
            "idempotency_key": event.idempotency_key,
            "occurred_at": event.occurred_at.isoformat(),
        }
