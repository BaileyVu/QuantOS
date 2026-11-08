"""Local experiment registry and checksum-bound dataset manifests.

The registry stores distinct variants separately from repeated result rows.  Its
trial counts are counts of registered variant identities, not ledger lines or
period evaluations.  It records counts for Deflated Sharpe inputs; it does not
invent a Sharpe estimate when none is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from statistics import NormalDist
from typing import Any

REGISTRY_SCHEMA = 1
MANIFEST_SCHEMA = 1
LEGACY_CCI_TRIALS = 81  # HUNT_SUMMARY_2026-10-31.md; external to imported JSONL.


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_params(params: dict) -> str:
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    return sha256_bytes(canonical_json(params).encode("utf-8"))


def _period_class(period: str) -> str:
    value = period.strip().upper()
    if value.startswith(("BUILD", "DEVELOP")):
        return "BUILD"
    if value.startswith("HELD"):
        return "HELD"
    return "OTHER"


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _validate_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a 64-character SHA-256 hex digest")
    try:
        bytes.fromhex(value)
    except ValueError:
        raise ValueError(f"{field} must be a 64-character SHA-256 hex digest") from None
    return value.lower()


def create_dataset_manifest(data_path: str | Path, manifest_path: str | Path, *,
                            source: str, start: str, end: str,
                            missing_ranges: list[dict[str, str]] | None = None) -> dict:
    """Write a manifest for an existing dataset file and return its SHA-256.

    Range endpoints and missing-range endpoints are ISO dates, inclusive. The
    dataset digest is computed from bytes and is verified by
    :func:`verify_dataset_manifest` before use.
    """
    data = Path(data_path).resolve()
    target = Path(manifest_path).resolve()
    if data == target:
        raise ValueError("manifest path must differ from dataset path")
    if not data.is_file():
        raise FileNotFoundError(data)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    gaps = missing_ranges or []
    for gap in gaps:
        if not isinstance(gap, dict) or set(gap) != {"start", "end"}:
            raise ValueError("each missing range must contain start and end")
        if date.fromisoformat(gap["start"]) > date.fromisoformat(gap["end"]):
            raise ValueError("missing range start must be on or before end")
        if gap["start"] < start or gap["end"] > end:
            raise ValueError("missing range must fall inside the declared range")
    try:
        relative_data = data.relative_to(target.parent)
        data_ref = relative_data.as_posix()
    except ValueError:
        data_ref = str(data)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "source": source.strip(),
        "range": {"start": start, "end": end},
        "missing_ranges": sorted(gaps, key=lambda item: (item["start"], item["end"])),
        "dataset": {"path": data_ref, "sha256": sha256_file(data)},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise FileExistsError(f"refusing to replace existing manifest: {target}")
    else:
        target.write_text(content, encoding="utf-8")
    return {**manifest, "manifest_sha256": sha256_bytes(canonical_json(manifest).encode("utf-8"))}


def verify_dataset_manifest(manifest_path: str | Path, *, expected_hash: str | None = None) -> dict:
    """Verify required fields and current dataset bytes; return verified manifest."""
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("unsupported dataset manifest schema")
    if not isinstance(manifest.get("source"), str) or not manifest["source"].strip():
        raise ValueError("manifest source is required")
    window = manifest.get("range")
    if not isinstance(window, dict):
        raise ValueError("manifest range is required")
    lo, hi = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    if lo > hi:
        raise ValueError("manifest range is reversed")
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict) or not isinstance(dataset.get("path"), str):
        raise ValueError("manifest dataset path is required")
    expected = _validate_sha256(dataset.get("sha256"), "dataset sha256")
    data_path = (path.parent / dataset["path"]).resolve()
    if not data_path.is_file():
        raise FileNotFoundError(data_path)
    actual = sha256_file(data_path)
    if actual != expected:
        raise ValueError("dataset checksum mismatch")
    gaps = manifest.get("missing_ranges")
    if not isinstance(gaps, list):
        raise ValueError("missing_ranges must be a list")
    for gap in gaps:
        if not isinstance(gap, dict) or set(gap) != {"start", "end"}:
            raise ValueError("invalid missing range")
        gap_start, gap_end = date.fromisoformat(gap["start"]), date.fromisoformat(gap["end"])
        if gap_start > gap_end or gap_start < lo or gap_end > hi:
            raise ValueError("missing range is outside manifest range")
    digest = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    if expected_hash is not None and digest != _validate_sha256(expected_hash, "expected_hash"):
        raise ValueError("manifest checksum mismatch")
    return {**manifest, "manifest_sha256": digest}


@dataclass(frozen=True)
class ImportSummary:
    lines_seen: int
    variants_added: int
    results_added: int
    duplicates_skipped: int
    source_sha256: str


@dataclass(frozen=True)
class DSRResult:
    probability: float
    z_score: float
    observed_sharpe: float
    benchmark_sharpe: float
    sample_size: int
    n_trials: int


def _return_moments(returns) -> tuple[float, int, float, float]:
    values = tuple(_finite_number(x, "return") for x in returns)
    n = len(values)
    if n < 3:
        raise ValueError("at least three returns are required to estimate skew and kurtosis")
    mean = sum(values) / n
    centered = [x - mean for x in values]
    m2 = sum(x * x for x in centered) / n
    if m2 <= 0:
        raise ValueError("return series must have nonzero variance")
    # Sample standard deviation for Sharpe; population central moments for the
    # standardized skew and Pearson (non-excess) kurtosis estimates.
    sample_sd = math.sqrt(sum(x * x for x in centered) / (n - 1))
    skewness = sum(x ** 3 for x in centered) / n / (m2 ** 1.5)
    kurtosis = sum(x ** 4 for x in centered) / n / (m2 ** 2)
    return mean / sample_sd, n, skewness, kurtosis


def calculate_deflated_sharpe(*, n_trials: int, trial_sharpe_mean: float,
                              trial_sharpe_std: float, returns=None,
                              sharpe_ratio: float | None = None,
                              sample_size: int | None = None,
                              skewness: float | None = None,
                              kurtosis: float | None = None) -> DSRResult:
    """Compute DSR probability from explicit returns or explicit moments.

    Sharpe and trial-distribution inputs must use the same *unannualized* return
    period. ``kurtosis`` is Pearson kurtosis (normal distribution = 3).
    ``trial_sharpe_std`` is the sample standard deviation of trial Sharpe ratios.
    No moment or Sharpe input is inferred from the imported ledger's mean bp.
    """
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1:
        raise ValueError("n_trials must be a positive integer")
    trial_mean = _finite_number(trial_sharpe_mean, "trial_sharpe_mean")
    if trial_mean != 0:
        raise ValueError("DSR uses a zero-Sharpe null; trial_sharpe_mean must be zero")
    trial_std = _finite_number(trial_sharpe_std, "trial_sharpe_std")
    if trial_std < 0:
        raise ValueError("trial_sharpe_std must be nonnegative")
    if returns is not None:
        if any(value is not None for value in (sharpe_ratio, sample_size, skewness, kurtosis)):
            raise ValueError("provide returns or explicit moments, not both")
        try:
            sharpe_ratio, sample_size, skewness, kurtosis = _return_moments(returns)
        except TypeError:
            raise ValueError("returns must be an iterable of finite numbers") from None
    if sharpe_ratio is None or sample_size is None or skewness is None or kurtosis is None:
        raise ValueError("provide returns or all of sharpe_ratio, sample_size, skewness, and kurtosis")
    sr = _finite_number(sharpe_ratio, "sharpe_ratio")
    skew = _finite_number(skewness, "skewness")
    kurt = _finite_number(kurtosis, "kurtosis")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 2:
        raise ValueError("sample_size must be an integer of at least two")
    if kurt < 1:
        raise ValueError("Pearson kurtosis must be at least one")
    if kurt + 1e-12 < 1 + skew * skew:
        raise ValueError("skewness and kurtosis violate the Pearson moment bound")

    if n_trials == 1:
        benchmark = trial_mean
    else:
        gamma = 0.5772156649015329
        normal = NormalDist()
        expected_max_z = ((1 - gamma) * normal.inv_cdf(1 - 1 / n_trials)
                          + gamma * normal.inv_cdf(1 - 1 / (n_trials * math.e)))
        benchmark = trial_mean + trial_std * expected_max_z
    variance = 1 - skew * sr + ((kurt - 1) / 4) * (sr ** 2)
    if not math.isfinite(variance) or variance <= 0:
        raise ValueError("PSR variance adjustment must be positive")
    z_score = (sr - benchmark) * math.sqrt(sample_size - 1) / math.sqrt(variance)
    probability = NormalDist().cdf(z_score)
    return DSRResult(probability, z_score, sr, benchmark, sample_size, n_trials)


class ResearchRegistry:
    """SQLite registry. Database and source files remain caller-owned/local."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ResearchRegistry":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _initialize(self) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS registry_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS variants (
                variant_key TEXT PRIMARY KEY,
                family TEXT NOT NULL,
                params_hash TEXT,
                identity_method TEXT NOT NULL,
                params_json TEXT,
                manifest_hash TEXT,
                first_variant_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS variants_family ON variants(family);
            CREATE TABLE IF NOT EXISTS variant_aliases (
                variant_key TEXT NOT NULL REFERENCES variants(variant_key),
                variant_id TEXT NOT NULL,
                PRIMARY KEY(variant_key, variant_id)
            );
            CREATE TABLE IF NOT EXISTS results (
                result_id INTEGER PRIMARY KEY,
                variant_key TEXT NOT NULL REFERENCES variants(variant_key),
                period TEXT NOT NULL,
                period_class TEXT NOT NULL,
                stats_json TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_line INTEGER NOT NULL,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(variant_key, period, result_hash)
            );
            CREATE INDEX IF NOT EXISTS results_variant ON results(variant_key, period_class);
            CREATE TABLE IF NOT EXISTS external_trial_counts (
                label TEXT PRIMARY KEY, count INTEGER NOT NULL, source TEXT NOT NULL
            );
        """)
        self.db.execute("INSERT OR IGNORE INTO registry_meta(key,value) VALUES('schema_version',?)",
                        (str(REGISTRY_SCHEMA),))
        self.db.execute("INSERT OR IGNORE INTO external_trial_counts(label,count,source) VALUES(?,?,?)",
                        ("historical CCI study", LEGACY_CCI_TRIALS,
                         "edge_vault/HUNT_SUMMARY_2026-10-31.md; external to search_ledger.jsonl"))
        self.db.commit()

    @staticmethod
    def _row_identity(row: dict) -> tuple[str, str | None, str, str | None, str]:
        family = row.get("family")
        variant_id = row.get("variant_id")
        if not isinstance(family, str) or not family.strip():
            raise ValueError("family must be a nonempty string")
        if not isinstance(variant_id, str) or not variant_id.strip():
            raise ValueError("variant_id must be a nonempty string")
        params = row.get("params")
        if params is None:
            params_hash, params_json, method = None, None, "legacy_variant_id"
            identity = "legacy_variant_id:" + variant_id
        else:
            if not isinstance(params, dict):
                raise ValueError("params must be an object when present")
            params_json = canonical_json(params)
            params_hash, method = sha256_bytes(params_json.encode("utf-8")), "canonical_params"
            identity = params_hash
        manifest_hash = row.get("manifest_hash", row.get("data_manifest_hash"))
        if manifest_hash is not None:
            manifest_hash = _validate_sha256(manifest_hash, "manifest_hash")
        key_input = "\0".join((family.strip(), identity, manifest_hash or "unbound-manifest"))
        variant_key = sha256_bytes(key_input.encode("utf-8"))
        return variant_key, params_hash, method, params_json, manifest_hash

    @staticmethod
    def _stats(row: dict) -> tuple[str, str, str]:
        period = row.get("period")
        if not isinstance(period, str) or not period.strip():
            raise ValueError("period must be a nonempty string")
        trades = row.get("trades")
        if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
            raise ValueError("trades must be a nonnegative integer")
        stats: dict[str, Any] = {"trades": trades}
        if "mean_net_bp" in row:
            stats["mean_net_bp"] = (None if row["mean_net_bp"] is None else
                                     _finite_number(row["mean_net_bp"], "mean_net_bp"))
        if "ci90" in row:
            ci = row["ci90"]
            if ci is None:
                stats["ci90"] = None
            elif not isinstance(ci, list) or len(ci) != 2:
                raise ValueError("ci90 must contain two bounds")
            else:
                stats["ci90"] = [_finite_number(x, "ci90") for x in ci]
            if stats["ci90"] is not None and stats["ci90"][0] > stats["ci90"][1]:
                raise ValueError("ci90 bounds are reversed")
        verdict = row.get("verdict")
        if verdict is not None and not isinstance(verdict, str):
            raise ValueError("verdict must be a string")
        stats["verdict"] = verdict
        return period.strip(), _period_class(period), canonical_json(stats)

    def import_jsonl(self, source_path: str | Path) -> ImportSummary:
        """Import all valid rows atomically; repeated identical rows are ignored.

        Param-less historical rows are retained by ``family + variant_id`` and
        marked as such; they are never presented as parameter-hash identities.
        Changed result snapshots are stored as separate observations.
        """
        source = Path(source_path)
        raw = source.read_bytes()
        source_hash = sha256_bytes(raw)
        records = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("each JSONL line must be an object")
                identity = self._row_identity(row)
                period, period_class, stats_json = self._stats(row)
                result_hash = sha256_bytes(canonical_json({"period": period, "stats": json.loads(stats_json)}).encode())
                records.append((row, identity, period, period_class, stats_json, result_hash, line_number))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(f"invalid ledger row at line {line_number}: {exc}") from None
        added_variants = added_results = duplicates = 0
        with self.db:
            for row, identity, period, period_class, stats_json, result_hash, line_number in records:
                key, params_hash, identity_method, params_json, manifest_hash = identity
                cur = self.db.execute("""INSERT OR IGNORE INTO variants
                    (variant_key,family,params_hash,identity_method,params_json,manifest_hash,first_variant_id)
                    VALUES(?,?,?,?,?,?,?)""",
                    (key, row["family"].strip(), params_hash, identity_method, params_json,
                     manifest_hash, row["variant_id"].strip()))
                added_variants += cur.rowcount
                self.db.execute("INSERT OR IGNORE INTO variant_aliases VALUES(?,?)",
                                (key, row["variant_id"].strip()))
                cur = self.db.execute("""INSERT OR IGNORE INTO results
                    (variant_key,period,period_class,stats_json,result_hash,source_sha256,source_line)
                    VALUES(?,?,?,?,?,?,?)""",
                    (key, period, period_class, stats_json, result_hash, source_hash, line_number))
                if cur.rowcount:
                    added_results += 1
                else:
                    duplicates += 1
        return ImportSummary(len(records), added_variants, added_results, duplicates, source_hash)

    def register_variant(self, family: str, variant_id: str, params: dict, *,
                         manifest_hash: str | None) -> str:
        """Register a locally generated trial, bound to a verified data hash."""
        if not isinstance(family, str) or not family.strip():
            raise ValueError("family must be a nonempty string")
        if not isinstance(variant_id, str) or not variant_id.strip():
            raise ValueError("variant_id must be a nonempty string")
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if manifest_hash is None:
            raise ValueError("new variants require a data manifest hash")
        manifest_hash = _validate_sha256(manifest_hash, "manifest_hash")
        params_json = canonical_json(params)
        params_hash = sha256_bytes(params_json.encode("utf-8"))
        key = sha256_bytes("\0".join((family.strip(), params_hash, manifest_hash)).encode("utf-8"))
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO variants
                (variant_key,family,params_hash,identity_method,params_json,manifest_hash,first_variant_id)
                VALUES(?,?,?,'canonical_params',?,?,?)""",
                (key, family.strip(), params_hash, params_json, manifest_hash, variant_id.strip()))
            self.db.execute("INSERT OR IGNORE INTO variant_aliases VALUES(?,?)", (key, variant_id.strip()))
        return key

    def record_observation(self, variant_key: str, *, period: str,
                           stats: dict, source_path: str | Path) -> bool:
        """Record a manifested variant's non-trade research result idempotently.

        Forecast errors remain forecast errors; callers need not invent a trade
        count or Sharpe ratio to use the same experiment registry.
        """
        if not isinstance(period, str) or not period.strip():
            raise ValueError("period must be a nonempty string")
        if not isinstance(stats, dict) or not stats:
            raise ValueError("stats must be a nonempty object")
        if self.db.execute("SELECT 1 FROM variants WHERE variant_key=?", (variant_key,)).fetchone() is None:
            raise ValueError("observation requires a registered variant")
        stats_json = canonical_json(stats)
        result_hash = sha256_bytes(canonical_json({"period": period.strip(), "stats": stats}).encode())
        source_hash = sha256_file(source_path)
        with self.db:
            cur = self.db.execute("""INSERT OR IGNORE INTO results
                (variant_key,period,period_class,stats_json,result_hash,source_sha256,source_line)
                VALUES(?,?,?,?,?,?,?)""", (variant_key, period.strip(), _period_class(period),
                                         stats_json, result_hash, source_hash, 0))
        return cur.rowcount == 1

    def trial_counts(self, family: str | None = None) -> dict[str, int | str | None]:
        """Return family/global registry N and the separate historical CCI count.

        N counts distinct variant identities, not BUILD/HELD rows. The CCI 81
        is separately sourced and is included only in ``global_n_with_legacy``;
        it is not assigned to an invented family or inserted as 81 fake rows.
        """
        if family is None:
            registry_n = self.db.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
            family_n = None
        else:
            registry_n = self.db.execute("SELECT COUNT(*) FROM variants WHERE family=?", (family,)).fetchone()[0]
            family_n = registry_n
        legacy = self.db.execute("SELECT COALESCE(SUM(count),0) FROM external_trial_counts").fetchone()[0]
        return {"family": family, "family_n": family_n,
                "registry_n": registry_n if family is None else None,
                "legacy_cci_n": legacy,
                "global_n_with_legacy": registry_n + legacy if family is None else None}

    def deflated_sharpe(self, *, scope: str, family: str | None = None,
                        trial_sharpe_mean: float, trial_sharpe_std: float,
                        returns=None, sharpe_ratio: float | None = None,
                        sample_size: int | None = None,
                        skewness: float | None = None,
                        kurtosis: float | None = None) -> DSRResult:
        """Calculate DSR using this registry's family or global trial count.

        Global N includes the separately documented 81 historical CCI trials;
        those trials are not assigned to an invented family count. Supply a
        dispersion estimate appropriate to the chosen scope.
        """
        if scope == "family":
            if not family:
                raise ValueError("family is required for family-scope DSR")
            n_trials = self.trial_counts(family)["family_n"]
        elif scope == "global":
            if family is not None:
                raise ValueError("family must be omitted for global-scope DSR")
            n_trials = self.trial_counts()["global_n_with_legacy"]
        else:
            raise ValueError("scope must be 'family' or 'global'")
        assert isinstance(n_trials, int)
        return calculate_deflated_sharpe(n_trials=n_trials,
            trial_sharpe_mean=trial_sharpe_mean, trial_sharpe_std=trial_sharpe_std,
            returns=returns, sharpe_ratio=sharpe_ratio, sample_size=sample_size,
            skewness=skewness, kurtosis=kurtosis)

    def result_rows(self, *, family: str | None = None, period_class: str | None = None) -> list[dict]:
        query = "SELECT v.family,v.params_hash,v.identity_method,v.manifest_hash,r.period,r.period_class,r.stats_json,r.result_hash FROM results r JOIN variants v USING(variant_key)"
        clauses, args = [], []
        if family is not None:
            clauses.append("v.family=?"); args.append(family)
        if period_class is not None:
            if period_class not in {"BUILD", "HELD", "OTHER"}:
                raise ValueError("period_class must be BUILD, HELD, or OTHER")
            clauses.append("r.period_class=?"); args.append(period_class)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY v.family,r.period,v.params_hash"
        return [{**dict(row), "stats": json.loads(row["stats_json"])}
                for row in self.db.execute(query, args).fetchall()]
