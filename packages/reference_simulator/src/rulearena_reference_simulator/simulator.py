from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import TypeVar

from rulearena_domain_contracts import ActionType
from rulearena_policy_schema import (
    Currency,
    MembershipRule,
    PointsRule,
    PromotionRule,
    RefundRule,
    RuleSpec,
    ScenarioType,
)

from .models import (
    SimAction,
    SimCoupon,
    SimEntitlement,
    SimEvent,
    SimMembership,
    SimOrder,
    SimulationState,
    SimUser,
    TransitionResult,
    TransitionStatus,
)

T = TypeVar("T")
CENT = Decimal("0.01")


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _replace(items: tuple[T, ...], index: int, item: T) -> tuple[T, ...]:
    return items[:index] + (item,) + items[index + 1 :]


class ReferenceSimulator:
    """Small, pure rule model. It deliberately has no Sandbox implementation dependency."""

    def __init__(self, rule_spec: RuleSpec) -> None:
        self.rule_spec = rule_spec
        self.promotion = next((r for r in rule_spec.rules if isinstance(r, PromotionRule)), None)
        self.refund = next((r for r in rule_spec.rules if isinstance(r, RefundRule)), None)
        self.points = next((r for r in rule_spec.rules if isinstance(r, PointsRule)), None)
        self.membership = next((r for r in rule_spec.rules if isinstance(r, MembershipRule)), None)

    def initial_state(self) -> SimulationState:
        return SimulationState(scenario_type=self.rule_spec.scenario_type)

    def legal_actions(self, state: SimulationState) -> tuple[SimAction, ...]:
        actions: list[SimAction] = []
        if not state.users:
            actions.append(SimAction.build(ActionType.CREATE_USER, initial_balance="500.00"))
            return tuple(actions)
        user = state.users[0]
        if state.scenario_type is ScenarioType.PROMOTION:
            if self.promotion and not state.coupons:
                actions.append(
                    SimAction.build(
                        ActionType.ISSUE_COUPON,
                        target_id=user.user_id,
                        value=format(self.promotion.discount_amount.amount, "f"),
                        threshold=format(self.promotion.minimum_order_amount.amount, "f"),
                    )
                )
            if not state.orders:
                amount = (
                    self.promotion.minimum_order_amount.amount if self.promotion else Decimal("200")
                )
                actions.append(
                    SimAction.build(
                        ActionType.CREATE_ORDER,
                        target_id=user.user_id,
                        amount=format(amount, "f"),
                    )
                )
            elif state.orders[0].status == "CREATED":
                order = state.orders[0]
                if state.coupons and order.coupon_id is None:
                    actions.append(
                        SimAction.build(
                            ActionType.APPLY_COUPON,
                            target_id=order.order_id,
                            coupon_id=state.coupons[0].coupon_id,
                        )
                    )
                actions.append(SimAction.build(ActionType.PAY_ORDER, target_id=order.order_id))
            elif state.orders[0].status in {"PAID", "PARTIALLY_REFUNDED"}:
                actions.append(
                    SimAction.build(
                        ActionType.REFUND_ORDER,
                        target_id=state.orders[0].order_id,
                        amount=format(
                            state.orders[0].paid_amount - state.orders[0].refunded_amount, "f"
                        ),
                    )
                )
        elif state.scenario_type is ScenarioType.REFUND_POINTS:
            if not state.orders:
                actions.append(
                    SimAction.build(
                        ActionType.CREATE_ORDER, target_id=user.user_id, amount="200.00"
                    )
                )
            elif state.orders[0].status == "CREATED":
                actions.append(
                    SimAction.build(ActionType.PAY_ORDER, target_id=state.orders[0].order_id)
                )
            elif state.orders[0].status in {"PAID", "PARTIALLY_REFUNDED"}:
                actions.append(
                    SimAction.build(
                        ActionType.REFUND_ORDER,
                        target_id=state.orders[0].order_id,
                        amount=format(
                            state.orders[0].paid_amount - state.orders[0].refunded_amount, "f"
                        ),
                    )
                )
                if user.points_balance > 0:
                    actions.append(
                        SimAction.build(ActionType.REDEEM_POINTS, amount=user.points_balance)
                    )
        elif state.scenario_type is ScenarioType.MEMBERSHIP_ENTITLEMENT and self.membership:
            if not state.memberships:
                actions.append(
                    SimAction.build(
                        ActionType.ACTIVATE_MEMBERSHIP,
                        target_id=user.user_id,
                        paid_amount=format(self.membership.price.amount, "f"),
                        quantity=self.membership.entitlement_quantity,
                    )
                )
            elif state.memberships[0].status == "ACTIVE":
                entitlement = state.entitlements[0]
                if entitlement.available:
                    actions.append(
                        SimAction.build(
                            ActionType.CONSUME_ENTITLEMENT,
                            target_id=entitlement.entitlement_id,
                            quantity=1,
                        )
                    )
                actions.append(
                    SimAction.build(
                        ActionType.CANCEL_MEMBERSHIP,
                        target_id=state.memberships[0].membership_id,
                        refund_requested=True,
                    )
                )
        return tuple(sorted(actions, key=SimAction.canonical_key))

    def transition(self, state: SimulationState, action: SimAction) -> TransitionResult:
        common = {ActionType.CREATE_USER}
        scenario_actions = {
            ScenarioType.PROMOTION: {
                ActionType.ISSUE_COUPON,
                ActionType.CREATE_ORDER,
                ActionType.APPLY_COUPON,
                ActionType.PAY_ORDER,
                ActionType.CANCEL_ORDER,
                ActionType.REFUND_ORDER,
            },
            ScenarioType.REFUND_POINTS: {
                ActionType.CREATE_ORDER,
                ActionType.PAY_ORDER,
                ActionType.CANCEL_ORDER,
                ActionType.REFUND_ORDER,
                ActionType.REDEEM_POINTS,
            },
            ScenarioType.MEMBERSHIP_ENTITLEMENT: {
                ActionType.ACTIVATE_MEMBERSHIP,
                ActionType.CONSUME_ENTITLEMENT,
                ActionType.CANCEL_MEMBERSHIP,
            },
        }
        if action.action_type not in common | scenario_actions[state.scenario_type]:
            return TransitionResult(
                TransitionStatus.UNSUPPORTED, state, code="ACTION_NOT_SUPPORTED"
            )
        if action.idempotency_key:
            marker = (action.action_type.value, action.idempotency_key)
            if marker in state.seen_idempotency_keys:
                return TransitionResult(TransitionStatus.APPLIED, state)
        handler = getattr(self, f"_do_{action.action_type.value.lower()}", None)
        if handler is None or action.action_type is ActionType.INSPECT_STATE:
            return TransitionResult(TransitionStatus.UNSUPPORTED, state, code="UNSUPPORTED_ACTION")
        try:
            result: TransitionResult = handler(state, action)
        except (ArithmeticError, TypeError, ValueError, StopIteration):
            return self._reject(state, "INVALID_ARGUMENT")
        if result.status is TransitionStatus.APPLIED and action.idempotency_key:
            result = replace(
                result,
                state=replace(
                    result.state,
                    seen_idempotency_keys=result.state.seen_idempotency_keys
                    | {(action.action_type.value, action.idempotency_key)},
                ),
            )
        return result

    @staticmethod
    def _reject(state: SimulationState, code: str) -> TransitionResult:
        return TransitionResult(TransitionStatus.REJECTED, state, code=code)

    def _do_create_user(self, state: SimulationState, action: SimAction) -> TransitionResult:
        if state.users:
            return self._reject(state, "USER_EXISTS")
        currency = Currency(str(action.argument("currency", "CNY")))
        balance = _money(action.argument("initial_balance", "0"))
        if balance < 0:
            return self._reject(state, "INVALID_AMOUNT")
        user = SimUser(action.actor_id, balance, currency)
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(state, users=(user,)),
            (SimEvent("USER_CREATED", "USER", user.user_id),),
        )

    def _do_issue_coupon(self, state: SimulationState, action: SimAction) -> TransitionResult:
        if not state.users or self.promotion is None:
            return self._reject(state, "PRECONDITION_FAILED")
        owner_id = action.target_id or action.actor_id
        if not any(user.user_id == owner_id for user in state.users):
            return self._reject(state, "USER_NOT_FOUND")
        face_value = _money(action.argument("value", self.promotion.discount_amount.amount))
        threshold = _money(action.argument("threshold", self.promotion.minimum_order_amount.amount))
        currency = Currency(str(action.argument("currency", "CNY")))
        if (
            face_value <= 0
            or threshold < 0
            or currency is not self.promotion.discount_amount.currency
        ):
            return self._reject(state, "INVALID_COUPON")
        coupon = SimCoupon(
            f"coupon-{len(state.coupons) + 1}",
            owner_id,
            face_value,
            threshold,
            currency,
        )
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(state, coupons=state.coupons + (coupon,)),
            (SimEvent("COUPON_ISSUED", "COUPON", coupon.coupon_id),),
        )

    def _do_create_order(self, state: SimulationState, action: SimAction) -> TransitionResult:
        if not state.users:
            return self._reject(state, "USER_NOT_FOUND")
        user_id = action.target_id or action.actor_id
        if not any(user.user_id == user_id for user in state.users):
            return self._reject(state, "USER_NOT_FOUND")
        amount = _money(action.argument("amount"))
        currency = Currency(str(action.argument("currency", "CNY")))
        if amount <= 0:
            return self._reject(state, "INVALID_AMOUNT")
        order = SimOrder(
            f"order-{len(state.orders) + 1}",
            user_id,
            amount,
            currency,
        )
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(state, orders=state.orders + (order,)),
            (SimEvent("ORDER_CREATED", "ORDER", order.order_id),),
        )

    def _do_apply_coupon(self, state: SimulationState, action: SimAction) -> TransitionResult:
        order_id, coupon_id = action.target_id, str(action.argument("coupon_id", ""))
        oi = next((i for i, item in enumerate(state.orders) if item.order_id == order_id), -1)
        ci = next((i for i, item in enumerate(state.coupons) if item.coupon_id == coupon_id), -1)
        if oi < 0 or ci < 0:
            return self._reject(state, "ASSET_NOT_FOUND")
        order, coupon = state.orders[oi], state.coupons[ci]
        if (
            order.status != "CREATED"
            or order.coupon_id
            or coupon.status != "AVAILABLE"
            or coupon.owner_id != order.user_id
            or coupon.currency is not order.currency
            or order.original_amount < coupon.threshold
        ):
            return self._reject(state, "COUPON_NOT_APPLICABLE")
        order = replace(
            order,
            coupon_id=coupon_id,
            discount_amount=min(coupon.face_value, order.original_amount),
        )
        coupon = replace(coupon, status="RESERVED", reserved_order_id=order_id)
        new_state = replace(
            state,
            orders=_replace(state.orders, oi, order),
            coupons=_replace(state.coupons, ci, coupon),
        )
        return TransitionResult(
            TransitionStatus.APPLIED, new_state, (SimEvent("COUPON_RESERVED", "COUPON", coupon_id),)
        )

    def _do_pay_order(self, state: SimulationState, action: SimAction) -> TransitionResult:
        oi = next(
            (i for i, item in enumerate(state.orders) if item.order_id == action.target_id), -1
        )
        if oi < 0 or state.orders[oi].status != "CREATED":
            return self._reject(state, "ORDER_NOT_PAYABLE")
        order = state.orders[oi]
        amount = order.original_amount - order.discount_amount
        ui = next(i for i, item in enumerate(state.users) if item.user_id == order.user_id)
        user = state.users[ui]
        if user.balance < amount:
            return self._reject(state, "INSUFFICIENT_BALANCE")
        granted = 0
        if self.points and self.points.spend_amount.amount > 0:
            granted = int(amount // self.points.spend_amount.amount) * self.points.points_granted
        user = replace(
            user,
            balance=user.balance - amount,
            points_balance=user.points_balance + granted,
            is_new_user=False,
        )
        order = replace(order, paid_amount=amount, points_granted=granted, status="PAID")
        coupons = state.coupons
        events = [SimEvent("PAYMENT_CAPTURED", "ORDER", order.order_id)]
        if order.coupon_id:
            ci = next(i for i, item in enumerate(coupons) if item.coupon_id == order.coupon_id)
            coupons = _replace(
                coupons,
                ci,
                replace(
                    coupons[ci],
                    status="USED",
                    used_order_id=order.order_id,
                    usage_count=coupons[ci].usage_count + 1,
                ),
            )
            events.append(SimEvent("COUPON_USED", "COUPON", order.coupon_id))
        if granted:
            events.append(
                SimEvent(
                    "POINTS_GRANTED",
                    "USER",
                    user.user_id,
                    (("amount", granted), ("order_id", order.order_id)),
                )
            )
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(
                state,
                users=_replace(state.users, ui, user),
                orders=_replace(state.orders, oi, order),
                coupons=coupons,
            ),
            tuple(events),
        )

    def _do_refund_order(self, state: SimulationState, action: SimAction) -> TransitionResult:
        oi = next(
            (i for i, item in enumerate(state.orders) if item.order_id == action.target_id), -1
        )
        if oi < 0:
            return self._reject(state, "ORDER_NOT_FOUND")
        order = state.orders[oi]
        amount = _money(action.argument("amount"))
        if (
            order.status not in {"PAID", "PARTIALLY_REFUNDED"}
            or amount <= 0
            or order.refunded_amount + amount > order.paid_amount
        ):
            return self._reject(state, "INVALID_REFUND")
        if self.refund and (
            not self.refund.allow_partial_refund
            and amount != order.paid_amount - order.refunded_amount
            or order.refund_count >= self.refund.maximum_refunds_per_order
        ):
            return self._reject(state, "REFUND_POLICY_REJECTED")
        ui = next(i for i, item in enumerate(state.users) if item.user_id == order.user_id)
        user = replace(state.users[ui], balance=state.users[ui].balance + amount)
        total = order.refunded_amount + amount
        full = total == order.paid_amount
        events = [
            SimEvent("REFUND_ISSUED", "ORDER", order.order_id, (("amount", format(amount, "f")),))
        ]
        if self.points and self.points.revoke_on_refund and order.points_granted:
            target_revoke = (
                order.points_granted
                if full
                else int(
                    (Decimal(order.points_granted) * total / order.paid_amount).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                )
            )
            revoke_delta = max(target_revoke - order.points_revoked, 0)
            if user.points_balance < revoke_delta:
                return self._reject(state, "POINTS_ALREADY_REDEEMED")
            if revoke_delta:
                user = replace(user, points_balance=user.points_balance - revoke_delta)
                events.append(
                    SimEvent(
                        "POINTS_REVOKED",
                        "USER",
                        user.user_id,
                        (("amount", revoke_delta), ("order_id", order.order_id)),
                    )
                )
        order = replace(
            order,
            refunded_amount=total,
            points_revoked=target_revoke
            if self.points and self.points.revoke_on_refund
            else order.points_revoked,
            refund_count=order.refund_count + 1,
            status="REFUNDED" if full else "PARTIALLY_REFUNDED",
        )
        coupons = state.coupons
        if full and order.coupon_id and self.promotion and self.promotion.restore_on_full_refund:
            ci = next(i for i, item in enumerate(coupons) if item.coupon_id == order.coupon_id)
            coupons = _replace(
                coupons,
                ci,
                replace(
                    coupons[ci], status="AVAILABLE", reserved_order_id=None, used_order_id=None
                ),
            )
            events.append(SimEvent("COUPON_RESTORED", "COUPON", order.coupon_id))
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(
                state,
                users=_replace(state.users, ui, user),
                orders=_replace(state.orders, oi, order),
                coupons=coupons,
            ),
            tuple(events),
        )

    def _do_redeem_points(self, state: SimulationState, action: SimAction) -> TransitionResult:
        if not state.users:
            return self._reject(state, "USER_NOT_FOUND")
        amount = int(action.argument("amount", 0))
        if amount <= 0 or state.users[0].points_balance < amount:
            return self._reject(state, "INSUFFICIENT_POINTS")
        user = replace(state.users[0], points_balance=state.users[0].points_balance - amount)
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(state, users=_replace(state.users, 0, user)),
            (SimEvent("POINTS_REDEEMED", "USER", user.user_id, (("amount", amount),)),),
        )

    def _do_activate_membership(
        self, state: SimulationState, action: SimAction
    ) -> TransitionResult:
        if not state.users or self.membership is None or state.memberships:
            return self._reject(state, "PRECONDITION_FAILED")
        user_id = action.target_id or action.actor_id
        ui = next((i for i, item in enumerate(state.users) if item.user_id == user_id), -1)
        if ui < 0:
            return self._reject(state, "USER_NOT_FOUND")
        paid = _money(action.argument("paid_amount", self.membership.price.amount))
        quantity = int(action.argument("quantity", self.membership.entitlement_quantity))
        if (
            paid != self.membership.price.amount
            or quantity != self.membership.entitlement_quantity
            or state.users[ui].balance < paid
        ):
            return self._reject(state, "MEMBERSHIP_RULE_MISMATCH")
        membership = SimMembership(
            "membership-1", state.users[ui].user_id, paid, self.membership.price.currency
        )
        entitlement = SimEntitlement("entitlement-1", membership.membership_id, quantity)
        user = replace(
            state.users[ui], balance=state.users[ui].balance - paid, membership_status="ACTIVE"
        )
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(
                state,
                users=_replace(state.users, ui, user),
                memberships=(membership,),
                entitlements=(entitlement,),
            ),
            (
                SimEvent("MEMBERSHIP_ACTIVATED", "MEMBERSHIP", membership.membership_id),
                SimEvent(
                    "ENTITLEMENT_GRANTED",
                    "ENTITLEMENT",
                    entitlement.entitlement_id,
                    (("quantity", quantity),),
                ),
            ),
        )

    def _do_consume_entitlement(
        self, state: SimulationState, action: SimAction
    ) -> TransitionResult:
        if not state.entitlements:
            return self._reject(state, "ENTITLEMENT_NOT_FOUND")
        quantity = int(action.argument("quantity", 0))
        ei = next(
            (
                i
                for i, item in enumerate(state.entitlements)
                if item.entitlement_id == action.target_id
            ),
            -1,
        )
        if ei < 0:
            return self._reject(state, "ENTITLEMENT_NOT_FOUND")
        entitlement = state.entitlements[ei]
        if quantity <= 0 or entitlement.available < quantity:
            return self._reject(state, "INSUFFICIENT_ENTITLEMENT")
        entitlement = replace(entitlement, consumed=entitlement.consumed + quantity)
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(state, entitlements=_replace(state.entitlements, ei, entitlement)),
            (
                SimEvent(
                    "ENTITLEMENT_CONSUMED",
                    "ENTITLEMENT",
                    entitlement.entitlement_id,
                    (("quantity", quantity),),
                ),
            ),
        )

    def _do_cancel_membership(self, state: SimulationState, action: SimAction) -> TransitionResult:
        if (
            not state.memberships
            or self.membership is None
            or state.memberships[0].status != "ACTIVE"
        ):
            return self._reject(state, "MEMBERSHIP_NOT_ACTIVE")
        membership, entitlement, user = state.memberships[0], state.entitlements[0], state.users[0]
        requested = bool(action.argument("refund_requested", False))
        if requested and self.membership.refund_policy == "UNUSED_ONLY" and entitlement.consumed:
            return self._reject(state, "ENTITLEMENT_CONSUMED")
        refundable = requested and self.membership.refund_policy != "NON_REFUNDABLE"
        refund = membership.paid_amount if refundable else Decimal("0")
        if self.membership.refund_policy == "PRORATED" and refundable:
            refund = (
                membership.paid_amount
                * Decimal(entitlement.available)
                / Decimal(entitlement.granted)
            ).quantize(CENT, rounding=ROUND_HALF_UP)
        membership = replace(
            membership, status="REFUNDED" if refund else "CANCELLED", refunded_amount=refund
        )
        entitlement = replace(entitlement, revoked=entitlement.revoked + entitlement.available)
        user = replace(user, balance=user.balance + refund, membership_status="INACTIVE")
        return TransitionResult(
            TransitionStatus.APPLIED,
            replace(
                state,
                users=_replace(state.users, 0, user),
                memberships=_replace(state.memberships, 0, membership),
                entitlements=_replace(state.entitlements, 0, entitlement),
            ),
            (
                SimEvent("MEMBERSHIP_CANCELLED", "MEMBERSHIP", membership.membership_id),
                SimEvent("ENTITLEMENT_REVOKED", "ENTITLEMENT", entitlement.entitlement_id),
            ),
        )
