# AF4A — AF3 WATCH Forensics

Research-only closeout of exactly the 17 WATCH rows in the immutable AF3 development result.
AF3 remains 599 KILL / 17 WATCH / 0 PROMOTE across 616 registered tests.
No classification, threshold, hypothesis, cost assumption or production behavior changes.

## Results

All 17 fail the mandatory event-count, BH FDR and total-RVS gates.
13 also fail the base-net expectancy gate. The four passing cost have only three
de-overlapped events each, on ETHUSDT at 10/15/30/60 minutes.

The frozen mandatory rules are:

- base-net expectancy >= 0.002503128284573645913980997751940;
- de-overlapped event count >= 100;
- BH q <= 0.05 in the original closed 616-test universe;
- RVS >= 13.

Return units remain simple-return fractions of entry notional.
The base cost is 0.002503128284573645913980997751940; stress cost is
0.005006256569147291827961995503880. Thus positive base-net expectancy alone
does not meet AF3's promotion requirement.

13 cases have S below its frozen maximum-score band; all 17 have F below maximum
and R=0 from insufficient temporal coverage. These are secondary component
weaknesses, not additional mandatory gates. The secondary COST count is 15:
it includes M below its maximum band as well as nonpositive stress expectancy.
13 have negative stress expectancy; none has nonpositive base-net expectancy.
Six nominal p-values <= 0.05 fail BH. Raw p tests gross mean against zero.

There are 7 BTC and 10 ETH cases; 13 price_activity_disagreement and
4 reversal_exhaustion cases. Horizon counts for 1/3/5/10/15/30/60 minutes are
0/1/3/2/4/3/4. These are descriptive concentrations, not independent replications
or a pooled candidate. Raw event counts are descriptive; de-overlap does not imply IID.

The result supports a joint sample/coverage/multiplicity deficit, with an
additional magnitude deficit in 13 cases. Sparse temporal coverage cannot establish
regime instability. MF1 is separate evidence; no statistics are combined.
The existing DE1 feasibility priority remains justified but unvalidated.
Mechanism categories only: aggressor direction, persistence, price/flow divergence,
impact and absorption. AF4A does not establish that any mechanism will solve these
deficits, and MF1's coarse size proxy did not test trade-size distributions.
No DE1 implementation or AF4B hypothesis is authorized by this closeout.

## Reproduction and artifacts

From the repository root, using its verified virtual environment:

~~~powershell
& .\.venv\Scripts\python.exe -B -m research.alpha_funnel_af4a --source-root <existing-alpha-funnel-store> --output-root <new-af4a-store>
~~~

The only scientific source reads are the pinned AF3 manifest/results and repository
AF3/MF1 references. No candles, screening-validation, sealed OOS, 2026 observations,
external services, evaluator execution, or model fitting are needed.

The repository holds the implementation, tests, this note and the portable
run.json reference. The external artifact store holds runs/<run_id>/:

- analysis.json: exact source fields for all 17 rows, parameter/evaluator identities,
  mandatory predicates, secondary checks, single-gate counterfactual bookkeeping,
  exact cost gaps, distributions and interpretation;
- report.md: readable interpretation and complete exact per-row inventory;
- analysis.py: the byte-identical analysis implementation snapshot;
- manifest.json: content hashes for those three files.

Run ID is SHA-256 of canonical analysis.json (sorted compact UTF-8 JSON plus newline).
The analysis binds source run/results/manifest/catalog/FDR identities, WATCH-set and
row hashes, frozen configuration, repository HEAD and analysis/verifier code hashes.
Existing artifacts must match exactly on rerun; mismatches fail rather than overwrite.
A staged directory is renamed into place only after all files are written.

Exact replay requires the recorded repository HEAD and recorded analysis code bytes;
a changed code identity intentionally yields a different run ID. Copying the archived
analysis.py into an isolated checkout's research/alpha_funnel_af4a.py recovers that
uncommitted analysis version. Do not replace source artifacts.

## Validation and limits

The focused tests use synthetic diagnostic fixtures; they do not pretend those
fixtures are AF3. Actual AF3 acceptance separately used durable source verification,
two byte-identical analyses under different Decimal contexts, a read allowlist and
blocked socket connections. Publication and repeat publication were byte-identical.

Every original tracked file and both original AF3 artifact files were compared with
the pre-edit SHA-256 baseline. Full test results and artifact hashes are in run.json.
No configured lint/type-check command exists in the project configuration.

AF4A passes as a forensic task only. No research candidate gains promotion or
production approval. No commit, push, merge or next-phase implementation is included.
