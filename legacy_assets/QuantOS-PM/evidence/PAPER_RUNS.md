# Actual synthetic paper runs

Virtual capital only. Synthetic mechanics cases; no demonstrated edge.

| Capital | Model | Ending equity | Net P&L | Maximum drawdown | Complete pairs | Run ID |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| $200 | optimistic | $196.48039201 | $-3.51960799 | 2.000000% | 24.182861 | `3bacb22a289ec3ad1688` |
| $200 | baseline | $196.48039201 | $-3.51960799 | 2.000000% | 24.182861 | `9ee3184e4cb460897141` |
| $200 | pessimistic | $196.48039201 | $-3.51960799 | 2.000000% | 24.182861 | `5fef92fa92320fa15374` |
| $350 | optimistic | $343.84068592 | $-6.15931408 | 2.000000% | 42.320008 | `758580030698af26cca4` |
| $350 | baseline | $343.84068592 | $-6.15931408 | 2.000000% | 42.320008 | `0d3a04f93abdd27658ed` |
| $350 | pessimistic | $343.31022860 | $-6.68977140 | 2.000000% | 40.297142 | `150d189c891df6eee790` |
| $500 | optimistic | $491.40000000 | $-8.60000000 | 1.958433% | 60.000000 | `742d23fd95720be5c58c` |
| $500 | baseline | $491.40000000 | $-8.60000000 | 1.958433% | 60.000000 | `dde567e3777d733dc8c6` |
| $500 | pessimistic | $490.75000000 | $-9.25000000 | 1.958433% | 52.000000 | `43684903b25ad4ac9e49` |

## Reproduction commands

```bash
.venv/bin/python -m quantos_pm paper --config configs/paper_200.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm paper --config configs/paper_350.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm paper --config configs/paper_500.toml --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm compare --feed fixtures/btc_5m.jsonl
.venv/bin/python -m quantos_pm report --run-id 5fef92fa92320fa15374
.venv/bin/python -m quantos_pm report --run-id 150d189c891df6eee790
.venv/bin/python -m quantos_pm report --run-id 43684903b25ad4ac9e49
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q quantos_pm tests
.venv/bin/python -m pip check
```

Run manifests contain exact code/config/feed/calibration hashes. Journals and full reports are in `artifacts/runs/RUN_ID/`. Synthetic calibration is disallowed in live shadow. Pessimistic residual inventory explains the larger losses at $350/$500.
