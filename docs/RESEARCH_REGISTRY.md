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
reporting, show `family_n` alongside the separate external count. There is no
Sharpe return or DSR probability until a run supplies valid return series and a
reviewed DSR implementation.

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
