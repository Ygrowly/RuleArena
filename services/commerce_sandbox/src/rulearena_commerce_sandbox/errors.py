from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class AckLost(RuntimeError):
    """The write committed and its receipt is durable; only the acknowledgement vanished.

    Deliberately not a `DomainError`: it is not a business outcome the caller may map to
    an error code, and it must never be turned into a JSON error body. The service layer
    raises it *after* the transaction commits precisely so the transport failure cannot
    be mistaken for a rollback.
    """
