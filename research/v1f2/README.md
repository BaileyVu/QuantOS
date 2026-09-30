# Persistent perpetual carry research

Run only under the explicit V1-F2 authorization and the sealed
`docs/V1_F2_PERSISTENT_CARRY_PREREGISTRATION.md`. The production engine and
V1-F1 research source are unchanged. This is continuous-notional economic
simulation, not Futures execution or historical exchange-order replay.

`carry.py` implements the causal score, persistent membership, prospective
switching-cost check, beta hedge and delta-fill/funding ledger. `evaluate.py`
reuses hash-verified V1-F1 data without downloading anything. Its coverage-only
mode computes no returns. Economic mode requires an external pre-outcome seal
and refuses a second run after the consumption marker exists.

Example commands from the repository root (with project dependencies installed):

```powershell
python -m unittest tests.unit.test_v1f2_carry -v
python -m research.v1f2.evaluate --root G:\QuantOS-Data\v1f1\v1f2-persistent-carry --data G:\QuantOS-Data\v1f1\economic-v1 --coverage-only
```

The economic invocation omits `--coverage-only` and must occur only once after
source/configuration/preregistration sealing. Synthetic test fixtures and
coverage diagnostics are not economic evidence. The reused 2021–2024 period is
adaptive development; untouched later holdouts are still required for promotion.
No Mainnet order or credential capability exists in this research package.
