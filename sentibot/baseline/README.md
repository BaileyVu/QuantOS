# Legacy Sentibot baseline source (reference only)

These eight `.py` files are the source-only copy of the user-provided
`sentibot_copy/` implementation. `SOURCE_MANIFEST.sha256` records each original
file's SHA-256 and exact relative source name. Only source files were copied;
no `.env`, tokens, cookies, downloaded candles, CSV/JSON data, logs, or runtime
state were copied.

This directory is retained to make the baseline implementation reviewable. Do
not execute or deploy any file in this directory. In particular, `paper.py`
has legacy output paths including `paper_perp_signals.jsonl`; it is reference
code only. The v2 shadow collector does not import `sentibot.baseline`, does
not launch these scripts, and has a fixed output filename
`reach_shadow.jsonl` under its required dedicated state directory.

## Launch paths

- **Legacy baseline, reference only:** the historical program was launched via
  `paper.py --once` or `paper.py --loop` from its app directory. That path is
  deliberately not supported from this vendored source directory.
- **Prepared shadow collector:** from the repository root, launch only the
  separate package entrypoint with isolated state:

  ```sh
  python -m sentibot.sentibot_reach_shadow \
    --once --state-dir /path/to/isolated-state/sentibot-reach
  ```

The shadow command never imports the reference-only source tree. The baseline
scoring module it uses is the separately maintained `sentibot/sentiment.py`,
which has public RSS/Arctic Shift inputs, expanded coin aliases, and the RSS
title-to-link map. It has no X bearer-token reader.

The original baseline source includes optional code paths that consult an
`X_BEARER_TOKEN` environment variable if its legacy sentiment/paper functions
are executed. No token value was read or copied. Do not execute that source or
configure credentials in the shadow environment.
