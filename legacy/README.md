# 🗄️ legacy/: everything before QuantOS V4

Frozen history. Nothing here is imported by the running V4 system; it's kept so every past decision stays auditable.

| Era | What it was | Verdict | Where |
|---|---|---|---|
| **V1 spec** (Oct 2026) | Spec-first Spot BTC/ETH engine: 6-module architecture, 1-minute candles, LightGBM model | Superseded by the futures paper engine | [`docs/000–008`](docs/), [`v1_v3/`](v1_v3/) |
| **Alpha-discovery funnel** (AF1–AF4B, DE1) | Pre-registered alpha funnel and data-edge research program | No edge promoted | [`docs/009–011`](docs/), [`docs/research_phase4/`](docs/research_phase4/), [`v1_v3/research/`](v1_v3/research/) |
| **V1 paper runtime** (T1–T3) | Paper core, evaluation core, paper runtime | Replaced by `quantos.paper` | [`docs/V1_T*`](docs/), [`v1_v3/src/`](v1_v3/src/) |
| **V3 paper** | Public-data paper contract, market sources, acceptance | Evolved into V4 | [`docs/V3_*`](docs/) |
| **QuantOS-PM** | BTC 5-minute binary-market paper engine (Polymarket research) | Archived, paper only | [`archive/QuantOS-PM/`](archive/QuantOS-PM/) |
| **S199 PaperKit** | NautilusTrader sandbox kit for Polymarket BTC markets | Archived, never run live | [`archive/QuantOS-S199-PaperKit/`](archive/QuantOS-S199-PaperKit/) |
| **Claude-QuantOS** | Earlier Claude-built bots and scanners | Archived | [`archive/Claude-QuantOS/`](archive/Claude-QuantOS/) |
| **Oct-29 crash replay** | Replay of the 10-29 sell-off on V3 and V4 code | Led to the V4 risk/slot fixes | [`research/2026-10-31/crash_replay_20261029/`](research/2026-10-31/crash_replay_20261029/) |
| **Oct-31 strategy hunt** | ~4,170 pre-registered variants, deflated Sharpe, one held-out look per family | Only `crash_flush` + `alt_dip` survive | [`edge_vault/HUNT_SUMMARY_2026-10-31.md`](edge_vault/HUNT_SUMMARY_2026-10-31.md), [`research/2026-10-31/`](research/2026-10-31/) |

Other folders:
- [`edge_vault/`](edge_vault/): one write-up per tested edge (`EDGE_SPEC.md` format), dead ones included.
- [`docs/`](docs/): archived V1/V3 specs and notes (`LATER.md`, `RISK_STUDY.md`, and `research_phase4/` decisions).
- [`scripts/`](scripts/): superseded deploy script (`deploy_live50.sh` → now `scripts/deploy_three.py`).

Run the preserved V1/V3 test suite from the repo root:

```bash
PYTHONSAFEPATH=1 PYTHONPATH=legacy/v1_v3:legacy/v1_v3/src python -m unittest discover -s legacy/v1_v3/tests -t legacy/v1_v3
```
