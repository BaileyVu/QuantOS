# BUILD-only archive index

The archive index accepts optional `start_date` and `end_exclusive` UTC date
bounds. It filters manifest tasks by monthly/daily partition period before
opening or checksumming cached archives. Daily-repair tasks use the same filter,
while their overlay remains bound to the complete original manifest's
`base_content_hash`. The resulting `manifest_hash` includes the selected symbols
and non-default selection bounds. Calls without bounds retain their prior
partition selection and content hash behavior.

Bars are selected using completed-close availability, not open time. With
`--end-exclusive 2023-07-01`, an hourly candle opened at 2023-06-30 23:00 UTC has
`close_exclusive_ms == 2023-07-01T00:00:00Z` and is excluded because its close
boundary is not strictly before the cutoff. This prevents a boundary candle
from leaking into BUILD unless a later study explicitly defines and records a
different date convention.

The stored train manifest begins 2021-10-01. Requesting a 2020-01 start is
supported but does not create earlier partitions; it simply selects all
available rows from the manifest onward.

From the data root, use a new ignored audit path so existing audit evidence is
not overwritten:

```sh
cd /Users/bailey12/Desktop/PythonProject/QuantOS
PYTHONPATH=/Users/bailey12/Desktop/PythonProject/QuantOS-infra-sentibot/src \
  python -m quantos.paper.research.dataset \
  --manifest data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json \
  --overlay-manifest data/portfolio/manifests/daily_repairs_2021-10-01_to_2024-12-31.json \
  --audit /tmp/quantos-build-only-audit.json \
  --start-date 2020-01-01 --end-exclusive 2023-07-01
```

This command only builds and audits the selected archive index. It does not run
strategy evaluation or model selection. Keep later experiments on the returned
BUILD-only index and record its manifest hash before calculating outcomes.
