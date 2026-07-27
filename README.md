# 9:30 VWAP Equity Scanner

Python/Angel One scanner for all current NSE stock F&O underlyings. It trades
cash equity only and defaults to paper execution.

## Confirmed strategy

- Exchange-aligned 3m candles; HLC3 session VWAP resets at 09:15 IST.
- From 09:30 through 09:51, the first red 3m candle is the stock's only trigger
  candidate.
- The Trigger must close above VWAP and inside the previous contiguous green
  candle's range; that previous candle must also close above VWAP.
- A failed first-red Trigger discards the stock for the entire day.
- During the immediately next 3m candle (C1), a strict Trigger-high break buys
  100 shares immediately with Trigger low as the initial SL.
- At C1 close, move SL up to C1 low and calculate standard 1.5R from the actual
  entry price and C1 low.
- If C1 itself already reached the resulting TP1, sell 50 shares at C1 close.
- If Trigger high never breaks during C1, require the normal green C1 with
  `C1 low >= Trigger low`, then use the original C2 C1-high breakout entry.
- Otherwise, TP1 sells 50 shares at the close of the one-minute candle that
  touched 1.5R.
- Trail the remaining 50 shares with the monotonic previous completed 3m
  candle low.
- Maximum one trigger candidate/trade per stock/day. Exit any runner at 15:15.

The precise rule treatment is in `STRATEGY_SPEC.md`.

## First run

1. Double-click `setup_scanner.cmd`.
2. The scanner first looks for a local `.env`. If absent, it reuses the
   existing VBOS project's broker `.env`. Set `SCANNER_BROKER_ENV` to override
   that location.
3. Start before 09:15 with `start_paper.cmd`.
4. Review `LiveReports/Scanner930_Dashboard.xlsx` and the CSV ledgers.

## Live-order lock

Paper-test first. Live mode requires both:

1. `LIVE_APPROVAL.txt` containing exactly `LIVE_EQUITY_ORDERS`.
2. The explicit confirmation already present in `start_live.cmd`.

Live orders are NSE `INTRADAY` market orders. Broker order IDs, status, and
average fill price are recorded in `OrderBook`. The program never retries an
uncertain order automatically.

## Reports

- `TradeBook`: entry route, stops, TP1, runner exit, and P&L
- `SetupLedger`: counted setups and failures
- `OrderBook`: paper/live broker-order audit trail
- `LiveEvents`: complete chronological rule events
- `CompletedCandles`: one- and three-minute OHLCV/VWAP
- `DailySummary`, `StockSummary`, `LiveStatus`, and dashboard workbook

Run tests:

```powershell
python -m unittest discover -s tests -v
```
