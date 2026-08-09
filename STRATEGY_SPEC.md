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

## Large-green route selection

After a valid Trigger forms, inspect every completed green 3-minute candle
starting at 09:21 and ending with the candle immediately before the Trigger.
For each candle calculate:

`Range% = (High - Low) / Low x 100`

If any such green candle has `Range% > 0.85%`, discard the stock for the day.
Otherwise, if any green candle has `Range% > 0.55%`, use the large-green B1
route. Both thresholds are strict: exact boundary values remain allowed. The
largest qualifying range is retained in the audit report. If no candle
qualifies, use the standard G1 route below.

## Large-green B1 entry

- Trigger range must be no greater than 0.60%; a larger Trigger is rejected.
- Inspect only the first six completed 1-minute candles after the Trigger.
- The first candle that closes strictly above Trigger high is B1.
- If none of those six candles closes above Trigger high, discard the stock.
- Inspect only the immediately following 1-minute candle, X1.
- Buy immediately on a strict break above B1 high during X1.
- If X1 does not break B1 high, discard the stock for the day.
- B1 entries do not use the Normal or Silver EMA entry-quality filters.
- The initial stop after entry is B1 low.
- TP1 depends on B1's full range: below 0.10% uses 3R; from 0.10% through
  0.35% uses 2R; above 0.35% uses 1.2R. The Trigger-percentage R1 cap does
  not apply to this route.
- From B1, inspect X1, X2 and X3. At least one must close strictly above B1
  high. A high break without such a close does not satisfy confirmation.
- If none closes above B1 high, exit the open position at X3's close and label
  it `BE_EXIT_NO_CLOSE_ABOVE_B1_HIGH`. This label describes the rule; the
  actual exit price is X3 close and may be above or below entry.
- A strict Trigger-low break at any point before entry still discards the stock.

## Standard G1 entry

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

The backtest retains a 0.05 breakout buffer above G1/B1 high and assumes the low-side failure
occurs first when both sides are present in the next 1-minute OHLC candle.

## Percentage target

Trigger range percentage:

`TriggerRange% = (Trigger High - Trigger Low) / Trigger Low × 100`

EP percentage:

- if `TriggerRange% < 0.20%`, `EP% = TriggerRange% × 2`;
- if `0.20% <= TriggerRange% < 0.50%`, `EP% = TriggerRange% × 1.4`;
- if `0.50% <= TriggerRange% <= 0.60%`, `EP% = TriggerRange% + 0.10%`.

Levels:

- `R1 = Trigger High × (1 + EP% / 100)`;
- for Normal/Silver, G1 range below 0.08% makes R2 equal 5R; from 0.08%
  through 0.30% makes R2 equal 1.3R; above 0.30% makes R2 equal 1.5R;
- `S3 = Trigger High × 1.0033`;
- for Standard Normal/Silver entries, `TP1 = maximum(minimum(R1, R2), S3)`.
  S3 is a floor: TP1 can never be below 0.33% above Trigger high.
- Golden and B1 target rules remain unchanged and do not use the S3 floor.

If entry itself is already at or above TP1, TP1 is considered touched and the
70-share partial exit occurs at that entry minute's close.

## Golden entry overlay

Every otherwise-valid G1 or B1 entry is also classified using the completed
Trigger and reference candle. The reference is G1 for Normal/Silver and B1 for
the large-green route.

- **Alpha**: Trigger starts exactly at 09:33 and reference range is no greater
  than 0.20%, where range is `(High - Low) / Low`.
- **Beta**: Alpha is true and Trigger same-slot RVOL10 is at least 0.575.
  Same-slot RVOL10 is Trigger volume divided by the median volume of the same
  3-minute time slot over the previous 10 available sessions, with at least
  three prior observations.
- **Gamma**: Alpha is true and Trigger 3-minute RVOL20 is at least 0.57.
  RVOL20 is Trigger volume divided by the median volume of the previous 20
  completed 3-minute candles, with at least five prior candles.

If Alpha, Beta or Gamma is true, classify the trade as **Golden**. Because Beta
and Gamma include Alpha, every Beta/Gamma trade is also Alpha; the individual
flags are retained for reporting. A Golden entry:

- uses 200 shares, with 140 at TP1 and 60 as the runner;
- uses a fixed TP1 at 1.3R from entry and initial stop;
- ignores the Trigger-percentage R1 cap; and
- otherwise keeps the existing stop upgrades, 1-minute TP close execution and
  5-minute trailing logic.

All other valid entries remain **Standard**: 100 shares, 70/30 split and the
existing route-specific dynamic TP1 rules.

Option paper execution mirrors the quality sizing: one configured lot-equivalent
for Standard and two for Golden. Local database history warms the two RVOL
metrics when available. Alpha still classifies correctly when a fresh runner has
insufficient historical volume; Beta/Gamma remain false until their minimum
history requirements are met.

## Stops and exits

- Standard G1 initial stop: Trigger low.
- Large-green B1 initial stop: B1 low, activated only after entry.
- After entry, the first completed 3-minute candle that closes strictly above
  Trigger high moves a standard G1 stop to G1 low. This step does not change
  the large-green B1 stop. A stop can never move down.
- When a 1-minute candle touches TP1, sell the configured TP1 quantity at that
  candle's close: 70 Standard or 140 Golden.
- After TP1, move a standard G1 runner stop to Trigger high and a B1 runner
  stop to breakeven (entry price).
- Subsequently, every completed 5-minute candle low can raise the runner stop.
  Five-minute candles are aligned from 09:15 and the stop remains monotonic.
- Stop exits are immediate when touched.
- Exit any remaining position at 15:15 IST.

## Reporting

`NetRR = Gross P&L / initial trade risk`, where initial risk is 100 shares
times Entry minus Trigger low for G1, or Entry minus B1 low for B1.

Monthly NetRR is the sum of trade NetRR. Backtest reports exclude brokerage,
taxes, exchange fees and slippage.

## Option paper overlay

- Trigger, G1, entry, SL, TP1, trailing and 15:15 exit decisions remain based
  exclusively on the underlying stock.
- Use the nearest unexpired monthly stock CE.
- For strike gaps below ₹10, choose the mathematically nearest strike; ties
  choose the lower strike.
- For strike gaps of ₹10 or more, calculate progress from the lower strike to
  the upper strike. Choose the upper strike only at 75% progress or higher;
  otherwise choose the lower strike.
- Paper entry fills at the best ask and exits fill at the best bid.
- Reject missing/stale quotes and entry spreads above 5%.
- The initial evaluation uses one lot-equivalent with the stock strategy's
  70% TP1 and 30% runner split applied proportionally. This normalized split
  is not necessarily executable with one real exchange lot.
- The option overlay cannot submit live option orders.
