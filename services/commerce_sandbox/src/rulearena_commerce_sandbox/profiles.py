from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .schemas import DefectAxis, SandboxVersion

ALL_AXES: frozenset[DefectAxis] = frozenset(DefectAxis)


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
