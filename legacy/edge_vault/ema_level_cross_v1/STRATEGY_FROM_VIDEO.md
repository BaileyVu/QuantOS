# Cipher Jay "EMA Cross Strategy" — rules as stated in the video

Source: Screen Recording 2026-10-21 at 14.19.27.mov (narration transcribed with Whisper; full transcript on the Mac at PythonProject/emacross/transcript.txt).
Claimed result: 100 trades across gold, bitcoin, EUR/USD, 68 winners / 32 losers. 5-minute charts.

## Step 1 — signal
- 9 EMA (green) and 20 EMA (white), 5m chart.
- 9 crosses above 20 = look for buys; below = look for sells. Entry on the close of the cross candle.
- On its own it's a "coin flip": wins in trends, stops out in sideways markets.

## Step 2 — level filter (must have)
- Only take a cross from a support/resistance level.
- A level needs price to push out of an area, come back and hold it again: 2 touches. Trade the 3rd touch.
- Long: price drops into the support zone, bounces, then 9 crosses above 20 → buy on that candle's close, "just above the zone". If price cuts straight through the zone, the setup is invalid.
- Flip zone: a level that rejected price 2+ times, then price breaks and closes through it, comes back and retests from the other side → trade in the break direction on the cross.
- Plain S/R vs flip: if price has never broken the level, it's plain S/R (trade the rejection).
- Stop: just beyond the zone. Target: last swing high/low, or 1:1.5 R:R (he uses 1:1.5).
- His example loss: a level with only 1 touch. Don't do that.

## Step 3 — context (improves it)
- Zoom out on the same 5m chart: trade only with the trend (HH/HL for longs, LH/LL for shorts).
- Fibonacci on the last big leg (swing high → swing low): sell only in premium (above 50%), buy only in discount (below 50%).
- "Miss step 3 and you'll still win some. Miss step 2 and you're back to a coin flip."
