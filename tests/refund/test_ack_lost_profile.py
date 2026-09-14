"""`vulnerable` still means "every business defect", and the transport axis is opt-in.

Adding `REFUND_ACK_LOST` to the reachable vocabulary must not change what an existing
`vulnerable` run does: under that profile every refund would cost the caller its whole
timeout, and the golden-v4 suites, the frozen demo, and the public Live Run would all
silently change behaviour and timing. A run that wants the lost acknowledgement names it,
which is what the refund suite does.
"""

from __future__ import annotations

from rulearena_commerce_sandbox.profiles import ALL_AXES, SandboxProfile
from rulearena_commerce_sandbox.schemas import SandboxVersion
from rulearena_domain_contracts import TRANSPORT_DEFECT_AXES, DefectAxis


def test_the_whole_suite_profile_carries_every_business_defect() -> None:
    profile = SandboxProfile.for_version(SandboxVersion.VULNERABLE)
    assert DefectAxis.COUPON_RESTORED_ON_REFUND in profile.axes
    assert DefectAxis.REFUND_AGAINST_ORIGINAL in profile.axes


def test_the_whole_suite_profile_leaves_the_transport_axis_out() -> None:
    assert ALL_AXES & TRANSPORT_DEFECT_AXES == frozenset()
    assert not SandboxProfile.for_version(SandboxVersion.VULNERABLE).loses_refund_acknowledgement


def test_a_run_that_names_the_transport_axis_gets_it() -> None:
    profile = SandboxProfile.for_run(
        SandboxVersion.FIXED.value, [DefectAxis.REFUND_ACK_LOST.value]
    )
    assert profile.loses_refund_acknowledgement
    assert not profile.allows_refund_against_original_amount


def test_a_faithful_run_has_no_defects_at_all() -> None:
    assert SandboxProfile.for_version(SandboxVersion.FIXED).axes == frozenset()
