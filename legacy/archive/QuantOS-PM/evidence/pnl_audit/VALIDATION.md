# Validation record

- Final command: `.venv/bin/python -m unittest discover -s tests -v`
- Result: **86 tests, PASS**, no skips, expected failures or suppressed failures;
  full output in `tests.txt`.
- Earlier full-suite run before adding auditor corruption checks: **82 tests,
  PASS**, retained in `tests_initial_82.txt`.
- An initial agent invocation used system Python 3.9 and failed during import
  because `tomllib` is unavailable there. The documented project virtual
  environment (Python 3.13) resolves that environment error. No failing assertion
  was removed to obtain a pass.
- Final audit command: `.venv/bin/python -m quantos_pm.pnl_audit --experiments`
- Result: **nine historical runs, 576 historical event checkpoints and 72 fixed
  diagnostic runs verified**; every monetary reconciliation residual is exactly
  zero. All nine diagnostic controls reproduce every historical evaluation field.
- Preservation: 79 original files match `original_input_hashes.json`.
  Journals are inspected using immutable, read-only SQLite only after verifying
  that WAL files are absent or empty. Original core code/configuration and
  historical evidence are not rewritten.
- Newly added tests: `tests/test_forensic_accounting.py` (15),
  `tests/test_research_experiments.py` (7), and `tests/test_pnl_audit.py` (4).
  The original 60 tests remain unchanged.
- Candidate comparisons are inspected synthetic-fixture diagnostics. Held-out
  validation is unavailable, as explicitly accepted by the user. No empirical
  profitability, guaranteed liquidation or five-second holding limit is claimed.
- No original QuantOS/S199 file, credential, live trading, deployment or external
  communication was involved.

The generated `verification.json` records the audit and experiment source hashes
and verification counts. `audit_run.txt` retains the final audit command output.
