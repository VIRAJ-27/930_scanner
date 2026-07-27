# 9:30 Scanner — Trigger 1-Minute Entry

## Market and data

- NSE cash equity, buy-only.
- HLC3 VWAP: `(High + Low + Close) / 3`, volume weighted from 09:15 IST.
- Three-minute candles are aligned to 09:15 IST.
- One setup and at most one trade per stock/day.

## Trigger

Only the three 3-minute candles starting at 09:30, 09:33 and 09:36 are
considered. The first red candle among them is the only Trigger candidate.

The Trigger is valid only when:

1. it closes above session VWAP;
2. the immediately previous contiguous 3-minute candle is green;
3. that previous candle closes above VWAP; and
4. the Trigger close is inside the previous candle's high-low range.

If the first red candle fails, or no red candle appears in the three-candle
window, discard that stock for the day.

## Four-candle guide window

The four 1-minute candles immediately after the Trigger closes are the complete
guide window. A strict break of the Trigger low before entry discards the stock.

The first green 1-minute candle in the window is G1. Its range must satisfy:

`(G1 High - G1 Low) / G1 Low <= 0.002`

That is a maximum range of 0.20%. If the first green candle is wider, if no
green candle appears, or if G1 forms too late to leave a G2 candle inside the
four-minute window, discard the stock.

## Entry

- G2 is the minute immediately following G1.
- A strict intraminute break above G1 high buys 100 shares immediately.
- If G2 only equals G1 high, G3 gets one strict-break opportunity.
- If G2 remains below G1 high, discard immediately; G3/G4 cannot rescue it.
- A strict G1-low break during G2 or G3 discards the stock before entry.
- G3 must strictly break G1 high. Otherwise discard the stock.

The backtest uses one NSE tick above G1 high and assumes the low-side failure
occurs first when both sides are present in the same 1-minute OHLC candle.

## Position management

- Initial stop: G1 low.
- Initial quantity: 100 shares.
- TP1: entry + `2.2 × (entry - G1 low)`.
- A stop is executed immediately when touched.
- When a 1-minute candle touches TP1, sell 50 shares at that candle's close.
- After TP1, move the remaining 50-share stop to entry price.
- Subsequently, only completed red 3-minute candle lows can raise the runner
  stop. The stop is monotonic and can never move down.
- Exit any remaining position at 15:15 IST.

Brokerage, taxes, exchange fees and slippage are excluded from backtests.

## Net RR

Each trade's `NetRR` is:

`Gross P&L / (100 × (Entry Price - G1 Low))`

Monthly NetRR is the sum of trade NetRR values for that month.
