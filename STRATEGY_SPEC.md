# 9:30 Scanner — Confirmed Source of Truth

## Market and position

- Universe: all current NSE stock F&O underlyings.
- Instrument traded: NSE cash equity, intraday product.
- Direction: buy only.
- Quantity: 100 shares (two lots of 50).
- Time zone: Asia/Kolkata.
- Candle boundaries are exchange aligned. The 09:30 3m candle covers
  09:30:00–09:32:59 and completes at 09:33:00.

## VWAP

- Reset every session at 09:15.
- For every completed 3m candle:
  `typical price = (high + low + close) / 3`.
- `VWAP = cumulative(typical price × candle volume) / cumulative volume`.
- “Above VWAP” is strict: close must be greater than VWAP.

## First-red trigger rule

- Eligible trigger starts: 09:30, 09:33, …, 09:51.
- Starting at 09:30, ignore green/doji candles until the first red 3m candle.
- That first red candle is the day's only trigger candidate for the stock.
- It must close above its VWAP.
- Its immediately previous contiguous 3m candle must be green and close above
  its own VWAP.
- Trigger close must be inside the previous candle's inclusive range:
  `previous low <= trigger close <= previous high`.
- If the first red candle fails any condition, discard the stock for the day.

## Early trigger-break entry

- The immediately next 3m candle after the valid Trigger is called C1.
- During C1, a sequential tick strictly above Trigger high buys 100 shares.
- Initial stop is Trigger low.
- If a tick trades strictly below Trigger low before entry, discard the stock.
- Stop touches after entry (`price <= active stop`) exit immediately.
- At C1 close, move the active stop up to C1 low. Never lower the stop.
- After C1 closes, calculate:
  `risk = entry price - C1 low`
  and `TP1 = entry price + 1.5 × risk`.
- If C1 high already reached/exceeded the final TP1, sell 50 shares at C1
  close.

## Valid-C1 fallback and C2

- If Trigger high does not break during C1, apply the original C1 rules.
- C1 must close green and its low must be greater than or equal to Trigger low.
- If C1 is invalid, discard the stock for the day.
- Only the immediately next 3m candle after valid C1 is C2.
- If `price < C1 low` first, discard the stock.
- If `price > C1 high` first, buy 100 shares immediately with C1 low as SL.
- Touching either level is not a strict break.
- If C2 completes without a C1-high break, discard the stock.

## TP1 and runner

- For either entry route, TP1 is standard 1.5R using entry price and C1 low.
- After C1, when a tick reaches/exceeds TP1, remember that one-minute candle
  and sell 50 shares at its completed close.
- After the 50-share TP1 exit, the remaining 50-share runner stop becomes the
  greater of the existing stop and the latest completed 3m candle low.
- On every later completed 3m candle, raise the runner stop to its low only if
  it increases the stop.
- Any remaining quantity exits at 15:15.
- An operator/service shutdown also squares off scanner-managed positions.

## Limits, ordering, and reports

- Maximum one trigger candidate and one trade per stock/day.
- A valid Trigger, failed C1/C2, entered trade, SL, TP1, or runner exit ends
  further scanning for that stock that day.
- On sequential ticks, an active stop is evaluated before target logic.
- For ambiguous one-minute OHLC bars in the backtest, the stop/low break wins.
- Paper fills use the stock signal price; live fills/status come from Angel's
  order book and remain separately auditable.
