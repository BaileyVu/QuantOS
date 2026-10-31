# Wallet evidence — incomplete economic attribution

Wallet: `0x56f609c609975454bf851f0d91079fbc4a5fe251`

Public cursor walks exhausted: **True**. Status: **INCOMPLETE_ATTRIBUTION**. Exact replica: false.
Profile join date: 2026-05-13T10:11:44+00:00. API activity starts in June, not the May join month.

| Dataset | Rows | Cursor exhausted |
| --- | ---: | --- |
| activity | 175821 | True |
| closed_positions | 19607 | True |
| open_positions | 11 | True |
| taker_trades | 10700 | True |
| tips | 0 | True |
| trades | 151835 | True |

## April–June 2026

- 2026-04: no wallet activity returned for this month; 0 rows; complete=True.
- 2026-05: no wallet activity returned for this month; 0 rows; complete=True.
- 2026-06: activity observed; 4823 rows; complete=True.

June activity types: `{"DEPOSIT":1,"MAKER_REBATE":4,"MERGE":2,"REDEEM":316,"SPLIT":322,"TRADE":4178}`; trade sides: `{"SELL":4178}`.

## Hypotheses ranked by observed support

1. **Maker liquidity provision: strongest support.** Documented v2 cohort classification gives {"CONFIRMED_MAKER":140911,"CONFIRMED_TAKER":10700,"UNKNOWN":224}. Classification uses complete maker-inclusive versus taker-only query cohorts, with duplicate signatures left UNKNOWN. It does not recover quotes, queue rank or intent.
2. **Split/sell/redemption inventory workflow: direct activity support.** Full-history activity types are {"DEPOSIT":6,"MAKER_REBATE":47,"MERGE":15,"REDEEM":14067,"SPLIT":9830,"TAKER_REBATE":16,"TRADE":151835,"WITHDRAWAL":5}; trade sides are {"SELL":151835}. The offline paired-bid engine is therefore a research strategy, not a replica of this observed workflow.
3. **Rebate contribution: credited API evidence, unresolved totals.** Profile PnL atoms and the activity sum disagree. No aggregate activity rebate is assumed to equal independently reconciled income.
4. **Spread versus directional/early-exit alpha: unresolved.** Gross realized sales, remaining split-token basis and aggregate redemptions cannot yet be cleanly decomposed. No conditional reversal study has been validated.

## PnL evidence and reconciliation gaps

The API profile currently reports these credited atoms (USD, timestamp included):
```json
{
  "deposits": null,
  "economic_pnl": "21464.06242",
  "fees": "-5935.75382",
  "fees_paid": "-5935.75382",
  "maker_rebate": "364.9126",
  "position_pnl": "21099.14982",
  "reward_income": "0.0",
  "taker_rebate": "0.0",
  "timestamp": 1792342061,
  "withdrawals": null
}
```
These public numbers are not audited profits and are not comparable to the small-capital synthetic runs. The profile snapshot, cursor-bounded activity and position pages are not atomic.

Partial reconstruction attribution: `{"directional_inventory":null,"early_exits":"-329222.2611790","fees":null,"matched_sets":"0.0","rebates":"4821.2866","rewards":null,"spread_capture":null}`.
Unexplained position-PnL difference: **$351088.2669790**. This large residual includes redemption cost/payout attribution and is not silently assigned to spread profit.
Activity-minus-profile incentive discrepancies: `{"maker_rebate":"4039.9589","taker_rebate":"416.4151"}`.
Unresolved-event reasons: `{"redemption token burn/cost requires transaction receipt":14067}`.

Polygon transaction hashes/explorer references are retained. The full receipt and token-burn history is not reconstructed; a bounded read-only sample is reported separately below. Inactive positions may be excluded even after API cursor exhaustion. Deposits/withdrawals are requested explicitly; the profile deposit/withdrawal atoms are null. A complete chain reconciliation and exact strategy reconstruction remain blocked.

## Reproduction

```bash
.venv/bin/python -m quantos_pm research --max-pages 50
.venv/bin/python -m quantos_pm research --max-pages 1000 --resume artifacts/research/0x56f609c609975454bf851f0d91079fbc4a5fe251-1792342872278223000
```

Raw pages, classified trades, positions, monthly data and the full report: `/Users/bailey12/Desktop/PythonProject/QuantOS-PM/artifacts/research/0x56f609c609975454bf851f0d91079fbc4a5fe251-1792342872278223000`. No wallet key or account permission was used.

## Supplemental Polygon receipt sample

Verified 2 of 12 purposively selected transactions through the fixed public Polygon RPC. 10 reads failed; observed provider failures were HTTP 403.
Verified receipts matched successful canonical blocks, API/block timestamps and wallet involvement. Transfer amounts remain raw units; no USD identity or full attribution is inferred.
Raw receipt/block evidence: `/Users/bailey12/Desktop/PythonProject/QuantOS-PM/artifacts/research/0x56f609c609975454bf851f0d91079fbc4a5fe251-1792342872278223000/chain-sample-1792363468634486000.json`. Full reconciliation remains false.
