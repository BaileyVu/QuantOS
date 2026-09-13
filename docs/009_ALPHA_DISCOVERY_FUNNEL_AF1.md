# Phase 4C Alpha Discovery Funnel AF1

Status: research infrastructure only

AF1 standardizes the first general screen for hypotheses explicitly supplied by a
human researcher. It does not generate hypotheses, optimize parameters, select a
production strategy, or change the QuantOS promotion lifecycle. A provisional
`PROMOTE` classification means only "eligible for human review for deeper
research."

## Placement and trusted inputs

The screening logic is part of the existing Evaluation domain at
`quantos.domain.evaluation.alpha_funnel`. Filesystem publication is an adapter at
`quantos.infrastructure.storage.alpha_funnel`. These additions do not create a
seventh production module and are not connected to runtime trading.

Each input dataset is a `ValidatedCandleSequence` plus a stable dataset ID,
verified candle-content SHA-256, and one explicit research role:
`development`, `screening_validation`, or `sealed_oos`. AF1 hashes every
canonical candle field in sequence order and rejects a supplied content digest
that does not match the screened candles.

QuantOS V1 has no persistent authoritative sealed-split registry. AF1 therefore
requires a predeclared `FdrUniverse` from a named research authority. Every
universe key binds the dataset ID, verified content digest, complete dataset
identity digest, symbol, role, complete canonical hypothesis-definition digest,
hypothesis and family, evaluator implementation identity, direction, and
horizon. The definition digest covers all serialized hypothesis metadata,
including required inputs, causal lookback, parameters, parameter neighborhood,
interpretation, and human explainability metadata. Screening must match every
declared key exactly. Reusing a universe with a relabeled dataset, changed
evaluator, changed parameterization, missing test, or additional test fails
closed. Creating a different declaration produces
a different universe and batch ID; the research lead remains responsible for the
authority declaration.

Ordinary calls reject a correctly declared `sealed_oos` dataset. The caller must
set `allow_sealed_oos=True`, and that explicit action is recorded in the
immutable manifest.

## Hypothesis and callback contract

Each human-defined hypothesis supplies a stable ID, explicit evaluator
implementation identity/version, family, description, direction and
interpretation, canonical candle inputs, parameter metadata, causal lookback,
optional parameter-neighborhood metadata, and an optional human explainability
score. Canonical mapping-key ordering makes semantically identical metadata share
a definition digest. Changing the implementation identity or any declared
parameterization changes the FDR universe and run ID.

The callback receives exactly the declared number of completed candles ending at
decision time. It receives no forward-candle argument. AF1 reference hypotheses
are strictly causal. A human callback is trusted reviewed research code: Python
closures can access external state, so AF1 does not claim to sandbox callback
purity or causality. `required_inputs` is auditable metadata, not field-level
runtime isolation.

The included short-return extreme, realized-volatility ratio, and candle
close-location examples are transparent fixtures. They are not candidate
strategies and carry no profitability claim.

## Timeline, outcomes, and common support

Before evaluating any callback, AF1 revalidates the canonical sequence, verifies
its content digest again, and requires every adjacent pair to satisfy:

`earlier close_time < later open_time`

Each candle contract already requires `open_time < close_time`. Together these
rules guarantee, for decision index `i` and horizon `h`:

`decision candle i close_time < entry candle i+1 open_time < exit candle i+h close_time`

They also guarantee strict chronological ordering throughout the complete
forward path. Entry is the next candle open. The horizon outcome exits at that
horizon candle close.

AF1 fixes horizons at 1, 3, 5, 10, 15, 30, and 60 minutes. Every horizon uses
common decision support: a decision is eligible only when its full 60-minute
forward window exists. The manifest records this support rule. A 60-candle,
lookback-one sequence has zero eligible decisions; 61 candles have one.

## De-overlap and descriptive statistics

Signal detection first records every causal event as `raw_event_count`. For
each horizon, AF1 keeps the earliest event, then keeps a later event only when
its decision index is at least the horizon length after the last retained event.
Their forward holding intervals cannot overlap. The retained value is
`deoverlapped_event_count`. All returns, uncertainty, excursion, stability,
cost, and scoring statistics use these de-overlapped events. Raw counts and raw
frequency remain descriptive diagnostics.

De-overlap does not prove statistical independence or a formal effective sample
size. Output uses `event_deoverlap` and explicitly records
`does_not_assert_iid=True`.

AF1 reports raw and directional mean and median returns, win rate, average
positive and negative returns, payoff ratio, sample standard error, descriptive
two-sided one-sample Student t statistic and p-value, deterministic bootstrap
interval, gross and scenario-adjusted expectancy, average maximum favorable and
adverse excursion, frequency, temporal blocks, FDR metadata, evidence scores,
and classification inputs.

An exact zero directional return is not a win and is excluded from both the
positive and negative conditional means. Payoff ratio requires at least one
strictly positive and one strictly negative return. Undefined values, including
t statistics for fewer than two observations or zero sample variance, are JSON
`null`.

## Deterministic arithmetic and bootstrap

All AF1 arithmetic executes inside the fixed
`af1-decimal-50-half-even-tcdf-v1` Decimal context: precision 50,
round-half-even, and fixed exponent limits. Context settings and numerical
version are recorded in the manifest. Caller Decimal precision, rounding, and
traps do not affect run IDs, results, bytes, or hashes.

The bootstrap resamples the mean directional return of de-overlapped events with
replacement. Its per-test seed includes the configured seed, dataset ID, symbol,
verified content digest, hypothesis ID, evaluator implementation ID, complete
hypothesis-definition digest, and horizon. It uses fixed SplitMix64-v1. Sample count and confidence level are
explicit manifest fields.

The interval and Student t result are descriptive research diagnostics.
De-overlap does not establish IID observations or nominal coverage under
remaining serial or regime dependence. The custom t-CDF uses Python binary
floating-point math after deterministic Decimal estimation; representative
numerical oracles are tested, but cross-runtime bit identity still depends on
the pinned Python/runtime environment.

## Closed multiple-testing universe

Benjamini-Hochberg correction runs once across every registered test in the
predeclared `FdrUniverse`. The batch ID is exactly the SHA-256 of the canonical
universe declaration; it is not a caller label. A subset cannot claim the same
batch because screening rejects incomplete or mismatched declarations.

Registered tests whose p-value is undefined remain `null` in output but count
conservatively as `p=1` when ranks and multiplicity are calculated. Results
record the universe ID, authority ID, registered count, calculable count,
undefined-p policy, q-value, and one authoritative `fdr_threshold`.

The same `fdr_threshold` controls both `passes_fdr_threshold` and eligibility
for `PROMOTE`. A row that fails the reported threshold cannot be promoted.

## Costs, temporal robustness, and RVS

Cost rates use one canonical unit:
`simple_return_fraction_of_entry_notional`. For example, `0.001` is 10 basis
points round trip. Rates must be finite and in `[0, 1)`. Configuration requires
exactly one base scenario and at least one stress scenario; every stress rate
must be at least the base rate. Each rate is serialized with its unit and
subtracted exactly once from mean directional gross return.

Temporal stability divides eligible decisions into configured equal contiguous
blocks. A block qualifies for robustness only when it has at least the configured
minimum event count. AF1 records qualified-block coverage and requires the
configured minimum coverage before producing robustness evidence. When coverage
passes, the R input is positive qualified blocks divided by all expected blocks,
so empty or underpopulated blocks cannot disappear from the denominator. When
coverage fails, the robustness input is null and R scores zero. Independently of
configured score bands, an uncapped R of 4 is reduced to 3 unless at least the
configured absolute minimum of positive qualified blocks is present; that
minimum must be between two and the configured block count.

Configured bands map base-cost expectancy to M, positive t statistic to S,
de-overlapped event frequency to F, and coverage-aware robustness evidence to R.
Each scores 0 through 4. X is copied only from human metadata. Missing X leaves
RVS null and makes `PROMOTE` impossible. RVS is the integer sum
`M + S + F + R + X`, maximum 20; it is never a probability.

`KILL`, `WATCH`, and `PROMOTE` are provisional deterministic triage labels.
They do not bypass research, backtest, walk-forward, Monte Carlo, paper-trading,
or explicit-live-approval stages.

## Immutable artifacts

Run identity hashes canonical JSON containing verified dataset identities and
content digests, the closed FDR universe and role authority, hypothesis and
implementation metadata, costs and units, horizons, random seed, numerical
policy, support and timestamp rules, scoring and classification settings, and
sealed-OOS authorization.

`ScreeningRun` is created only by the public evaluator entrypoint. Arbitrary
payload construction has no evaluator provenance signer. Each run carries a
process-private proof bound to its run ID and exact canonical manifest/results
bytes. Its manifest and results are recursively immutable, and it retains
verified canonical bytes. Publication rechecks the run ID, manifest identity,
results SHA-256, canonical bytes, evaluator-issued proof, and consistency across
datasets, hypotheses, the FDR universe, evaluator IDs, and result rows.

The process-private proof authorizes only initial publication of an in-memory
`ScreeningRun`; it is intentionally neither persisted nor recreated.
`AlphaFunnelArtifactStore.load_verified(run_id)` instead returns an immutable
`VerifiedScreeningArtifact` after independently checking path containment,
canonical bytes, hashes, run identity, dataset and hypothesis identity bindings,
the FDR universe, evaluator consistency, result membership and ordering, and
Benjamini-Hochberg metadata. This durable read model grants no publication
authority and remains verifiable in a fresh Python process without evaluator
re-execution or the creating process's HMAC secret.

JSON uses sorted keys, compact separators, UTF-8, finite values, Decimal strings,
and one trailing newline. Results have deterministic ordering.

`AlphaFunnelArtifactStore` accepts a caller-supplied local root. Run IDs must be
canonical SHA-256 values. The resolved destination must remain strictly beneath
that root's `runs` directory, and symbolic-link escapes are rejected. Existing
manifest/results children must be regular files resolving directly inside the
canonical run directory; child symlinks and reparse escapes are rejected before
reading. Existing identical runs are reused; differing bytes, unexpected contents, corrupt run
objects, and path traversal fail closed. Concurrent identical publishers either
publish or verify and reuse the complete winner. Generated artifacts remain
outside Git.
