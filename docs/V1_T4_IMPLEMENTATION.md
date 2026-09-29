# V1-T4 implementation and operator status

The specification-only exception is committed separately as c317fef. The
implementation adds `tsmom-daily-v1` and metadata identifier
`deterministic-tsmom-no-ml-v1`. There is no trained model and no selected winner
until the preregistered evidence gates pass.

## Contracts and causality

The Feature Engine keeps at most 31 daily closes, the last canonical minute,
coverage count and a hash chain. It starts counting a daily close only after a
whole UTC day from midnight. The first partial day is excluded; later gaps,
conflicts, backward input and incomplete candles fail closed. Exact replay of
the last canonical minute is idempotent. Readiness requires H+1 completed days.

Source candle timestamps are immutable. Actual archive evidence changes close
precision at 2025-01-01: 59.999000 seconds becomes 59.999999 seconds. The new TSMOM
schema explicitly timestamps its decision/feature at `open_time + 1 minute`.
Both provider timestamps lie before that boundary. Historical and paper TSMOM
paths use this identical clock, while preserving original candles in provenance,
Risk inputs and checkpoints. `candidate-v1` retains its existing completion-grid
validation and feature formulas. This precision correction changes no strategy,
cost, calibration estimator or selection threshold.

`AlphaDecisionContext` is an immutable, versioned view of the Execution-owned
account. TSMOM receives `(FeatureVector, AlphaDecisionContext)` and returns the
existing AlphaEvaluation through application composition. Existing one-argument
candidate callbacks remain supported. Alpha never updates account state.
Any BTC balance, including dust, prevents another intentional entry. ETH holdings
block BTC entry. Risk still owns sizing, loss stops and final rejection.

V1-T2 `BacktestConfig.feature_version` and `PaperRuntimePolicy.feature_version`
default to candidate-v1; TSMOM requires explicit tsmom-daily-v1 selection. There is
no second backtester, Risk implementation or accounting implementation.

Paper checkpoint schema is now paper-runtime-v2. Older checkpoints fail closed;
there is no automatic migration or deletion of previous runtime evidence. The
new checkpoint binds daily-feature identity to minute evidence. Bootstrap takes
validated canonical sequences, builds features with no economic actions, and
binds the source/state identity into runtime identity. Restart requires the same
bootstrap identity. The first new live minute must immediately follow bootstrap
or the last committed minute; exact last-minute replay produces no new decision.
Pass bootstrap to PaperRuntime explicitly; the existing smoke CLI is not a TSMOM
launch command and must not be presented as production validation.

## Calibration screen

See V1_T4_PREREGISTRATION.md and configs/tsmom.preregistered.toml for the immutable
protocol. Labels are complete entry/exit price episodes strictly from permitted
training input; unfinished terminal positions contribute no invented outcome.
The conservative gross-return estimate reserves stressed exit costs before the
remaining entry edge is passed to Risk. Risk separately deducts entry costs.
This is an estimator with finite-sample and serial-dependence limitations, not
proof of profitability. Failed sample/edge gates prevent strategy promotion.

Run from this worktree with its src on PYTHONPATH and the project environment:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
python -m quantos.interfaces.tsmom_screen --data-root <canonical-root> --dataset <canonical-parquet> --output <new-evidence-directory>
```

The screen publishes protocol, source-file content identity, exact source-code
snapshot, every fold calibration, final pre-holdout calibration, and its verdict
outside Git. Exit 2 means NO_GO_FOR_LIVE; exit 0 means only eligibility for further
development evaluation. Neither exit permits paper/Testnet/Mainnet promotion.
Collision with different evidence fails closed. It never evaluates holdout PnL
or submits any exchange order.

Canonical continuation command (existing adapter and publisher):

```powershell
python -m quantos.interfaces.tsmom_data --base <authorized-base-parquet> --root <canonical-root> --end-exclusive 2026-09-28T00:00:00+00:00 --ingestion-version v1-t4-daily-continuation-20260929-v1
```

Raw ZIPs and CHECKSUMs are kept under the dedicated T4 ingestion directory. The
existing archive adapter validates their checksums, normalizes provider rows,
and the existing extension/publisher validates the entire continuous successor,
reads it back, and publishes without overwriting the original dataset.

No authenticated adapter, Mainnet launch, Testnet roundtrip, kill/flatten command,
or live approval is supplied by these T4A/entry-screen changes. Do not enter API
credentials for these commands; they require none.

## V1-T4R preservation audit

All 91 archived Python source files match the preservation implementation byte-for-byte except domain/features/daily.py: newline normalization and redundant-parenthesis removal only; Python ASTs match exactly. The acquisition CLI and offline test were completed after the snapshot. They do not change calibration math. The immutable source bundle, protocol, calibrations and NO_GO_FOR_LIVE result remain unchanged. Five documentation pins now match amendment c317fef, as explicitly authorized. Historical AF4B catalogs and assertions remain intact. This commit preserves a failed research candidate and is not promotion.
