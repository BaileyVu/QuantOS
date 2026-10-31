from decimal import Decimal

import pytest

from s199_v2.fees import FeeMetadata, fee_rate_from_payload, taker_fee
from s199_v2.sizing import AskLevel, plan_market_buy


META = FeeMetadata("token", Decimal("0.07"), "https://clob.polymarket.com/fee-rate", True)


@pytest.mark.parametrize(("price", "expected"), [("0.01", "0.06930"), ("0.50", "1.75000"), ("0.70", "1.47000"), ("0.99", "0.06930")])
def test_official_crypto_examples_100_shares(price: str, expected: str) -> None:
    assert taker_fee(shares="100", price=price, metadata=META) == Decimal(expected)


def test_fee_rate_metadata_is_basis_points() -> None:
    got = fee_rate_from_payload("x", {"fee_rate_bps": 700}, source_url="https://clob.polymarket.com/fee-rate")
    assert got.fee_rate == Decimal("0.07")


def test_unverified_fee_is_rejected() -> None:
    with pytest.raises(ValueError, match="unverified"):
        taker_fee(shares="2", price="0.5", metadata=FeeMetadata("x", Decimal(".07"), "x", False))


def test_base_target_is_two_shares_not_quote_amount() -> None:
    plan = plan_market_buy(target_shares="2", asks=[AskLevel(Decimal("0.73"), Decimal("75"))],
                           available_cash="200", minimum_shares="1", quantity_increment="0.01", fee_metadata=META)
    assert plan.executable_shares == Decimal("2")
    assert plan.quote_cost == Decimal("1.46")
    assert plan.fee == Decimal("0.02759")
    assert plan.cash_debit == Decimal("1.48759")


def test_partial_liquidity_is_explicit() -> None:
    plan = plan_market_buy(target_shares="2", asks=[AskLevel(Decimal("0.6"), Decimal("1.25"))],
                           available_cash="200", minimum_shares="1", quantity_increment="0.01", fee_metadata=META)
    assert plan.executable_shares == Decimal("1.25")
    assert not plan.complete and plan.reason == "partial_liquidity_or_cash"


def test_no_fabricated_fill_below_minimum() -> None:
    plan = plan_market_buy(target_shares="2", asks=[AskLevel(Decimal("0.6"), Decimal("0.9"))],
                           available_cash="200", minimum_shares="1", quantity_increment="0.01", fee_metadata=META)
    assert plan.executable_shares == 0 and plan.reason == "venue_minimum"
