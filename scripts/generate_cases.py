"""Build benchmark cases from parameterised defect templates, then verify each one.

Hand-writing a case means hand-writing three things that must agree: the ground-truth
trace, the defect the environment has to exhibit, and the invariant the Oracle has to
report. Getting one of them wrong is silent -- the case still loads and still looks like
a case. So cases are derived here from one template per defect axis, and every generated
case is replayed against the real Sandbox before it is written:

  1. under its own axis, its ground truth must confirm the invariant it is labelled with;
  2. under a faithful environment, it must confirm nothing;
  3. under every sibling axis of the same scenario, it must confirm nothing.

Property 3 is the one that makes a discovery rate attributable. Without it a path can
trip a defect the case never meant to measure and be scored as a miss.

Usage:
    uv run python scripts/generate_cases.py --suite development --write
    uv run python scripts/generate_cases.py --suite hidden --write
    uv run python scripts/generate_cases.py --suite development      # verify only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rulearena_attack_runtime import ReplayClassification, SandboxReplayRunner
from rulearena_domain_contracts import AXES_BY_SCENARIO, ActionType, DefectAxis
from rulearena_oracle import InvariantId
from rulearena_policy_schema import RuleSpec, ScenarioType
from rulearena_reference_simulator import SimAction

ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT = ROOT / "benchmarks" / "development-v1.json"
HIDDEN = ROOT / ".env.hidden-suite.json"
HIDDEN_MANIFEST = ROOT / "benchmarks" / "hidden-manifest.json"

USER = "user-1"


def _money(amount: str) -> str:
    return f"{float(amount):.2f}"


@dataclass(frozen=True)
class Blueprint:
    """A case, fully determined by its axis plus the numbers that instantiate it."""

    case_id: str
    axis: DefectAxis
    scenario: ScenarioType
    rule_spec_ref: str
    invariant: InvariantId
    actions: tuple[dict[str, Any], ...]
    tags: tuple[str, ...]
    reason: str

    @property
    def scenario_version_id(self) -> str:
        return {
            "promotion": "promotion-v1",
            "refund_points": "refund_points-v1",
            "membership": "membership-v1",
        }[self.rule_spec_ref]

    @property
    def rule_version_id(self) -> str:
        return {
            "promotion": "00000000-0000-0000-0000-000000000101",
            "refund_points": "00000000-0000-0000-0000-000000000102",
            "membership": "00000000-0000-0000-0000-000000000103",
        }[self.rule_spec_ref]


# --- templates: one per axis ---------------------------------------------------------
#
# Each returns the shortest trace that makes the axis observable as an invariant breach
# rather than as a mere state difference. The two are not the same: a full refund leaving
# a coupon AVAILABLE is only a violation once the coupon is spent a second time.


def _create_user(balance: str = "500.00") -> dict[str, Any]:
    return {"action_type": "CREATE_USER", "arguments": {"initial_balance": balance}}


def coupon_reused(order: str, coupon_value: str, threshold: str) -> tuple[dict[str, Any], ...]:
    """Refund an order in full, then spend the restored coupon on a second order.

    The refund has to equal what was actually paid -- the order amount less the discount
    -- or the order never reaches REFUNDED and the coupon is never restored at all.
    """
    paid = _money(str(float(order) - float(coupon_value)))
    return (
        _create_user(),
        {
            "action_type": "ISSUE_COUPON",
            "target_id": USER,
            "arguments": {"value": coupon_value, "threshold": threshold},
        },
        {
            "action_type": "CREATE_ORDER",
            "target_id": USER,
            "arguments": {"amount": _money(order)},
        },
        {
            "action_type": "APPLY_COUPON",
            "target_id": "order-1",
            "arguments": {"coupon_id": "coupon-1"},
        },
        {"action_type": "PAY_ORDER", "target_id": "order-1", "arguments": {}},
        {
            "action_type": "REFUND_ORDER",
            "target_id": "order-1",
            "arguments": {"amount": paid},
        },
        {
            "action_type": "CREATE_ORDER",
            "target_id": USER,
            "arguments": {"amount": _money(order)},
        },
        {
            "action_type": "APPLY_COUPON",
            "target_id": "order-2",
            "arguments": {"coupon_id": "coupon-1"},
        },
        {"action_type": "PAY_ORDER", "target_id": "order-2", "arguments": {}},
    )


def over_refund(paid: str, first: str, second: str) -> tuple[dict[str, Any], ...]:
    """Two partial refunds that together exceed what was paid.

    The ceiling has to be lifted to the original amount for the second to land: with the
    faithful remainder as the ceiling the sandbox refuses it before any invariant is at
    stake.
    """
    return (
        _create_user(),
        {
            "action_type": "CREATE_ORDER",
            "target_id": USER,
            "arguments": {"amount": _money(paid)},
        },
        {"action_type": "PAY_ORDER", "target_id": "order-1", "arguments": {}},
        {
            "action_type": "REFUND_ORDER",
            "target_id": "order-1",
            "arguments": {"amount": _money(first)},
        },
        {
            "action_type": "REFUND_ORDER",
            "target_id": "order-1",
            "arguments": {"amount": _money(second)},
        },
    )


def points_regranted(amount: str, refund: str | None = None) -> tuple[dict[str, Any], ...]:
    """Pay, then refund: the points the payment earned must not survive as a second grant."""
    return (
        _create_user(),
        {
            "action_type": "CREATE_ORDER",
            "target_id": USER,
            "arguments": {"amount": _money(amount)},
        },
        {"action_type": "PAY_ORDER", "target_id": "order-1", "arguments": {}},
        {
            "action_type": "REFUND_ORDER",
            "target_id": "order-1",
            "arguments": {"amount": _money(refund if refund is not None else amount)},
        },
    )


def over_redeemed(amount: str, redeem: int) -> tuple[dict[str, Any], ...]:
    """Redeem more points than the balance holds."""
    return (
        _create_user(),
        {
            "action_type": "CREATE_ORDER",
            "target_id": USER,
            "arguments": {"amount": _money(amount)},
        },
        {"action_type": "PAY_ORDER", "target_id": "order-1", "arguments": {}},
        {
            "action_type": "REDEEM_POINTS",
            "target_id": USER,
            "arguments": {"amount": redeem},
        },
    )


def refunded_after_consumption(
    price: str, quantity: int, consumed: int
) -> tuple[dict[str, Any], ...]:
    """Consume part of a UNUSED_ONLY membership, then cancel it for a full refund."""
    return (
        _create_user(),
        {
            "action_type": "ACTIVATE_MEMBERSHIP",
            "target_id": USER,
            "arguments": {"paid_amount": _money(price), "quantity": quantity},
        },
        {
            "action_type": "CONSUME_ENTITLEMENT",
            "target_id": "entitlement-1",
            "arguments": {"quantity": consumed},
        },
        {
            "action_type": "CANCEL_MEMBERSHIP",
            "target_id": "membership-1",
            "arguments": {"refund_requested": True},
        },
    )


def entitlement_survives_refund(
    price: str, quantity: int, consumed: int
) -> tuple[dict[str, Any], ...]:
    """Refund an unused membership, then keep spending the entitlement it granted."""
    return (
        _create_user(),
        {
            "action_type": "ACTIVATE_MEMBERSHIP",
            "target_id": USER,
            "arguments": {"paid_amount": _money(price), "quantity": quantity},
        },
        {
            "action_type": "CANCEL_MEMBERSHIP",
            "target_id": "membership-1",
            "arguments": {"refund_requested": True},
        },
        {
            "action_type": "CONSUME_ENTITLEMENT",
            "target_id": "entitlement-1",
            "arguments": {"quantity": consumed},
        },
    )


def over_consumed(price: str, quantity: int, consumed: int) -> tuple[dict[str, Any], ...]:
    """Consume more entitlement than was granted."""
    return (
        _create_user(),
        {
            "action_type": "ACTIVATE_MEMBERSHIP",
            "target_id": USER,
            "arguments": {"paid_amount": _money(price), "quantity": quantity},
        },
        {
            "action_type": "CONSUME_ENTITLEMENT",
            "target_id": "entitlement-1",
            "arguments": {"quantity": consumed},
        },
    )


PROMOTION, POINTS, MEMBERSHIP = (
    ScenarioType.PROMOTION,
    ScenarioType.REFUND_POINTS,
    ScenarioType.MEMBERSHIP_ENTITLEMENT,
)
COUPON_AXIS = DefectAxis.COUPON_RESTORED_ON_REFUND
REFUND_AXIS = DefectAxis.REFUND_AGAINST_ORIGINAL
GRANT_AXIS = DefectAxis.POINTS_GRANTED_AGAIN_ON_REFUND
REDEEM_AXIS = DefectAxis.POINTS_OVERREDEMPTION
CONSUMED_AXIS = DefectAxis.FULL_REFUND_AFTER_CONSUMPTION
RESIDUAL_AXIS = DefectAxis.ENTITLEMENT_LEFT_AFTER_REFUND
OVERCONSUME_AXIS = DefectAxis.ENTITLEMENT_OVERCONSUMPTION

COUPON_SPEC = ("50.00", "150.00")


def _coupon(case_id: str, order: str, tags: tuple[str, ...]) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=COUPON_AXIS,
        scenario=PROMOTION,
        rule_spec_ref="promotion",
        invariant=InvariantId.COUPON_SINGLE_CONSUMPTION,
        actions=coupon_reused(order, *COUPON_SPEC),
        tags=tags,
        reason=(
            f"全额退款 {float(order) - float(COUPON_SPEC[0]):.2f} 元后优惠券回到可用状态，"
            f"随即在第二笔 {_money(order)} 元订单上再次使用，usage_count=2。"
        ),
    )


def _over_refund(case_id: str, paid: str, first: str, second: str) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=REFUND_AXIS,
        scenario=PROMOTION,
        rule_spec_ref="promotion",
        invariant=InvariantId.REFUND_NOT_EXCEED_PAID,
        actions=over_refund(paid, first, second),
        tags=("partial-refund", "repeat"),
        reason=(
            f"实付 {_money(paid)} 元，两次部分退款各为 {_money(first)} / {_money(second)} 元；"
            f"第二次的额度上限被放宽回原始实付额，累计 {float(first) + float(second):.2f} 元"
            "超过实付。"
        ),
    )


def _regrant(case_id: str, amount: str, refund: str | None = None) -> Blueprint:
    refunded = refund if refund is not None else amount
    return Blueprint(
        case_id=case_id,
        axis=GRANT_AXIS,
        scenario=POINTS,
        rule_spec_ref="refund_points",
        invariant=InvariantId.POINTS_VALUE_CONSERVATION,
        actions=points_regranted(amount, refund),
        tags=("refund", "points", "value-conservation"),
        reason=(
            f"{_money(amount)} 元订单支付后获得 {float(amount):.0f} 积分，"
            f"退款 {_money(refunded)} 元时未按比例撤销，而是再次发放，用户净得积分。"
        ),
    )


def _over_redeem(case_id: str, amount: str, redeem: int) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=REDEEM_AXIS,
        scenario=POINTS,
        rule_spec_ref="refund_points",
        invariant=InvariantId.POINTS_VALUE_CONSERVATION,
        actions=over_redeemed(amount, redeem),
        tags=("redeem", "boundary"),
        reason=(
            f"支付 {_money(amount)} 元后余额仅 {float(amount):.0f} 积分，却成功兑换 {redeem} 积分，"
            "余额被扣成负数。"
        ),
    )


def _consumed_refund(case_id: str, price: str, quantity: int, consumed: int) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=CONSUMED_AXIS,
        scenario=MEMBERSHIP,
        rule_spec_ref="membership",
        invariant=InvariantId.ENTITLEMENT_REFUND_CONSISTENCY,
        actions=refunded_after_consumption(price, quantity, consumed),
        tags=("membership", "refund", "consumed"),
        reason=(
            f"已消费 {consumed}/{quantity} 份权益的会员仍获全额退款 {_money(price)} 元，"
            "违反 UNUSED_ONLY 退款政策。"
        ),
    )


def _residual(case_id: str, price: str, quantity: int, consumed: int) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=RESIDUAL_AXIS,
        scenario=MEMBERSHIP,
        rule_spec_ref="membership",
        invariant=InvariantId.ENTITLEMENT_REFUND_CONSISTENCY,
        actions=entitlement_survives_refund(price, quantity, consumed),
        tags=("cancel", "residual-entitlement"),
        reason=(
            f"未使用会员全额退款后，其 {quantity} 份权益未被撤销，仍可继续消费 {consumed} 份。"
        ),
    )


def _over_consume(case_id: str, price: str, quantity: int, consumed: int) -> Blueprint:
    return Blueprint(
        case_id=case_id,
        axis=OVERCONSUME_AXIS,
        scenario=MEMBERSHIP,
        rule_spec_ref="membership",
        invariant=InvariantId.ENTITLEMENT_NON_NEGATIVE,
        actions=over_consumed(price, quantity, consumed),
        tags=("entitlement", "overconsume"),
        reason=(
            f"一次消费 {consumed} 份权益，超过授予的 {quantity} 份，可用数量变为负数。"
        ),
    )


MEMBERSHIP_PRICE = "50.00"
MEMBERSHIP_QUANTITY = 2

# The parameters below are the whole difference between one case and the next. Amounts
# stay inside what the frozen RuleSpec accepts: the coupon keeps the spec's value and
# threshold, and the membership keeps its price and granted quantity.
DEVELOPMENT_ADDITIONS = (
    _over_refund("dev-promotion-07", "300.00", "180.00", "160.00"),
    _regrant("dev-refund-06", "150.00"),
    _over_redeem("dev-refund-07", "120.00", 200),
    _consumed_refund("dev-membership-06", MEMBERSHIP_PRICE, MEMBERSHIP_QUANTITY, 2),
    _residual("dev-membership-07", MEMBERSHIP_PRICE, MEMBERSHIP_QUANTITY, 2),
)

# The hidden suite must not be the development suite with different prices: it keeps
# distinct amounts throughout, so a strategy tuned to the published cases has nothing to
# memorise.
HIDDEN_ADDITIONS = (
    _coupon("hidden-09", "260.00", ("coupon-reuse", "sequence")),
    _over_refund("hidden-10", "260.00", "140.00", "130.00"),
    _regrant("hidden-11", "240.00"),
    _regrant("hidden-12", "180.00", "90.00"),
    _over_redeem("hidden-13", "90.00", 150),
    _over_redeem("hidden-14", "210.00", 400),
    _consumed_refund("hidden-15", MEMBERSHIP_PRICE, MEMBERSHIP_QUANTITY, 2),
    _residual("hidden-16", MEMBERSHIP_PRICE, MEMBERSHIP_QUANTITY, 2),
    _over_consume("hidden-17", MEMBERSHIP_PRICE, MEMBERSHIP_QUANTITY, 4),
)


def _document(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))


def _spec(document: dict[str, Any], blueprint: Blueprint) -> RuleSpec:
    return RuleSpec.model_validate_json(json.dumps(document["rule_specs"][blueprint.rule_spec_ref]))


async def _confirms(
    replay: SandboxReplayRunner,
    spec: RuleSpec,
    actions: Sequence[dict[str, Any]],
    invariant: InvariantId,
    *,
    axes: Sequence[str] | None,
) -> bool:
    parsed = tuple(
        SimAction(
            action_type=ActionType(action["action_type"]),
            actor_id=str(action.get("actor_id", USER)),
            target_id=(str(action["target_id"]) if action.get("target_id") else None),
            arguments=tuple(sorted(dict(action["arguments"]).items())),
        )
        for action in actions
    )
    result = await replay.replay(
        spec, parsed, invariant, sandbox_version="vulnerable", defect_axes=axes
    )
    return result.classification is ReplayClassification.CONFIRMED_VIOLATION


@dataclass(frozen=True)
class Verdict:
    blueprint: Blueprint
    declared: bool
    faithful: bool
    siblings: dict[str, bool]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.declared
            and not self.faithful
            and not any(self.siblings.values())
        )

    def describe(self) -> str:
        if self.error:
            return f"ERROR {self.error}"
        parts = [f"declared={'confirm' if self.declared else 'MISS'}"]
        parts.append(f"faithful={'confirm' if self.faithful else 'silent'}")
        leaked = sorted(name for name, hit in self.siblings.items() if hit)
        parts.append(f"siblings={'leak:' + ','.join(leaked) if leaked else 'silent'}")
        return " ".join(parts)


async def verify(
    blueprints: Sequence[Blueprint],
    document: dict[str, Any],
    base_url: str,
    token: str,
) -> list[Verdict]:
    replay = SandboxReplayRunner(base_url, token, timeout=60.0)
    semaphore = asyncio.Semaphore(4)

    async def check(blueprint: Blueprint) -> Verdict:
        spec = _spec(document, blueprint)
        siblings = sorted(
            axis.value
            for axis in AXES_BY_SCENARIO[blueprint.scenario]
            if axis is not blueprint.axis
        )
        observed: dict[str, bool] = {}
        try:
            async with semaphore:
                declared = await _confirms(
                    replay, spec, blueprint.actions, blueprint.invariant,
                    axes=[blueprint.axis.value],
                )
                faithful = await _confirms(
                    replay, spec, blueprint.actions, blueprint.invariant, axes=[]
                )
                for axis in siblings:
                    observed[axis] = await _confirms(
                        replay, spec, blueprint.actions, blueprint.invariant, axes=[axis]
                    )
        except Exception as error:  # noqa: BLE001 - a broken case must be reported, not hidden
            return Verdict(blueprint, False, False, {}, f"{type(error).__name__}: {error}")
        return Verdict(blueprint, declared, faithful, observed)

    return list(await asyncio.gather(*(check(bp) for bp in blueprints)))


def _case_row(
    blueprint: Blueprint, benchmark_version: str, budget: dict[str, Any], visibility: str
) -> dict[str, Any]:
    return {
        "case_id": blueprint.case_id,
        "benchmark_version": benchmark_version,
        "visibility": visibility,
        "scenario_type": blueprint.scenario.value,
        "tags": list(blueprint.tags),
        "budget": budget,
        "rule_version_id": blueprint.rule_version_id,
        "scenario_version_id": blueprint.scenario_version_id,
        "sandbox_version": "vulnerable",
        "defect_axes": [blueprint.axis.value],
        "oracle_version": "1.0",
        "rule_spec_ref": blueprint.rule_spec_ref,
        "expected_outcome": "VULNERABLE",
        "expected_invariant_ids": [blueprint.invariant.value],
        "construction_reason": blueprint.reason,
        "ground_truth_replays": [True, True, True],
        "ground_truth_actions": [dict(action) for action in blueprint.actions],
    }


def _insert_after_last_case_group(cases: list[dict[str, Any]], row: dict[str, Any]) -> None:
    """Keep each scenario's cases together so the file stays readable."""
    scenario = row["scenario_type"]
    last = max(
        (index for index, case in enumerate(cases) if case["scenario_type"] == scenario),
        default=len(cases) - 1,
    )
    cases.insert(last + 1, row)


def _write_json(path: Path, document: Any) -> None:
    text = json.dumps(document, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    if path.read_bytes().endswith(b"\r\n"):
        text += "\r\n"
    path.write_bytes(text.encode("utf-8"))


def _budget_of(document: dict[str, Any]) -> dict[str, Any]:
    budgets = {json.dumps(case["budget"], sort_keys=True) for case in document["cases"]}
    if len(budgets) != 1:
        raise SystemExit("the suite must declare exactly one budget before expanding")
    budget: dict[str, Any] = json.loads(budgets.pop())
    return budget


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("development", "hidden"), required=True)
    parser.add_argument("--write", action="store_true", help="merge the verified cases in")
    parser.add_argument("--base-url", default=os.getenv("SANDBOX_HTTP_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--token", default=os.getenv("INTERNAL_SERVICE_TOKEN", ""))
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("set INTERNAL_SERVICE_TOKEN (or pass --token)")
    path = DEVELOPMENT if args.suite == "development" else HIDDEN
    blueprints = DEVELOPMENT_ADDITIONS if args.suite == "development" else HIDDEN_ADDITIONS
    document = _document(path)
    known = {case["case_id"] for case in document["cases"]}
    pending = [bp for bp in blueprints if bp.case_id not in known]
    if not pending:
        print(f"{args.suite}: nothing to add, {len(known)} cases already present")
        return 0

    verdicts = asyncio.run(verify(pending, document, args.base_url, args.token))
    failed = [verdict for verdict in verdicts if not verdict.ok]
    for verdict in verdicts:
        mark = "ok  " if verdict.ok else "FAIL"
        print(f"{mark} {verdict.blueprint.case_id:<22} {verdict.describe()}")
    if failed:
        print(f"\n{len(failed)} of {len(verdicts)} blueprints did not verify; nothing written.")
        return 1

    if not args.write:
        print(f"\n{len(verdicts)} blueprints verified (no --write, nothing changed).")
        return 0

    benchmark_version = document["cases"][0]["benchmark_version"]
    budget = _budget_of(document)
    for verdict in verdicts:
        _insert_after_last_case_group(
            document["cases"],
            _case_row(verdict.blueprint, benchmark_version, budget, args.suite),
        )
    _write_json(path, document)

    if args.suite == "hidden":
        manifest = _document(HIDDEN_MANIFEST)
        public = (
            "case_id",
            "benchmark_version",
            "visibility",
            "scenario_type",
            "tags",
            "budget",
        )
        for verdict in verdicts:
            row = _case_row(verdict.blueprint, benchmark_version, budget, args.suite)
            manifest.append({key: row[key] for key in public})
        manifest.sort(key=lambda row: row["case_id"])
        _write_json(HIDDEN_MANIFEST, manifest)

    print(f"\nwrote {len(verdicts)} cases into {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
