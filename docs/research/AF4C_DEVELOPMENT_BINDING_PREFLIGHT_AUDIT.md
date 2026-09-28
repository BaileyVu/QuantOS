# AF4C STEP 9B — blinded DEVELOPMENT binding preflight

Status: **CHANGES REQUIRED — implementation drafted; runtime validation blocked.**
This is implementation work with synthetic temporary-directory tests only. No real
dataset identity, observation, AF4B outcome, or AF4C result is recorded here.

## Scope and frozen lineage

Starting HEAD: `06f68b289b37584dc554924dee3ac131242b427c`.
Branch: `codex/af4c-de1-development-binding-preflight`.

The scientific manifest pins:

| Identity | Frozen value |
| --- | --- |
| A1 commit | `3722019f461ea28eb9e49c2de27e6cd259dfb609` |
| Preregistration commit | `8a48faa0365e990de1bc13511016ca6bef746d96` |
| Evaluator commit | `06f68b289b37584dc554924dee3ac131242b427c` |
| Catalog ID | `71b244b9843bc6c105f9b761f23437984e4ee87ee6c5156f300e72c8bb785350` |
| FDR family ID | `f0d87b0935d56a8c53f3939304c3c071cc4d6ec33d59ef9029a775322432bd42` |
| Evaluator ID | `af4c-impact-screen-tplus2-v1` |

Exactly five new files implement the application contract, infrastructure adapter,
two test modules, and this audit. Existing files, frozen specifications, frozen
AF4C evaluator/preregistration artifacts, and O1/O2/O3 implementations are unchanged.
No CLI, execution script, commit, push, merge, or PR is part of this change.

## Architecture and future invocation boundary

`quantos.application.af4c_development_binding` owns the pure binding contract.
It accepts paired validated Candle sequences, their physical-byte digests, exact
DE1 ranges, authoritative constituent archive manifests, and validated minute
datasets. Archive manifests are necessary because existing range references do
not carry research roles: the binder validates the actual source role and matches
each manifest back to its exact range reference. Dates never imply role.

The canonical Candle `dataset_id` implementation is supplied through a callable
port. The infrastructure adapter always supplies the existing Parquet store's
`dataset_id`; application code does not import infrastructure or redefine the
Candle content digest.

`quantos.infrastructure.storage.af4c_binding.preflight_development` accepts exactly
two ordered explicit Candle paths (BTC, ETH), a local source root, an explicitly
constructed `ParquetAggregateTradeMinutePrimitiveCache`, and optionally a
predeclared exact revision tuple for each symbol. A previous binding can optionally
be supplied to require byte-identical replay. It returns a binding and an
operational receipt, without publishing or evaluating them. The public API always
uses the frozen interval. No production short-range or test-mode flag exists.
Source roots must be scoped to permitted DEVELOPMENT publications: manifest
metadata is checked for role and date before O3 can decode certificate/event values.
Mixed-role or out-of-interval roots fail closed instead of silently being filtered.

Private `_TestOnlyContract`, `_build`, `_load`, `_preflight`, and store fixture
methods support miniature unit fixtures. Public binding loading, preflight, and
publication do not accept that contract. The frozen-contract unit test constructs
invented identity-only metadata with 639 references per symbol in memory; it does
not create full-interval observations or publish a purported real binding.

No real invocation was performed. Future invocation and real-data access require
separate authorization. The existing evaluator accepts synthetic inputs only;
no development evaluator was added or altered.

## Scientific schema and canonical serialization

Schema: `af4c-development-binding-v1`.

| Section | Allowed information |
| --- | --- |
| `schema_version`, `research_phase`, `data_role` | Fixed binding version, blinded phase, DEVELOPMENT |
| `development_interval` | Exact UTC endpoints, timeframe, day count |
| `scientific_lineage` | Six frozen identities above |
| `candle_bindings` | Ordered BTC/ETH logical identities and physical digests |
| `de1_range_bindings` | DEVELOPMENT role and canonical domain range manifest |
| `de1_minute_bindings` | Canonical domain minute identity, dataset/content IDs, structural counts |
| `alignment` | Four exact grids, common minute count, total source partition count |
| `verification` | Fixed source/role/lineage/grid verification facts |
| `binding_id` | Digest of every other field |

Serialization is built-in JSON types only, UTF-8 with ASCII escapes, sorted keys,
compact separators, `allow_nan=False`, and exactly one final LF. Floats are not
accepted. Timestamps use UTC `Z` with six fractional digits; dates use ISO dates;
digests use lowercase hexadecimal. Source and version labels must be simple
identifier tokens. Paths, hosts, PIDs, random IDs, and wall-clock values have no
schema location.

`binding_id = SHA256(canonical_bytes(document_without_binding_id))`, including
the final LF. The scientific artifact contains no cache-dependent diagnostics.
Loading rejects duplicate keys, alternate byte encodings, unexpected nested
fields, invalid types/digests, altered lineage, wrong grids/counts/symbols, and
inconsistent domain identities. The range loader's canonical reconstruction
rejects unknown fields in its nested references and boundary evidence.

Bindings retain only immutable bytes. Normal direct construction is rejected;
validated factories construct them. Public dictionaries are fresh copies.

## Operational receipt

Schema: `af4c-development-binding-receipt-v1`.
The separate receipt contains its schema, binding ID, catalog diagnostics, and two
ordered symbol diagnostic records. It has its own closed, strictly typed schema.

Catalog fields are `certificate_hit_partition_count`,
`certificate_miss_partition_count`, `certificate_built_partition_count`,
`canonical_events_replayed`, and `certificate_fast_path_used`.

Each symbol contains `o1_verified_range_available`, `cache_hit_partition_count`,
`cache_miss_partition_count`, `cache_built_partition_count`,
`cached_minute_rows_loaded`, `raw_events_consumed`, and `warm_cache_used`.
Timing, event buffers, per-minute activity counts, and source values are excluded.
Receipts are returned to the caller; scientific storage does not persist them.

## Identity hierarchy

For Candles, `dataset_id` is the existing digest over canonical identity metadata;
it is not a content checksum. `content_sha256` uses AF1's existing
`canonical_candle_content_sha256`. `identity_sha256` binds the exported logical
Candle fields (including dataset/content IDs and coverage), excluding itself and
the physical-byte hash. `parquet_byte_sha256` records physical evidence separately.
The canonical store validates checksums, Arrow and physical schemas, metadata,
chronology, and values. The adapter hashes bytes before and after reads and again
at the end, without trusting file size or modification time.

A valid alternate Parquet encoding can preserve all three logical identity
digests while changing the physical digest. Since the whole binding includes
physical evidence, that changes the overall binding ID. Replay against a supplied
previous binding rejects it. Cold/warm invariance assumes unchanged authoritative
source bytes, not an equivalent physical re-encoding.

For DE1, archive manifest and source revision IDs bind authoritative provenance;
raw ZIP and canonical-sequence digests remain distinct. The existing domain
`range_id` binds ordered partition lineage, coverage, revision policy, and boundary
integrity evidence. Canonical range references preserve source timestamp units.
Minute-state content uses the existing domain content hash; its dataset ID uses
the existing `aggregate_trade_minute_dataset_id(identity, content_sha256)`.
The full minute identity retains source references, current range ID, schema,
aggregation version, availability state, and interval semantics. Minute values
are never copied into the binding. No second scientific content algorithm exists.

## Frozen contract and alignment

Both BTCUSDT and ETHUSDT are mandatory, in canonical order, with no duplicates or
additional symbols. Full source coverage is
`[2024-01-01T00:00:00Z, 2025-10-01T00:00:00Z)`: 639 UTC days, 920160 Candle rows
and 920160 completed-minute rows per symbol, 639 DE1 partitions per symbol, and
1278 total partitions. There are exactly two Candle datasets, two ranges, and two
minute datasets. Candle timeframe is `1m`; the last open is
`2025-09-30T23:59:00Z`. The common signal boundary at 23:53 does not truncate data.

The pure binder revalidates source contracts, ordered full minute grids, exact
range/minute references, validation statuses, schema/aggregation compatibility,
and all frozen lineage. Domain validation rejects gaps, duplicates, reorder, and
partial intervals. Matching all four grids to the same frozen start/end/count
proves same-minute alignment without comparing values or evaluating H5.

## O1, O2, O3

O1 is used through existing range composition and `verified_range_report`.
Unavailable verified evidence invokes the existing exact stream fallback. The
receipt records availability; scientific bytes do not record acceleration choice.
UNIQUE selection rejects missing or ambiguous revisions. EXACT accepts only the
caller's complete ordered revision declarations; it does not choose by outcomes.

O2 uses `aggregate_trade_minute_states` with the caller's explicit cache instance.
The cache root is independent of the source root. Daily cache identity remains
range-independent, while the final minute identity always binds the current range.
Corrupt cache entries fail closed. A synthetic regression reuses one day while
extending the surrounding range and checks the resulting range/dataset identities.

O3 uses the certificate-aware catalog rebuild and existing diagnostics. Source
files remain authoritative. The new adapter checks existing certificates before
rebuild: corrupt or physically stale certificates stop this preflight instead of
letting O3 automatically repair them. Missing certificates remain a valid cold
path. O3 itself is unchanged. Its certificate store still writes derived evidence
under the supplied source root; independently relocating O3 is outside this task.

The cold/warm test requires byte-identical scientific documents and IDs, unchanged
minute IDs/content hashes, warm certificate hits, and zero warm raw-event reads.
The O1 fallback test requires the same scientific bytes. These assertions are
authored but have not executed because the mandated interpreter cannot start.

## Blindness and local storage

Trusted code internally reads canonical Candle and event bytes, certificate
boundary events, minute primitives, and materialized states solely for integrity,
lineage, hashing, and coverage. It never calls or imports AF4C evaluation/trigger
functions. No observation or result fields are allowed in the exported schema.
Range boundary IDs/timestamps and accepted source-row counts are integrity and
provenance evidence, not signal or performance measures.

Closed schemas prohibit price/quantity/OHLC/volume/flow/VWAP/imbalance/displacement,
signal counts, returns, statistics, rankings, and classifications. Errors at
public binding/storage boundaries are fixed messages with lower-level exception
chaining suppressed. The implementation has no logging/stdout path. Dynamic tests
patch the evaluator and sockets, capture stdout/stderr, and inject a secret
sentinel into a lower-level exception. They remain unexecuted.

Storage layout:

```text
<artifact-root>/research/alpha-funnel-af4c/binding/
  af4c-development-binding-v1/<binding-id>.json
```

Filenames derive only from validated digests. Local path checks reject traversal,
UNC paths, symlinks, and junctions; source/cache trees are checked before discovery.
Publication writes a same-directory temporary file, flushes and fsyncs it,
validates read-back, then creates an atomic exclusive hard link. Identical existing
bytes are idempotent; differing bytes collide without overwrite. Unsupported hard
links fail closed. Temporary files are cleaned up. POSIX also fsyncs the directory;
Windows uses file fsync and atomic hard-link publication without claiming portable
directory-fsync durability.

`to_evaluator_identity()` exports the binding ID, lineage, and exactly four input
identities: BTC/ETH Candle and BTC/ETH DE1 minute state. It carries no paths or
values and does not enable the current synthetic evaluator to run real inputs.

## Tests and validation actually performed

39 new test methods are authored: 17 application tests and 22 storage tests.
They cover canonical IDs/serialization, exact frozen and miniature contracts,
paired alignment, role rejection, Candle grids/identity/content/physical
mutations, range ambiguity/missing days/EXACT selection, O1 fallback, O2 independent
cache and cross-range reuse, O3 cold/warm/corrupt certificates, the 2024/2025
timestamp-unit transition, nested unknown fields, JSON duplicates, wrong frozen
lineage/counts/hashes, collision/atomicity, traversal/redirects, exception
redaction, no evaluator call, and no network imports/calls. The symlink test may
skip when the host does not permit creating test symlinks.

The required local virtual-environment executable was invoked for `--version`,
the two new unit modules, and a broader 16-module gate covering AF4C evaluator,
shadow and preregistration; AF4B preregistration; AF1/AF4A; O1 ranges/streaming;
O2 minute state/storage/cache; O3 certificates/catalog; archive storage; and Candle
Parquet storage. Every Python invocation failed before starting the interpreter:
the venv launcher could not create its configured base-interpreter process.
The local venv configuration declares 3.12.10, but the running version is
**unconfirmed**. No alternate Python executable or network installation was used.

New tests executed: **0**. Broader tests executed: **0**. No passing test totals or
import/compile verification are claimed. No lint/type-check configuration was
found in the inspected project configuration or configuration-file search.
Git whitespace checks pass, including separate no-index checks of all new files
because ordinary `git diff --check` does not include untracked files. Tracked
diffs remain empty. All five new files were inspected for forbidden machine paths.

## Limitations and next required action

- Restore the mandated Python 3.12.10 runtime and run both gates before audit PASS.
  Functional behavior and import compatibility remain unverified until then.
- Existing AF1 and minute content hashing materialize large canonical JSON values.
  This work reuses them as required; full-interval memory use is not benchmarked.
- The adapter examines the exact cache type's private `_root` solely for local
  path checks because the frozen implementation has no public root accessor.
- Local integrity evidence is not a digital signature. An adversary able to
  rewrite every source, manifest, and digest coherently is outside the existing
  storage trust model. Optional expected-binding replay detects divergence from
  a separately retained binding. Loading alone cannot reverify source bytes.
- Source byte checks and path checks do not lock files against concurrent external
  rewrites. Future runs require immutable, quiescent local publications and trusted
  caller-supplied roots. No process-level filesystem security boundary is claimed.
- Free-form source/version labels outside the conservative identifier grammar are
  rejected and would require audit, not silent normalization.
- No real source roots, observations, AF4B outputs/status, or prohibited process
  were accessed. No AF4C evaluation, market acquisition, or network action ran.
  This is neither data-readiness evidence nor validation/paper/live approval.

Verdict: **CHANGES REQUIRED** until the required interpreter and tests succeed.
