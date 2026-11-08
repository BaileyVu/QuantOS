# Sentibot reach shadow

This directory vendors the public sentiment scorer used for comparison and the
Exa/Jina shadow collector. The baseline collection includes RSS and public
Arctic Shift/Reddit sources. The eight original app source files are preserved
under `baseline/` with checksums as reference only, including the historical
paper writers. The shadow entrypoint never imports or executes those writers.
No cookies, credentials, or live state are part of this snapshot.

The collector emits only `reach_shadow.jsonl`, under a caller-selected
dedicated state directory. It refuses to place state inside the vendored source
directory and has no output-path option that could point at QuantOS signal or
account files. It stores scored samples and event provenance, not scraped body
text. No LLM is used. A bundled deterministic crypto lexicon is the only scorer,
and each sample records its scorer version and lexicon hash so ambient package
installations cannot silently change results.

Coin aliases and per-coin subreddit names cover the 29 symbols in
`quantos.paper.strategies.candidate_features.ALT_SOURCE_UNIVERSE` (USDT twins
for QuantOS USDC contracts). The collector reads public Binance `exchangeInfo` for currently trading USDC
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

Continuous sampling defaults to a 15-minute cadence (`--every 900`), including
collection time. The POSIX CLI enforces a hard 600-second cycle deadline
(`--cycle-timeout 600`), including DNS, redirects and slow reads. A timed-out
collection reports an error and does not publish usable scores. No deployment
or 72-hour collector has been run from this branch. The new deterministic scorer
is identified separately from the historical app scorer; score equality with
historical VADER installations is not claimed. See [preparation and rollback](SHADOW_PREPARATION.md).

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
The optional fail-closed stale-sentiment gate remains off by default; QuantOS
alerts when the baseline input is over 30 minutes old. These paths have synthetic
tests. A 72-hour soak, measured return report and gate-value report remain
unverified. The collector never changes the baseline file used by pD.
