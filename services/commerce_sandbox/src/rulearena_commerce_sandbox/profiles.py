from __future__ import annotations

from dataclasses import dataclass

from .schemas import SandboxVersion


@dataclass(frozen=True)
class SandboxProfile:
    """Internal fault profile; never serialize this object into API-visible data."""

    version: SandboxVersion

    @property
    def is_vulnerable(self) -> bool:
        return self.version is SandboxVersion.VULNERABLE

    @property
    def restores_coupon_after_full_refund(self) -> bool:
        return self.is_vulnerable

    @property
    def allows_refund_against_original_amount(self) -> bool:
        return self.is_vulnerable

    @property
    def grants_points_again_on_refund(self) -> bool:
        return self.is_vulnerable

    @property
    def allows_full_membership_refund_after_consumption(self) -> bool:
        return self.is_vulnerable

    @property
    def leaves_entitlement_after_membership_refund(self) -> bool:
        return self.is_vulnerable

    @property
    def allows_entitlement_overconsumption(self) -> bool:
        return self.is_vulnerable
