# 9:30 Scanner — Trigger Percentage

## Market and data

- NSE cash equity, buy-only.
- HLC3 VWAP: `(High + Low + Close) / 3`, volume weighted from 09:15 IST.
- Three-minute candles are aligned to 09:15 IST.
- One setup and at most one trade per stock/day.

## Trigger

Only the 3-minute candles starting at 09:30, 09:33 and 09:36 are considered.
The first red candle among them is the only Trigger candidate.

The Trigger is valid only when:

1. it closes above session VWAP;
2. the immediately previous contiguous 3-minute candle is green;
3. that previous candle closes above VWAP; and
4. the Trigger close is inside the previous candle's high-low range.

If the first red candle fails, or no red candle appears in the three-candle
window, discard the stock for the day.

## G1 and entry

- Inspect the next three completed 1-minute candles after the Trigger.
- The first green candle is G1. If none is green, discard the stock.
- A strict Trigger-low break before or during G1 discards the stock.
- After G1, inspect only the immediately following 1-minute candle.
- Buy 100 shares immediately when that candle strictly breaks G1 high.
- A strict G1-low or Trigger-low break before entry discards the stock.
- If that next candle does not break G1 high, discard the stock.

Before accepting the G1-high break, classify the setup using completed candles
only:

- **Silver**: the 1-minute EMA20 has risen at least 0.116% over the previous
  five completed 1-minute candles, and G1's real body is at least 57.9% of its
  high-low range.
- **Normal**: if Silver fails, the 3-minute EMA20 has risen at least 0.01% over
  the previous two completed 3-minute candles.
- Silver has precedence when both conditions pass. If neither tier passes,
  discard the stock for the day.

EMA20 is continuous across trading sessions. Backtests warm it with all
available candles before the requested start date, and a continuously running
live process preserves its completed-candle EMA history across day resets.

The backtest uses one NSE tick above G1 high and assumes the low-side failure
occurs first when both sides are present in the next 1-minute OHLC candle.

## Percentage target

Trigger range percentage:

`TriggerRange% = (Trigger High - Trigger Low) / Trigger Low × 100`

EP percentage:

- if `TriggerRange% < 0.50%`, `EP% = TriggerRange% × 1.4`;
- if `TriggerRange% >= 0.50%`, `EP% = TriggerRange% + 0.10%`.

Levels:

- `R1 = Trigger High × (1 + EP% / 100)`;
- `R2 = Entry + 3 × (Entry - Trigger Low)`;
- `TP1 = minimum(R1, R2)`.

If entry itself is already at or above TP1, TP1 is considered touched and the
70-share partial exit occurs at that entry minute's close.

## Stops and exits

- Initial stop: Trigger low.
- After entry, the first completed 3-minute candle that closes strictly above
  Trigger high moves the stop to G1 low. The stop can never move down.
- When a 1-minute candle touches TP1, sell 70 shares at that candle's close.
- After TP1, move the remaining 30-share stop to Trigger high.
- Subsequently, every completed 5-minute candle low can raise the runner stop.
  Five-minute candles are aligned from 09:15 and the stop remains monotonic.
- Stop exits are immediate when touched.
- Exit any remaining position at 15:15 IST.

## Reporting

`NetRR = Gross P&L / (100 × (Entry - Trigger Low))`

Monthly NetRR is the sum of trade NetRR. Backtest reports exclude brokerage,
taxes, exchange fees and slippage.
