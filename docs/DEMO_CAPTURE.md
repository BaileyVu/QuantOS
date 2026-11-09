# Demo capture

The release records passive evidence in `state/capture/YYYY-MM-DD.jsonl.gz`.
These files are separate from market retention and are never pruned by the
three-day market cleanup. Writes are thread/process locked. Capture failures
increment `status.json` → `capture.errors`; they never change exchange results
or turn an accepted order into an ambiguous order. A nonzero error count makes
that recording incomplete for acceptance.

Each line contains schema, kind, received_ms and payload. Kinds include:

- `runtime_input`: the exact schema-2 input used by the existing event replay.
- `input_bytes`: base64 and SHA256 of the sentiment line or ml1h JSON bytes
  actually read; a later polling copy is never substituted. ML cached reads
  keep using the earlier captured version until the reader reloads its file.
- `exchange_response`: allowed unsigned parameters and decoded responses,
  including failed responses. No headers, signatures or listen keys are stored.
- `user_stream`: received order/account events; `exchange_inputs` identifies
  the batch actually consumed by the mirror.
- `mirror_links`: paper-to-exchange order mapping, needed to join real fills.
- `model_fill`: original model price, quote and modeled absolute slippage,
  buffered until its journal transaction commits. Rollback observations are
  discarded. Later Binance accounting corrections cannot overwrite this record.
- `journal_snapshot`: start/stop SQLite backups and hashes under `snapshots/`.
- `capture_stop`: final recorder counters before normal shutdown.

Only the existing execution client makes requests. The recorder adds no API
calls or orders and cannot change risk decisions. A model fill without a matching
committed journal row is not exported. Journal snapshots preserve the initial
account state needed for a future window replay, including resumed demo runs.

The release includes capture only. After the owner launches the demo, a separate
report will compare exact decisions and each independently modeled fill against
its linked Binance fill over the first 20 real fills or three days. Existing
book-corrected journal prices must never substitute for the captured model price.
That comparison, 24-hour shadow reconciliation and 72-hour Sentibot evidence
remain acceptance work; none is claimed by these capture tests.
