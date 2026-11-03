# Dashboard v5 — glass terminal (approved by bailey 2026-11-03)

Reference markup: `dashboard_v5_mockup.dc.html` (+ `dashboard_v5_feargreed.dc.html`).
It is a design-tool component (`<x-dc>`, `{{holes}}`, `<sc-for>`, `<sc-if>`, a `renderVals()` class).
Port it to plain HTML/CSS/vanilla JS served by `quantos.paper.webdash`; copy CSS (the `<style>` block) and inline styles verbatim.

## Look
- Background pure #000; three blurred colour fields (opacity .32/.25/.2) + faint decorative candlesticks (opacity .3) + SVG grain (opacity .06). Glass needs something behind it.
- Panels `.g`: gradient glass, backdrop-filter blur(22px) saturate(150%), inset top highlight, masked gradient rim (`::before`), opaque fallback via `@supports not`.
- Fonts: Inter Tight (UI) + IBM Plex Mono (numbers). Up #3DD68C, down #F2637E, warn #F5B562, blue #9CCBFF, accent #8EC5FF.

## Behaviour
- Header tabs Overview / Strategies / Trades / Sentiment / Logs switch client-side (no reload). Remember last tab in localStorage.
- Poll `/api/state` every 5s and `/api/news` every 60s; repaint numbers in place.
- Overview: equity curve (pD), account + slots, Fear & Greed, strategies table, alerts (derived from health), news.
- Strategies: full table, alt-ML top scores vs threshold, signal funnel, reject reasons.
- Trades: candle chart of each open position's symbol (15m, last 96 bars, Binance klines) with entry + mark lines and shaded since-entry; open positions; open orders (protective stops); fills.
- Sentiment: sentibot composite bars, gate mode, F&G, news.
- Logs: health cards; event log newest first, collapse consecutive identical events into one row with ×count.
- News: all items newest first in a scroll box (max-height ~560px), thumbnail (n.image) or source-initials tile, link opens article in new tab. Source chips All / CoinDesk / Cointelegraph / Watcher Guru with counts; a source chip shows its latest 15 + "See more on <source>" (coindesk.com, cointelegraph.com, t.me/s/WatcherGuru). LIVE dot + last polled time + feeds ok.
- Strip "JUST IN:" and trailing "@WatcherGuru" from Watcher Guru headlines.
- HL copy is dropped: no HL panels.

## New endpoints (server side, cached)
- `/api/klines?symbol=NEARUSDC&interval=15m&limit=96` → proxy Binance /api/v3/klines, cache 15s.
- `/api/fng` → proxy https://api.alternative.me/fng/?limit=8, cache 10 min; show today, yesterday, 7d ago.
