from decimal import Decimal
import json
import os

import pytest

from s199_v2.atomic import atomic_json
from s199_v2.fees import FeeMetadata
from s199_v2.ledger import Ledger


META=FeeMetadata("t",Decimal(".07"),"https://clob.polymarket.com/fee-rate",True)


def test_restart_restores_cash_open_risk_and_deduplicates(tmp_path) -> None:
    path=tmp_path/"ledger.sqlite3"
    ledger=Ledger(path,experiment="s199-v2-a",initial_cash="200")
    assert ledger.record_fill(fill_id="f1",event_id="e1",instrument_id="i",slug="s",outcome="Up",shares="2",price=".73",fee_metadata=META,ts_ns=1)
    assert not ledger.record_fill(fill_id="f1",event_id="e1",instrument_id="i",slug="s",outcome="Up",shares="2",price=".73",fee_metadata=META,ts_ns=1)
    before=ledger.portfolio(); ledger.close()
    restored=Ledger(path,experiment="s199-v2-a",initial_cash="200")
    assert restored.portfolio()==before
    assert before.cash==Decimal("198.51241") and before.open_cost==Decimal("1.48759")
    restored.close()


def test_settlement_is_transactional_and_realized(tmp_path) -> None:
    ledger=Ledger(tmp_path/"x.db",experiment="x")
    ledger.record_fill(fill_id="f",event_id="e",instrument_id="i",slug="s",outcome="Down",shares="2",price=".7",fee_metadata=META,ts_ns=1)
    assert ledger.record_settlement(settlement_id="z",fill_id="f",official_outcome="Down",ts_ns=2,evidence_url="https://example.test")
    assert not ledger.record_settlement(settlement_id="z",fill_id="f",official_outcome="Down",ts_ns=2,evidence_url="https://example.test")
    p=ledger.portfolio(); assert p.unresolved==0 and p.realized_pnl==Decimal("0.57060")
    ledger.close()


def test_new_name_required_to_reset_initial_cash(tmp_path) -> None:
    path=tmp_path/"x.db"; Ledger(path,experiment="a").close()
    with pytest.raises(ValueError,match="new experiment"):
        Ledger(path,experiment="b")


def test_open_risk_guard(tmp_path) -> None:
    ledger=Ledger(tmp_path/"x.db",experiment="x")
    ledger.record_fill(fill_id="f",event_id="e",instrument_id="i",slug="s",outcome="Up",shares="5",price="1",fee_metadata=META,ts_ns=1)
    assert ledger.can_enter(proposed_debit="1.01",max_open_risk="6") == (False,"aggregate_open_risk")
    ledger.close()


def test_atomic_checkpoint_replaces_complete_json(tmp_path) -> None:
    path=tmp_path/"checkpoint.json"; atomic_json(path,{"state":"stopping","pid":os.getpid()})
    assert json.loads(path.read_text())["state"]=="stopping"
    assert not list(tmp_path.glob("*.tmp"))
