# 2nd Candle Entry Backtest

Period: 2026-07-14 through 2026-07-24
Universe: 210 stocks
Input: 210 one-minute OHLCV CSV files

| Metric | Result |
|---|---:|
| Trades | 28 |
| Winners | 14 |
| Losers | 14 |
| Win rate | 50.00% |
| Net P&L | -3299.60 |
| Net RR | 8.0658 |
| Average RR | 0.2881 |
| TP1 trades | 14 |
| Normal trades | 19 |
| Silver trades | 2 |

The standard entry route is G1 -> immediate green G2 -> G2 high break within the next five 1-minute candles. If G1 closes above Trigger High, the special next-candle high-break route is used. Initial SL for the new route is G1 low and TP1 is fixed at 1.5R. Existing B1 logic and post-TP1 stop/trailing logic remain unchanged.

Net RR is the sum of trade GrossPnL / initial trade risk, matching the project's reporting convention. P&L is cash P&L from the configured share quantities and therefore can differ in direction from summed RR.

Tests: 47 passed, 0 failed.
