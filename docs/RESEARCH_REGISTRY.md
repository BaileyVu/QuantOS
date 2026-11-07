# Research registry

`quantos.paper.research.registry` provides a local SQLite ledger for distinct
experiment variants and their BUILD/HELD result observations. It does not run
experiments or calculate a Sharpe ratio.

```python
from quantos.paper.research.registry import ResearchRegistry

with ResearchRegistry("research/registry.sqlite") as registry:
    summary = registry.import_jsonl("/path/to/search_ledger.jsonl")
    print(summary)
    print(registry.trial_counts())
```

The JSONL importer is atomic: malformed rows reject the import without partial
writes. Exact repeated observations are skipped, changed observations remain
as separate rows, and repeated evaluations do not increase the trial count.
BUILD, DEVELOP, and HELD period labels are retained verbatim; BUILD and DEVELOP
map to the BUILD result class, and HELD labels to HELD. Legacy rows without a
`params` object use `family + variant_id` for identity and are marked
`legacy_variant_id`; they do not receive a fabricated parameter hash.

Trial counts are distinct `(family, params hash, data manifest hash)` identities.
The historical CCI study contributes 81 external trials per the Mac hunt
summary. It is reported separately as `legacy_cci_n`, not assigned to a
fabricated family or inserted as synthetic variant records. For overall
multiple-testing context, use `global_n_with_legacy`; for family-level
reporting, show `family_n` alongside the separate external count. The imported
ledger has no per-period Sharpe inputs, so the registry does not calculate DSR
from its means or CIs. `calculate_deflated_sharpe()` accepts either a return
series or explicit unannualized Sharpe/sample-size/skew/Pearson-kurtosis
moments, plus the trial Sharpe mean and standard deviation. It applies the
Bailey–López de Prado expected-maximum-Sharpe threshold and PSR non-normality
adjustment. For one trial and zero trial mean, it reduces to PSR against zero.
The single-trial case is handled directly instead of applying the extreme-value
approximation outside its useful `N > 1` range. See the [authors' paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf),
Equations 1–2.

Select N through the registry to keep family and all-search counts separate.
Supply trial-Sharpe dispersion estimates that match the chosen scope:

```python
family_dsr = registry.deflated_sharpe(
    scope="family", family="crash_flush_e2",
    returns=periodic_returns,
    trial_sharpe_mean=family_trial_mean,
    trial_sharpe_std=family_trial_std,
)
global_dsr = registry.deflated_sharpe(
    scope="global", sharpe_ratio=observed_sharpe,
    sample_size=n_periods, skewness=return_skew,
    kurtosis=pearson_kurtosis,
    trial_sharpe_mean=all_trial_mean,
    trial_sharpe_std=all_trial_std,
)
```

The Sharpe ratio and its trial distribution must use matching unannualized
periodic return units. Input moments, return dependence, and the chosen
effective trial count need research review; the count alone does not establish
independent trials.

The imported `edge_vault/search_ledger.jsonl` contains 4,309 lines, including
14 rows without `params`. Line count is not DSR N. The importer derives trial N
from distinct identities and keeps repeated period results separately. Older
hunt documents cite a different ledger snapshot; use the actual imported
registry counts for the current snapshot and retain the 81 historical CCI
count as a separate external component. This snapshot imports as 4,227 distinct
registry variants, giving a documented all-search count of 4,308 with the 81
external CCI trials. Older notes that use ledger-line count as N describe a
different counting method; report registry and external components separately.

Create and verify a dataset manifest without copying or modifying the dataset:

```python
from quantos.paper.research.registry import (
    create_dataset_manifest, verify_dataset_manifest,
)

create_dataset_manifest(
    "data/bars.csv", "research/manifests/bars.json",
    source="provider and collection method",
    start="2020-01-01", end="2023-06-30",
    missing_ranges=[{"start": "2021-03-04", "end": "2021-03-04"}],
)
manifest = verify_dataset_manifest("research/manifests/bars.json")
print(manifest["manifest_sha256"])
```

The manifest binds source, inclusive date range, declared gaps, dataset path,
and dataset SHA-256. Verification checks the file bytes and the manifest hash.
Existing manifests are not overwritten with different content. Keep generated
manifest/database outputs in local research storage; do not add datasets or
generated outputs to Git.

The training `run_cell` output carries `data_manifest_hash` from the verified
dataset index content hash. Pass that hash when registering locally generated
variants so the variant identity remains bound to its data snapshot.
