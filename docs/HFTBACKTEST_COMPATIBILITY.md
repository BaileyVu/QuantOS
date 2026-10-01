# QuantOS HFT research export and hftbacktest semantics

QuantOS records normalized depth, trade, ticker, mark/funding, receive-time, and
processing-time fields as immutable ZSTD Parquet batches. The
`export_hftbacktest_research` adapter produces a flat research table containing
exchange/local timestamps, update IDs, trades, and depth payloads suitable for
conversion into the event representation selected by the installed
`hftbacktest` version.

QuantOS intentionally differs in these ways:

- live paper uses a conservative trade-only queue-depletion model; unexplained
  depth cancellation never advances a paper order;
- touching a quote never fills it;
- feed latency is observed, while order acknowledgement latency is an explicit
  configured paper input;
- BTCUSDC and BTCUSDT fees are explicit QuantOS configuration and no
  institutional maker rebate is imported;
- inventory is limited to one bounded direction and 100 quote-currency units of
  starting equity, not the notebook's institutional sizing;
- exported events contain no invented order latency, queue probability, or
  fill. Those remain explicit research-simulator inputs.

These differences make QuantOS paper fills at least as conservative as its
available public evidence. Version-specific hftbacktest binary conversion is a
research step, not part of the live paper runtime.
