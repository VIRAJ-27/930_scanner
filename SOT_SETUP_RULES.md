# SOT SETUP Backtest Rules

## Scope

- 3-minute candles are aggregated from Angel One 1-minute OHLCV bars aligned from 09:15 IST.
- Backtest date range and the F&O universe are provided as command-line inputs.
- Exclude a symbol for the whole selected period if any included 1-minute candle high exceeds ₹9,999.
- Trigger candle starts from 09:30 through 12:00 IST, inclusive.

## Opening volume references

- Basket 1: 09:15, 09:18, 09:21, 09:24 3-minute candle volumes.
- Basket 2: 09:18, 09:21, 09:24, 09:27 3-minute candle volumes.
- Record the maximum volume in each basket.

## Trigger and active candle

A trigger must make a new running day high, close red, exceed at least one basket maximum in volume, exceed the lower of the previous two 3-minute candle volumes, and rank first or second by volume among completed 3-minute candles from 09:18 through the trigger. Exclude the 09:15 first candle from this volume ranking; ties at second place qualify. This rank uses only candles available when the trigger closes. Before the trigger candle, at least two earlier green 3-minute candles must have closed above the day's first 3-minute candle high. Each counted candle must close above its open and above the first candle's high. The trigger candle itself does not count toward the two closes.

If the trigger candle closes below the low of the immediately preceding 3-minute candle, label the setup `WAITING_FOR_ENTRY`. This is a label only and does not change the entry rules.

The next complete 3-minute candle is active if its high does not exceed the trigger high. Discard the setup if it exceeds the trigger high.

For trigger candles starting before 09:45, bypass the VWAP-touch and 0.30% range filters. For triggers starting at 09:45 or later, reject if the trigger or active candle touches/crosses session VWAP (VWAP is within the candle low-high, inclusive).

After the active candle closes, calculate its range to the 09:18 candle high. The current implementation uses the absolute distance from active close to the 09:18 high divided by active close. For trigger candles starting at 09:45 or later, keep the setup only when the result is 0.30% or greater. Pre-09:45 triggers bypass this range filter.

## Entry and risk

- Entry level is the lower low of trigger and active candles.
- Look for a 1-minute break of that level during the next three complete 3-minute candles after the active candle.
- If trigger high breaks first, discard the setup. If a 1-minute candle touches both boundaries, assume trigger high broke first.
- Entry is at the break level, or at the minute open when it gaps below the entry level.
- Initial stop is trigger high.

## Target and exits

- Calculate a continuously recursive 50-period EMA of 3-minute closes. Seed it with the first available 3-minute close and use the latest fully closed 3-minute EMA at entry time.
- At entry, the short target starts at the lower numeric price of (a) 0.50% below entry and (b) the latest fully closed 3-minute EMA50.
- After each completed 3-minute candle, update the target to the lower of the fixed 0.50%-below-entry price and the latest fully closed 3-minute EMA50. The EMA50 component trails as the EMA changes; exit when a 1-minute bar reaches the updated target.
- If stop and target are both touched in one 1-minute candle, assume stop first. Gap fills use the minute open when it is less favorable than the stop/target level.
- Close any open trade at the last available 1-minute close of the day.

The backtest is gross and excludes fees, taxes, spread, slippage, short-sale availability, position sizing, and simultaneous-capital constraints. Distinct setups are evaluated independently.

## Run

```powershell
python sot_setup_backtest.py `
  --data .\data_2026-10 `
  --universe-csv .\FO_Universe_2026-10-24.csv `
  --output .\backtest_sot_2026-10-01_to_2026-10-30 `
  --start 2026-10-01 --end 2026-10-30
```

Outputs include `Trades.csv`, `SetupAudit.csv`, `UniverseCoverage.csv`, `ExcludedStocks.csv`, `DataFailures.csv`, `Summary.csv`, and `Summary.json`.

