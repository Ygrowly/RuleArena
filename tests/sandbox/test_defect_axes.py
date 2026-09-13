"""A run's environment exhibits the defect axes it declares, and no others.

The single `vulnerable` flag made every case in a scenario share an environment that
could exhibit every defect, so a path could trip a different case's defect and be scored
against a label it never touched -- which is how two Oracle-confirmed violations went
uncounted in the golden-v3 run.

Each probe below is the observable of one axis, taken from the frozen rule: the value
that should not move when the implementation is faithful. Running all seven probes
against a run declaring a single axis must produce the identity matrix.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

pytestmark = pytest.mark.sandbox

AXES = (
    "COUPON_RESTORED_ON_REFUND",
    "REFUND_AGAINST_ORIGINAL",
    "POINTS_GRANTED_AGAIN_ON_REFUND",
    "POINTS_OVERREDEMPTION",
    "FULL_REFUND_AFTER_CONSUMPTION",
    "ENTITLEMENT_LEFT_AFTER_REFUND",
    "ENTITLEMENT_OVERCONSUMPTION",
)


def _base(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return f"{parts.scheme or 'http'}://{parts.netloc}"


class Sandbox:
    """A reused client on purpose.

    Module-level `httpx.post` builds a fresh Client per call, and that construction costs
    close to a second on this machine -- the probes below made the suite look like it hung
    when it was only paying client setup forty times over.
    """

    def __init__(self, url: str, token: str) -> None:
        self.url = _base(url)
        self.token = token
        self._client = httpx.Client(
            headers={"X-Internal-Service-Token": token}, timeout=30, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._client.post(f"{self.url}{path}", json=payload).json())

    def run(self, scenario: str, axes: tuple[str, ...]) -> str:
        created = self._post(
            "/internal/runs",
            {
                "schema_version": "1.0",
                "scenario_type": scenario,
                "sandbox_version": "fixed",
                "defect_axes": list(axes),
            },
        )
        return str(created["run_id"])

    def create(
        self, scenario: str, sandbox_version: str, axes: list[str] | None = None
    ) -> httpx.Response:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "scenario_type": scenario,
            "sandbox_version": sandbox_version,
        }
        if axes is not None:
            payload["defect_axes"] = axes
        return self._client.post(f"{self.url}/internal/runs", json=payload)

    def act(
        self, run: str, index: int, action: str, target: str | None = None, **arguments: object
    ) -> str:
        receipt = self._post(
            f"/internal/runs/{run}/actions",
            {
                "action": action,
                "actor_id": "user-1",
                "target_id": target,
                "arguments": arguments,
                "idempotency_key": f"{run}:{index}",
            },
        )
        return str(receipt.get("status", "REJECTED"))

    def state(self, run: str) -> dict[str, Any]:
        response = self._client.get(f"{self.url}/internal/runs/{run}/snapshot")
        return dict(response.json()["state"])


def _coupon_restored(box: Sandbox, run: str) -> bool:
    """A full refund should leave the coupon consumed, not available for reuse."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "issue_coupon", "user-1", value="50.00", threshold="150.00")
    box.act(run, 3, "create_order", "user-1", amount="150.00")
    box.act(run, 4, "apply_coupon", "order-1", coupon_id="coupon-1")
    box.act(run, 5, "pay_order", "order-1")
    box.act(run, 6, "refund_order", "order-1", amount="100.00")
    coupons = box.state(run)["coupons"]
    return bool(coupons) and coupons[0]["status"] == "AVAILABLE"


def _refund_against_original(box: Sandbox, run: str) -> bool:
    """Two partial refunds each larger than the remainder: the ceiling should be the remainder."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "create_order", "user-1", amount="100.00")
    box.act(run, 3, "pay_order", "order-1")
    first = box.act(run, 4, "refund_order", "order-1", amount="60.00")
    second = box.act(run, 5, "refund_order", "order-1", amount="60.00")
    return first == "SUCCEEDED" and second == "SUCCEEDED"


def _points_granted_again(box: Sandbox, run: str) -> bool:
    """A refund should revoke the points it funded, leaving none behind."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "create_order", "user-1", amount="100.00")
    box.act(run, 3, "pay_order", "order-1")
    box.act(run, 4, "refund_order", "order-1", amount="100.00")
    return int(box.state(run)["users"][0]["points_balance"]) > 0


def _points_overredemption(box: Sandbox, run: str) -> bool:
    """Redeeming more than the balance should be refused."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "create_order", "user-1", amount="100.00")
    box.act(run, 3, "pay_order", "order-1")
    return box.act(run, 4, "redeem_points", "user-1", amount="300") == "SUCCEEDED"


def _full_refund_after_consumption(box: Sandbox, run: str) -> bool:
    """An UNUSED_ONLY membership that has been consumed should not refund in full."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "activate_membership", "user-1", paid_amount="50.00", quantity=2)
    box.act(run, 3, "consume_entitlement", "entitlement-1", quantity=1)
    box.act(run, 4, "cancel_membership", "membership-1", refund_requested=True)
    memberships = box.state(run)["memberships"]
    return bool(memberships) and memberships[0]["status"] == "REFUNDED"


def _entitlement_left_after_refund(box: Sandbox, run: str) -> bool:
    """Refunding a membership should revoke the entitlement it granted."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "activate_membership", "user-1", paid_amount="50.00", quantity=2)
    box.act(run, 3, "cancel_membership", "membership-1", refund_requested=True)
    entitlements = box.state(run)["entitlements"]
    if not entitlements:
        return False
    item = entitlements[0]
    available = (
        int(item["granted_quantity"])
        - int(item["consumed_quantity"])
        - int(item["revoked_quantity"])
    )
    return available > 0


def _entitlement_overconsumption(box: Sandbox, run: str) -> bool:
    """Consuming more than was granted should be refused."""
    box.act(run, 1, "create_user", initial_balance="500.00")
    box.act(run, 2, "activate_membership", "user-1", paid_amount="50.00", quantity=2)
    return box.act(run, 3, "consume_entitlement", "entitlement-1", quantity=3) == "SUCCEEDED"


PROBES = {
    "COUPON_RESTORED_ON_REFUND": ("PROMOTION", _coupon_restored),
    "REFUND_AGAINST_ORIGINAL": ("PROMOTION", _refund_against_original),
    "POINTS_GRANTED_AGAIN_ON_REFUND": ("REFUND_POINTS", _points_granted_again),
    "POINTS_OVERREDEMPTION": ("REFUND_POINTS", _points_overredemption),
    "FULL_REFUND_AFTER_CONSUMPTION": ("MEMBERSHIP_ENTITLEMENT", _full_refund_after_consumption),
    "ENTITLEMENT_LEFT_AFTER_REFUND": ("MEMBERSHIP_ENTITLEMENT", _entitlement_left_after_refund),
    "ENTITLEMENT_OVERCONSUMPTION": ("MEMBERSHIP_ENTITLEMENT", _entitlement_overconsumption),
}


@pytest.fixture
def box(sandbox_http_url: str, sandbox_token: str) -> Iterator[Sandbox]:
    client = Sandbox(sandbox_http_url, sandbox_token)
    yield client
    client.close()


def test_a_faithful_environment_exhibits_no_defect(box: Sandbox) -> None:
    """The control: with no axes declared, none of the seven observables moves."""
    deviated = [
        name
        for name, (scenario, probe) in PROBES.items()
        if probe(box, box.run(scenario, ()))
    ]
    assert deviated == [], f"an environment with no defect axes deviated: {deviated}"


def test_each_run_exhibits_exactly_the_axis_it_declares(box: Sandbox) -> None:
    observed: dict[str, list[str]] = {}
    for axis in AXES:
        observed[axis] = [
            name
            for name, (scenario, probe) in PROBES.items()
            if probe(box, box.run(scenario, (axis,)))
        ]
    expected = {axis: [axis] for axis in AXES}
    assert observed == expected, json.dumps(observed, indent=2)


def test_axes_combine_without_leaking_into_each_other(box: Sandbox) -> None:
    """A two-axis environment exhibits those two and nothing else."""
    pair = ("COUPON_RESTORED_ON_REFUND", "POINTS_GRANTED_AGAIN_ON_REFUND")
    observed = [
        name
        for name, (scenario, probe) in PROBES.items()
        if probe(box, box.run(scenario, pair))
    ]
    assert sorted(observed) == sorted(pair), observed


def test_an_unknown_axis_is_refused(box: Sandbox) -> None:
    """Naming an axis that does not exist must fail loudly, not silently do nothing."""
    response = box.create("PROMOTION", "fixed", ["NOT_A_REAL_AXIS"])
    assert response.status_code == 422, response.status_code


def test_the_suite_versions_still_carry_their_whole_axis_set(box: Sandbox) -> None:
    """Omitting axes keeps the old meaning: `vulnerable` is every axis, `fixed` none."""
    run = str(box.create("REFUND_POINTS", "vulnerable").json()["run_id"])
    assert _points_granted_again(box, run) is True
