# QOS04 bounded experiment ledger

| ID | Mechanism | Fixed version | Evidence / disposition |
|---|---|---|---|
| Q04-LL-v1 | relative-return lead–lag | 5s, >=2bps, OBI .15, half-move uncertainty | Closed, not promoted. Synthetic causal mechanics pass. Final 900s: 7,932 evaluations, 1 raw signal rejected by flow/imbalance, 0 confirmations/trades; no independent positive after-cost evidence. |
| Q04-LD-v1 | sampled forced-flow continuation | >=2x best liquidity, 5s response, same confirmations | Closed, not promoted. Two genuine development liquidation events were below liquidity threshold. Final 900s: 7,932 evaluations, 0 observed liquidation events, 0 signals/trades; insufficient coverage and no independent positive after-cost evidence. |

Both baseline captures replay exactly under final code `276e570`; predefined
1.5x taker fee / 3bps-per-leg slippage / 300ms acknowledgment stress also completes.
No trade reached cost admission, so stress is a technical check, not demonstrated
economic robustness. Expectancy is undefined with zero closed trades. Neither
candidate meets ALPHA_PASS. Code is preserved as experimental infrastructure;
closure does not silently change fixed runtime behavior or promote deployment.

No optimized variants, protected holdouts, model fitting or historical research
campaigns. Synthetic profitable fills verify mechanics, not positive expectancy.
Chronological development diagnostics and the fixed public qualification were
not used to tune parameters. See QOS04_FINAL_REPORT.md for exact evidence,
rejection counts, source limitations and acceptance classifications.
