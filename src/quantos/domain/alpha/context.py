"""Immutable account context supplied by Execution, never mutated by Alpha."""
from dataclasses import dataclass
from datetime import datetime
from quantos.domain.runtime_contracts import AccountSnapshot, validate_account
from quantos.domain.common import require_utc

@dataclass(frozen=True, slots=True)
class AlphaDecisionContext:
    timestamp: datetime
    account: AccountSnapshot
    version: str = "alpha-account-context-v1"

    def __post_init__(self):
        require_utc(self.timestamp, "alpha context time")
        validate_account(self.account)
        if self.version != "alpha-account-context-v1" or self.account.timestamp > self.timestamp:
            raise ValueError("invalid Alpha context version or future account")
