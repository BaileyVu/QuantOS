from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

FEE_QUANTUM = Decimal("0.00001")
CRYPTO_DEFAULT_RATE = Decimal("0.07")


@dataclass(frozen=True)
class FeeMetadata:
    token_id: str
    fee_rate: Decimal
    source_url: str
    verified: bool
    asset: str = "USDC"


def fee_rate_from_payload(
    token_id: str,
    payload: Mapping[str, object],
    *,
    source_url: str,
) -> FeeMetadata:
    """Parse the public CLOB fee-rate response (basis points) fail-closed."""
    raw = payload.get("fee_rate_bps", payload.get("base_fee"))
    try:
        bps = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("missing or invalid fee_rate_bps") from exc
    if bps < 0:
        raise ValueError("fee_rate_bps must be non-negative")
    return FeeMetadata(token_id, bps / Decimal("10000"), source_url, True)


def verified_crypto_default(token_id: str, *, evidence_url: str) -> FeeMetadata:
    if not evidence_url.startswith("https://"):
        raise ValueError("verified crypto default requires an HTTPS evidence URL")
    return FeeMetadata(token_id, CRYPTO_DEFAULT_RATE, evidence_url, True)


def taker_fee(
    *, shares: Decimal | str,
    price: Decimal | str,
    metadata: FeeMetadata,
) -> Decimal:
    """Return the official taker fee in USDC, rounded to 5 decimals."""
    if not metadata.verified:
        raise ValueError("unverified fee metadata")
    quantity = Decimal(str(shares))
    fill_price = Decimal(str(price))
    if quantity < 0 or not Decimal("0") <= fill_price <= Decimal("1"):
        raise ValueError("invalid quantity or binary share price")
    raw = quantity * metadata.fee_rate * fill_price * (Decimal("1") - fill_price)
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)
