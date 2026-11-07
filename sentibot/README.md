# Sentibot reach shadow

This directory vendors the public sentiment scorer used for comparison and the
Exa/Jina shadow collector. The baseline collection includes RSS and public
Arctic Shift/Reddit sources. Authenticated X access, paper trading, Binance
data downloads, `paper.py`, and the `paper_perp_signals.jsonl` writer are not
included. No cookies, credentials, or live state are part of this snapshot.

The collector emits only `reach_shadow.jsonl`, under a caller-selected
dedicated state directory. It refuses to place state inside the vendored source
directory and has no output-path option that could point at QuantOS signal or
account files. It stores scored samples and event provenance, not scraped body
text. No LLM is used. A bundled deterministic crypto lexicon is the only scorer,
and each sample records its scorer version and lexicon hash so ambient package
installations cannot silently change results.

Coin aliases and per-coin subreddit names cover the 29 symbols in
`quantos.paper.strategies.candidate_features.ALT_SOURCE_UNIVERSE` (USDT twins
for QuantOS USDC contracts). The current source does not prove coverage of any
additional dynamically eligible contract outside that explicit universe. The
collector now reads public Binance `exchangeInfo` for currently trading USDC
perpetuals and derives USDT-style score keys for their base assets. Search is
round-robin capped at 12 symbols per sample; rows report incomplete per-sample
query coverage. If exchange metadata is unavailable, rows retain the known
29-symbol fallback and explicitly mark universe coverage incomplete.

Each row records collection `started_at` and `available_at`; `t` equals
`available_at` because scoring completes after source fetches. Offline forward
reports must use availability time to avoid lookahead. Tests inject the
universe and completion clock and make no network requests.

## One offline check

From the repository root, with Python 3.11 or newer:

```sh
python -m unittest tests.unit.test_sentibot_shadow
```

These tests stub HTTP and DNS. They do not call Exa, Jina, Reddit, RSS feeds,
Binance, or any exchange endpoint.

## Manual shadow invocation

Use a clean, reviewed snapshot and a state path separate from any paper/demo
directory. `--once` is suitable for a single sample:

```sh
python -m sentibot.sentibot_reach_shadow \
  --once --state-dir /path/to/isolated-state/sentibot-reach
```

Continuous sampling defaults to 15 minutes (`--every 900`); values under one
minute are rejected. No deployment or 72-hour collector has been run from this
branch.

## Three-day measurement prerequisites

Before a 72-hour shadow soak, the reviewed snapshot still needs to be deployed
by Bailey with a dedicated state path and a recorded rollback procedure. Check
public DNS and HTTPS access to Exa, Jina, the configured RSS feeds, and Arctic
Shift/Reddit from the runner; API availability and rate limits are unverified.
Keep this collector isolated from the running Sentibot app and QuantOS state.

The output provides reach scores, RSS/Reddit baseline scores, and per-event URL,
published time, first-seen time, novelty, and coin hint. The offline
`sentibot.forward_report` utility summarizes precollected 4-hour/24-hour returns
and existing `SENTIMENT_SHADOW` gate outcomes when matching quote journals and
events are available; it does not collect market history.
The optional fail-closed stale-sentiment gate and >30-minute alert are also a
separate main-agent integration task. A 72-hour soak, return report, gate-value
report, and staleness alert remain unverified.
