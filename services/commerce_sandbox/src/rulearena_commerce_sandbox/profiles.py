from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rulearena_domain_contracts import TRANSPORT_DEFECT_AXES

from .schemas import DefectAxis, SandboxVersion

# The whole-suite `vulnerable` profile carries every *business* defect. A transport
# defect is left out on purpose: it changes no business fact, only whether the caller
# learns the answer, and every refund under it costs the caller its full timeout. A run
# that is measuring it names it, which is how the refund suite asks for it -- and that
# keeps `vulnerable` meaning exactly what it meant before this axis existed.
ALL_AXES: frozenset[DefectAxis] = frozenset(DefectAxis) - TRANSPORT_DEFECT_AXES


@dataclass(frozen=True)
class SandboxProfile:
    """Internal fault profile; never serialize this object into API-visible data."""

    axes: frozenset[DefectAxis] = frozenset()

    @classmethod
    def for_version(cls, version: SandboxVersion) -> SandboxProfile:
        """The whole-suite profiles: `vulnerable` carries every axis, `fixed` none."""
        return cls(ALL_AXES if version is SandboxVersion.VULNERABLE else frozenset())

    @classmethod
    def for_run(cls, version: str, stored_axes: Sequence[str] | None) -> SandboxProfile:
        """A run's profile: its declared axes, or the version's whole set when absent.

        Runs created before axes existed carry none, and keep behaving exactly as they did.
        """
        if stored_axes is None:
            return cls.for_version(SandboxVersion(version))
        return cls(frozenset(DefectAxis(item) for item in stored_axes))

    @property
    def version(self) -> SandboxVersion:
        return SandboxVersion.VULNERABLE if self.axes else SandboxVersion.FIXED

    @property
    def restores_coupon_after_full_refund(self) -> bool:
        return DefectAxis.COUPON_RESTORED_ON_REFUND in self.axes

    @property
    def allows_refund_against_original_amount(self) -> bool:
        return DefectAxis.REFUND_AGAINST_ORIGINAL in self.axes

    @property
    def loses_refund_acknowledgement(self) -> bool:
        return DefectAxis.REFUND_ACK_LOST in self.axes

    @property
    def grants_points_again_on_refund(self) -> bool:
        return DefectAxis.POINTS_GRANTED_AGAIN_ON_REFUND in self.axes

    @property
    def allows_points_overredemption(self) -> bool:
        return DefectAxis.POINTS_OVERREDEMPTION in self.axes

    @property
    def allows_full_membership_refund_after_consumption(self) -> bool:
        return DefectAxis.FULL_REFUND_AFTER_CONSUMPTION in self.axes

    @property
    def leaves_entitlement_after_membership_refund(self) -> bool:
        return DefectAxis.ENTITLEMENT_LEFT_AFTER_REFUND in self.axes

    @property
    def allows_entitlement_overconsumption(self) -> bool:
        return DefectAxis.ENTITLEMENT_OVERCONSUMPTION in self.axes
