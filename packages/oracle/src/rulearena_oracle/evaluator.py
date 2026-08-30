from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from rulearena_policy_schema import MembershipRule, PointsRule, RuleSpec

from .models import InvariantId, OracleFinding, OracleReport, OracleStatus

TERMINAL_ORDER_STATES = {"CANCELLED", "REFUNDED"}


def _state(snapshot: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = snapshot.get("state")
    return value if isinstance(value, Mapping) else None


def _items(state: Mapping[str, Any] | None, name: str) -> list[Mapping[str, Any]] | None:
    if state is None or not isinstance(state.get(name), list):
        return None
    values = state[name]
    return values if all(isinstance(item, Mapping) for item in values) else None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, float | bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if str(value) == str(parsed) else None


def _finding(
    invariant_id: InvariantId,
    status: OracleStatus,
    snapshots: Sequence[Mapping[str, Any]],
    explanation: str,
    *,
    evidence: dict[str, Any] | None = None,
    events: Iterable[Mapping[str, Any]] = (),
    actions: Iterable[Mapping[str, Any]] = (),
) -> OracleFinding:
    return OracleFinding(
        invariant_id=invariant_id,
        status=status,
        evidence=evidence or {},
        before_hash=str(snapshots[0].get("state_hash"))
        if snapshots and snapshots[0].get("state_hash")
        else None,
        after_hash=str(snapshots[-1].get("state_hash"))
        if snapshots and snapshots[-1].get("state_hash")
        else None,
        related_action_ids=tuple(
            str(item.get("receipt_id")) for item in actions if item.get("receipt_id")
        ),
        related_event_ids=tuple(
            str(item.get("event_id")) for item in events if item.get("event_id")
        ),
        explanation=explanation,
    )


class DeterministicOracle:
    """Evaluates normalized evidence only; it has no environment/profile input."""

    def evaluate(
        self,
        rule_spec: RuleSpec,
        *,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]] = (),
        events: Sequence[Mapping[str, Any]] = (),
    ) -> OracleReport:
        checks = (
            self._net_paid,
            self._refund_limit,
            self._coupon_consumption,
            self._points_conservation,
            self._terminal_monotonicity,
            self._entitlement_non_negative,
            self._entitlement_refund,
            self._idempotency,
        )
        return OracleReport(
            findings=tuple(check(rule_spec, snapshots, receipts, events) for check in checks)
        )

    def _orders(self, snapshots: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]] | None:
        return _items(_state(snapshots[-1]), "orders") if snapshots else None

    def _net_paid(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        orders = self._orders(snapshots)
        iid = InvariantId.NET_PAID_NON_NEGATIVE
        if orders is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "订单状态证据缺失。"
            )
        if not orders:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有订单。")
        bad = []
        for order in orders:
            paid, refunded = (
                _decimal(order.get("paid_amount")),
                _decimal(order.get("refunded_amount")),
            )
            if paid is None or refunded is None:
                return _finding(
                    iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "订单金额字段无效。"
                )
            if paid - refunded < 0:
                bad.append(
                    {"order_id": order.get("id"), "paid": str(paid), "refunded": str(refunded)}
                )
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "存在净支付为负的订单。" if bad else "所有订单净支付均非负。",
            evidence={"orders": bad},
        )

    def _refund_limit(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        orders = self._orders(snapshots)
        iid = InvariantId.REFUND_NOT_EXCEED_PAID
        if orders is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "订单状态证据缺失。"
            )
        if not orders:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有订单。")
        bad = []
        for order in orders:
            paid = _decimal(order.get("paid_amount"))
            refunded = _decimal(order.get("refunded_amount"))
            if paid is None or refunded is None:
                return _finding(
                    iid,
                    OracleStatus.INSUFFICIENT_EVIDENCE,
                    snapshots,
                    "订单金额字段无效。",
                )
            if refunded > paid:
                bad.append(str(order.get("id")))
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "累计退款超过实付。" if bad else "累计退款未超过实付。",
            evidence={"order_ids": bad},
        )

    def _coupon_consumption(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.COUPON_SINGLE_CONSUMPTION
        coupons = _items(_state(snapshots[-1]), "coupons") if snapshots else None
        if coupons is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "优惠券状态证据缺失。"
            )
        if not coupons:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有优惠券。")
        counts = Counter(
            str(event.get("aggregate_id"))
            for event in events
            if event.get("event_type") == "COUPON_USED"
        )
        bad = []
        for coupon in coupons:
            usage_count = _integer(coupon.get("usage_count", 0))
            if usage_count is None:
                return _finding(
                    iid,
                    OracleStatus.INSUFFICIENT_EVIDENCE,
                    snapshots,
                    "优惠券使用次数字段无效。",
                )
            coupon_id = str(coupon.get("id"))
            if usage_count > 1 or counts[coupon_id] > 1:
                bad.append(coupon_id)
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "优惠券被重复消费。" if bad else "每张优惠券最多消费一次。",
            evidence={"coupon_ids": bad},
            events=events,
        )

    def _points_conservation(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.POINTS_VALUE_CONSERVATION
        rule = next((r for r in spec.rules if isinstance(r, PointsRule)), None)
        if rule is None:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "规则不包含积分。")
        orders = self._orders(snapshots)
        users = _items(_state(snapshots[-1]), "users") if snapshots else None
        if orders is None or users is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "积分判定所需状态缺失。"
            )
        grants: dict[str, int] = defaultdict(int)
        revokes: dict[str, int] = defaultdict(int)
        for event in events:
            event_type = event.get("event_type")
            if event_type not in {"POINTS_GRANTED", "POINTS_REVOKED"}:
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, Mapping):
                continue
            order_id = str(payload.get("order_id", ""))
            try:
                amount = int(payload.get("amount", payload.get("points", 0)) or 0)
            except (TypeError, ValueError):
                return _finding(
                    iid,
                    OracleStatus.INSUFFICIENT_EVIDENCE,
                    snapshots,
                    "积分事件金额无效。",
                )
            if event_type == "POINTS_GRANTED":
                grants[order_id] += amount
            else:
                revokes[order_id] += amount
        bad: list[dict[str, Any]] = []
        for order in orders:
            paid = _decimal(order.get("paid_amount"))
            if paid is None:
                return _finding(
                    iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "订单实付金额无效。"
                )
            expected = int(paid // rule.spend_amount.amount) * rule.points_granted
            oid = str(order.get("id"))
            if int(order.get("points_granted", 0)) != expected or (
                grants and grants[oid] != expected
            ):
                bad.append(
                    {
                        "order_id": oid,
                        "expected_grant": expected,
                        "state_grant": order.get("points_granted"),
                        "event_grant": grants[oid],
                    }
                )
            refunded = _decimal(order.get("refunded_amount"))
            if refunded is None:
                return _finding(
                    iid,
                    OracleStatus.INSUFFICIENT_EVIDENCE,
                    snapshots,
                    "订单退款金额无效。",
                )
            expected_revoke = (
                int(Decimal(expected) * refunded / paid)
                if paid > 0 and rule.revoke_on_refund
                else 0
            )
            if events and revokes[oid] != expected_revoke:
                bad.append(
                    {
                        "order_id": oid,
                        "expected_revoke": expected_revoke,
                        "event_revoke": revokes[oid],
                    }
                )
        for user in users:
            points_balance = _integer(user.get("points_balance", 0))
            if points_balance is None:
                return _finding(
                    iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "用户积分字段无效。"
                )
            if points_balance < 0:
                bad.append({"negative_user_balance": True, "user_id": user.get("id")})
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "积分价值流与冻结规则不一致。" if bad else "积分价值流守恒。",
            evidence={"mismatches": bad},
            events=events,
        )

    def _terminal_monotonicity(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.ORDER_TERMINAL_MONOTONICITY
        if len(snapshots) < 2:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "至少需要两个连续快照。"
            )
        histories: dict[str, list[str]] = defaultdict(list)
        for snapshot in snapshots:
            orders = _items(_state(snapshot), "orders")
            if orders is None:
                return _finding(
                    iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "快照缺少订单集合。"
                )
            for order in orders:
                histories[str(order.get("id"))].append(str(order.get("status")))
        bad = {
            oid: values
            for oid, values in histories.items()
            if any(
                values[index] in TERMINAL_ORDER_STATES and values[index + 1] != values[index]
                for index in range(len(values) - 1)
            )
        }
        if not histories:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有订单。")
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "订单从终态非法回退。" if bad else "订单终态保持单调。",
            evidence={"histories": bad},
        )

    def _entitlement_non_negative(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.ENTITLEMENT_NON_NEGATIVE
        values = _items(_state(snapshots[-1]), "entitlements") if snapshots else None
        if values is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "权益状态证据缺失。"
            )
        if not values:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有权益。")
        bad = []
        for item in values:
            granted = _integer(item.get("granted_quantity", 0))
            consumed = _integer(item.get("consumed_quantity", 0))
            revoked = _integer(item.get("revoked_quantity", 0))
            if granted is None or consumed is None or revoked is None:
                return _finding(
                    iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "权益数量字段无效。"
                )
            available = (
                granted
                - consumed
                - revoked
            )
            if available < 0:
                bad.append({"entitlement_id": item.get("id"), "available": available})
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "权益余额为负。" if bad else "权益余额均非负。",
            evidence={"entitlements": bad},
        )

    def _entitlement_refund(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.ENTITLEMENT_REFUND_CONSISTENCY
        rule = next((r for r in spec.rules if isinstance(r, MembershipRule)), None)
        if rule is None:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "规则不包含会员权益。")
        state = _state(snapshots[-1]) if snapshots else None
        memberships, entitlements = _items(state, "memberships"), _items(state, "entitlements")
        if memberships is None or entitlements is None:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "会员或权益证据缺失。"
            )
        if not memberships:
            return _finding(iid, OracleStatus.NOT_APPLICABLE, snapshots, "没有会员。")
        by_membership = {str(e.get("membership_id")): e for e in entitlements}
        bad = []
        for membership in memberships:
            if membership.get("status") in {"CANCELLED", "REFUNDED"}:
                entitlement = by_membership.get(str(membership.get("id")))
                if entitlement is None:
                    return _finding(
                        iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "会员缺少关联权益。"
                    )
                granted = _integer(entitlement.get("granted_quantity", 0))
                consumed = _integer(entitlement.get("consumed_quantity", 0))
                revoked = _integer(entitlement.get("revoked_quantity", 0))
                if granted is None or consumed is None or revoked is None:
                    return _finding(
                        iid,
                        OracleStatus.INSUFFICIENT_EVIDENCE,
                        snapshots,
                        "权益数量字段无效。",
                    )
                available = granted - consumed - revoked
                if available != 0:
                    bad.append({"membership_id": membership.get("id"), "available": available})
                if (
                    rule.refund_policy == "NON_REFUNDABLE"
                    and membership.get("status") == "REFUNDED"
                ):
                    bad.append({"membership_id": membership.get("id"), "reason": "non_refundable"})
                if (
                    rule.refund_policy == "UNUSED_ONLY"
                    and membership.get("status") == "REFUNDED"
                    and consumed > 0
                ):
                    bad.append(
                        {"membership_id": membership.get("id"), "reason": "used_then_refunded"}
                    )
                if membership.get("status") == "REFUNDED":
                    membership_id = str(membership.get("id"))
                    entitlement_id = str(entitlement.get("id"))
                    refund_indexes = []
                    for index, event in enumerate(events):
                        payload = event.get("payload", {})
                        payload_membership_id = (
                            str(payload.get("membership_id"))
                            if isinstance(payload, Mapping) and payload.get("membership_id")
                            else ""
                        )
                        if event.get("event_type") == "MEMBERSHIP_REFUNDED" and (
                            str(event.get("aggregate_id")) == membership_id
                            or payload_membership_id == membership_id
                        ):
                            refund_indexes.append(index)
                    if refund_indexes and any(
                        later.get("event_type") == "ENTITLEMENT_CONSUMED"
                        and str(later.get("aggregate_id")) == entitlement_id
                        for later in events[min(refund_indexes) + 1 :]
                    ):
                        bad.append(
                            {"membership_id": membership_id, "reason": "consumed_after_refund"}
                        )
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "退款与权益有效性不一致。" if bad else "会员终止后权益状态与规则一致。",
            evidence={"memberships": bad},
        )

    def _idempotency(
        self,
        spec: RuleSpec,
        snapshots: Sequence[Mapping[str, Any]],
        receipts: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
    ) -> OracleFinding:
        iid = InvariantId.IDEMPOTENT_EFFECT
        keyed = [r for r in receipts if r.get("idempotency_key")]
        if not keyed:
            return _finding(
                iid, OracleStatus.INSUFFICIENT_EVIDENCE, snapshots, "没有幂等键回执证据。"
            )
        groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for receipt in keyed:
            groups[
                (
                    str(receipt.get("action_type", receipt.get("action"))),
                    str(receipt.get("idempotency_key")),
                )
            ].append(receipt)
        event_counts = Counter(
            (str(e.get("event_type")), str(e.get("idempotency_key")))
            for e in events
            if e.get("idempotency_key")
        )
        bad = []
        for key, values in groups.items():
            successful = [
                value for value in values if value.get("status") in {"SUCCEEDED", "APPLIED"}
            ]
            fingerprints = {
                (
                    str(value.get("status")),
                    repr(value.get("result")),
                    repr(value.get("monetary_effects")),
                )
                for value in values
            }
            duplicate_effect = any(
                count > 1 and event_key == key[1] for (_, event_key), count in event_counts.items()
            )
            if len(fingerprints) > 1 or (len(successful) > 1 and duplicate_effect):
                bad.append({"action_type": key[0], "idempotency_key": key[1]})
        return _finding(
            iid,
            OracleStatus.VIOLATED if bad else OracleStatus.SATISFIED,
            snapshots,
            "同一幂等请求产生不一致或重复副作用。" if bad else "重复请求未产生额外副作用。",
            evidence={"keys": bad},
            events=events,
            actions=receipts,
        )
