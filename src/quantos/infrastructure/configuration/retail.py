"""Explicit QOS-07 profile loading; no inferred migration of historical state."""
from decimal import Decimal
import tomllib
from quantos.domain.risk.retail import FullEquityRiskPolicy


def load_retail_profile(path):
    raw = tomllib.loads(path.read_text())
    if set(raw) != {"profile", "starting_equity", "maximum_exposure_fraction", "maximum_daily_loss_fraction", "maximum_drawdown_fraction", "adverse_gap_bps"}:
        raise ValueError("unexpected retail profile fields")
    if raw.pop("starting_equity") != "200":
        raise ValueError("retail capital must be 200")
    return FullEquityRiskPolicy(**{k: v if k == "profile" else Decimal(v) for k, v in raw.items()})
