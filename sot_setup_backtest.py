#!/usr/bin/env python3
"""Backtest SOT SETUP on Angel One 1-minute OHLCV data."""
from __future__ import annotations
import argparse, csv, json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sot_core import Bar, aggregate_three_minute, ema_series, load_minute_bars, load_universe, vwap_series

PRICE_CAP = 9999.0
SCAN_START, SCAN_END = time(9, 30), time(12, 0)
FILTER_START = time(9, 45)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, action='append', required=True, help='Folder of SYMBOL_1m.csv files; repeat to merge history')
    p.add_argument('--universe-csv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--quiet', action='store_true')
    return p.parse_args()


def fmt(dt: datetime | None) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S%z') if dt else ''


def write_rows(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or (list(rows[0]) if rows else [])
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=names)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    symbols = load_universe(args.universe_csv)
    sources: dict[str, list[Path]] = defaultdict(list)
    for d in args.data:
        for f in d.glob('*_1m.csv'):
            sources[f.name.removesuffix('_1m.csv').upper()].append(f)

    trades: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for n, symbol in enumerate(symbols, 1):
        paths = sources.get(symbol, [])
        if not paths:
            coverage.append({'symbol': symbol, 'data_available': False, 'price_cap_excluded': False,
                             'selected_period_sessions': 0, 'trigger_rule_passes': 0, 'active_setups': 0, 'entries': 0})
            continue
        try:
            minute_map: dict[datetime, Bar] = {}
            for path in paths:
                for m in load_minute_bars(path, symbol):
                    minute_map[m.start] = m
            minutes = [minute_map[k] for k in sorted(minute_map)]
            three = aggregate_three_minute(minutes)
            emas, vwaps = ema_series(three), vwap_series(three)
            ema_by_start = {b.start: emas[i] for i, b in enumerate(three)}
            vwap_by_start = {b.start: vwaps[i] for i, b in enumerate(three)}
            minute_days: dict[date, list[Bar]] = defaultdict(list)
            three_days: dict[date, list[tuple[int, Bar]]] = defaultdict(list)
            for m in minutes:
                if args.start <= m.start.date() <= args.end:
                    minute_days[m.start.date()].append(m)
            for i, b in enumerate(three):
                if args.start <= b.start.date() <= args.end:
                    three_days[b.start.date()].append((i, b))
            oct_mins = [m for day in minute_days.values() for m in day]
            max_price = max((m.high for m in oct_mins), default=0)
            if max_price > PRICE_CAP:
                excluded.append({'symbol': symbol, 'max_selected_period_1m_high': max_price,
                                 'reason': 'Selected-period 1-minute high exceeded 9999'})
                coverage.append({'symbol': symbol, 'data_available': True, 'price_cap_excluded': True,
                                 'selected_period_sessions': len(minute_days), 'trigger_rule_passes': 0, 'active_setups': 0, 'entries': 0})
                continue
            rule_passes = active_setups = entries = 0
            for day, indexed_bars in sorted(three_days.items()):
                bars = [b for _, b in indexed_bars]
                indices = [i for i, _ in indexed_bars]
                by_hm = {b.start.strftime('%H:%M'): j for j, b in enumerate(bars)}
                needed = ('09:15', '09:18', '09:21', '09:24', '09:27')
                if not all(t in by_hm for t in needed):
                    continue
                basket1 = bars[by_hm['09:15']:by_hm['09:24'] + 1]
                basket2 = bars[by_hm['09:18']:by_hm['09:27'] + 1]
                if len(basket1) != 4 or len(basket2) != 4:
                    continue
                max_b1, max_b2 = max(b.volume for b in basket1), max(b.volume for b in basket2)
                first_high = bars[by_hm['09:15']].high
                high_0918 = bars[by_hm['09:18']].high
                day_minutes = minute_days.get(day, [])
                minute_by_start = {m.start: m for m in day_minutes}
                running_high = bars[0].high
                close_above_first_count = 0
                for j, trigger in enumerate(bars):
                    tm = trigger.start.time()
                    if tm < SCAN_START or tm > SCAN_END:
                        close_above_first_count += trigger.close > first_high and trigger.close > trigger.open
                        running_high = max(running_high, trigger.high)
                        continue
                    prior_close_count = close_above_first_count
                    if j < 2:
                        close_above_first_count += trigger.close > first_high and trigger.close > trigger.open
                        running_high = max(running_high, trigger.high)
                        continue
                    prev1, prev2 = bars[j - 1], bars[j - 2]
                    volume_bars_through_trigger = bars[by_hm['09:18']:j + 1]
                    daily_volume_rank = 1 + sum(b.volume > trigger.volume for b in volume_bars_through_trigger)
                    conditions = {
                        'new_running_day_high': trigger.high > running_high,
                        'red_close': trigger.close < trigger.open,
                        'basket_volume': trigger.volume > max_b1 or trigger.volume > max_b2,
                        'prior_two_volume': trigger.volume > min(prev1.volume, prev2.volume),
                        'prior_two_green_closes_above_first_high': prior_close_count >= 2,
                        'prior_closes_above_first_high_count': prior_close_count,
                        'top_two_volume_so_far_excluding_first_candle': daily_volume_rank <= 2,
                        'volume_rank_so_far_excluding_first_candle': daily_volume_rank,
                    }
                    if not all(conditions.values()):
                        close_above_first_count += trigger.close > first_high and trigger.close > trigger.open
                        running_high = max(running_high, trigger.high)
                        continue
                    rule_passes += 1
                    trigger_label = 'WAITING_FOR_ENTRY' if trigger.close < prev1.low else ''
                    trig_vwap = vwap_by_start[trigger.start]
                    early_filters_bypassed = trigger.start.time() < FILTER_START
                    if not early_filters_bypassed and trigger.low <= trig_vwap <= trigger.high:
                        audits.append({'symbol': symbol, 'date': day.isoformat(), 'trigger_time': fmt(trigger.start),
                                       'status': 'REJECT_TRIGGER_TOUCHES_VWAP', 'trigger_vwap': trig_vwap,
                                       'active_time': '', 'active_vwap': '', 'active_range_to_0918_pct': '',
                                       'trigger_label': trigger_label, 'early_filters_bypassed': False, **conditions})
                        close_above_first_count += trigger.close > first_high and trigger.close > trigger.open
                        running_high = max(running_high, trigger.high)
                        continue
                    active = bars[j + 1] if j + 1 < len(bars) else None
                    if active is None or active.start != trigger.start + timedelta(minutes=3):
                        audits.append({'symbol': symbol, 'date': day.isoformat(), 'trigger_time': fmt(trigger.start),
                                       'status': 'NO_COMPLETE_ACTIVE_CANDLE', 'trigger_vwap': trig_vwap,
                                       'active_time': '', 'active_vwap': '', 'active_range_to_0918_pct': '',
                                       'trigger_label': trigger_label,
                                       'early_filters_bypassed': early_filters_bypassed, **conditions})
                    elif active.high > trigger.high:
                        audits.append({'symbol': symbol, 'date': day.isoformat(), 'trigger_time': fmt(trigger.start),
                                       'status': 'ACTIVE_BROKE_TRIGGER_HIGH', 'trigger_vwap': trig_vwap,
                                       'active_time': fmt(active.start), 'active_vwap': vwap_by_start[active.start],
                                       'active_range_to_0918_pct': '',
                                       'trigger_label': trigger_label,
                                       'early_filters_bypassed': early_filters_bypassed, **conditions})
                    else:
                        active_vwap = vwap_by_start[active.start]
                        active_range_pct = abs(active.close - high_0918) / active.close * 100 if active.close else 0
                        if early_filters_bypassed:
                            status = 'ACTIVE_SETUP_PRE_0945_FILTERS_BYPASSED'
                        elif active.low <= active_vwap <= active.high:
                            status = 'REJECT_ACTIVE_TOUCHES_VWAP'
                        else:
                            if active_range_pct < 0.30:
                                status = 'ACTIVE_RANGE_BELOW_0.30_PERCENT'
                            else:
                                status = 'ACTIVE_SETUP'
                        if status in ('ACTIVE_SETUP', 'ACTIVE_SETUP_PRE_0945_FILTERS_BYPASSED'):
                            active_setups += 1
                            combined_low = min(trigger.low, active.low)
                            entry = None
                            entry_time = None
                            invalidation_time = None
                            first_after_active = active.start + timedelta(minutes=3)
                            deadline = active.start + timedelta(minutes=12)
                            for m in day_minutes:
                                if not first_after_active <= m.start < deadline:
                                    continue
                                if m.high >= trigger.high:
                                    invalidation_time = m.start
                                    break
                                if m.low <= combined_low:
                                    entry = min(m.open, combined_low)
                                    entry_time = m.start
                                    break
                            ema_entry = None
                            target = None
                            exit_target = None
                            exit_price = None
                            exit_time = None
                            exit_reason = ''
                            if entry_time is not None:
                                entries += 1
                                completed_ema = [b for b in three if b.start.date() == day and b.start + timedelta(minutes=3) <= entry_time]
                                # EMA50 remains continuous over the full available 3-minute history.
                                ema_entry = ema_by_start[completed_ema[-1].start] if completed_ema else None
                                if ema_entry is None:
                                    status = 'ENTRY_NO_COMPLETED_EMA'
                                else:
                                    target_050 = entry * (1 - 0.005)
                                    target = min(target_050, ema_entry)
                                    for m in day_minutes:
                                        if m.start < entry_time:
                                            continue
                                        if m.high >= trigger.high:
                                            exit_price = max(m.open, trigger.high)
                                            exit_time, exit_reason = m.start, 'STOP'
                                            break
                                        completed_ema = [b for b in three if b.start.date() == day
                                                         and b.start + timedelta(minutes=3) <= m.start]
                                        current_ema = ema_by_start[completed_ema[-1].start] if completed_ema else ema_entry
                                        current_target = min(target_050, current_ema)
                                        if m.low <= current_target:
                                            exit_price = min(m.open, current_target)
                                            exit_target = current_target
                                            exit_time, exit_reason = m.start, 'TARGET_MIN_0.50_OR_EMA50'
                                            break
                                    if not exit_reason and day_minutes:
                                        exit_price, exit_time, exit_reason = day_minutes[-1].close, day_minutes[-1].start, 'EOD'
                                    status = 'TRADE_CLOSED'
                            elif invalidation_time:
                                status = 'TRIGGER_HIGH_BROKE_BEFORE_ENTRY'
                            else:
                                status = 'NO_ENTRY_WITHIN_THREE_CANDLES'
                            trades.append({
                                'symbol': symbol, 'date': day.isoformat(),
                                'basket1_max_volume': max_b1, 'basket2_max_volume': max_b2,
                                'day_first_high': first_high, 'day_0918_high': high_0918,
                                'trigger_label': trigger_label,
                                'trigger_close_below_previous_low': trigger_label == 'WAITING_FOR_ENTRY',
                                'trigger_volume_rank_so_far_excluding_first_candle': daily_volume_rank,
                                'trigger_volume_top_two_so_far_excluding_first_candle': daily_volume_rank <= 2,
                                'trigger_time': fmt(trigger.start), 'trigger_high': trigger.high,
                                'trigger_low': trigger.low, 'trigger_volume': trigger.volume,
                                'trigger_vwap': trig_vwap, 'active_time': fmt(active.start),
                                'active_high': active.high, 'active_low': active.low, 'active_close': active.close,
                                'active_vwap': active_vwap, 'active_range_to_0918_pct': active_range_pct,
                                'early_filters_bypassed': early_filters_bypassed,
                                'combined_low': combined_low, 'entry_time': fmt(entry_time), 'entry_price': entry,
                                'ema50_at_entry': ema_entry, 'target_050_price': entry * 0.995 if entry is not None else None,
                                'target_ema50': ema_entry, 'target_price': target,
                                'exit_target_price': exit_target,
                                'initial_stop': trigger.high, 'exit_time': fmt(exit_time), 'exit_price': exit_price,
                                'exit_reason': exit_reason, 'status': status,
                                'pnl_per_share': entry-exit_price if entry is not None and exit_price is not None else None,
                                'return_pct': (entry-exit_price)/entry*100 if entry and exit_price is not None else None,
                            })
                        audits.append({'symbol': symbol, 'date': day.isoformat(), 'trigger_time': fmt(trigger.start),
                                       'status': status, 'trigger_vwap': trig_vwap, 'active_time': fmt(active.start),
                                       'active_vwap': active_vwap, 'active_range_to_0918_pct': active_range_pct,
                                       'early_filters_bypassed': early_filters_bypassed,
                                       'trigger_label': trigger_label,
                                       **conditions})
                    close_above_first_count += trigger.close > first_high and trigger.close > trigger.open
                    running_high = max(running_high, trigger.high)
            coverage.append({'symbol': symbol, 'data_available': True, 'price_cap_excluded': False,
                             'selected_period_sessions': len(minute_days), 'trigger_rule_passes': rule_passes,
                             'active_setups': active_setups, 'entries': entries})
        except Exception as exc:
            failures.append({'symbol': symbol, 'error': f'{type(exc).__name__}: {exc}'})
            coverage.append({'symbol': symbol, 'data_available': True, 'price_cap_excluded': False,
                             'selected_period_sessions': 0, 'trigger_rule_passes': 0, 'active_setups': 0, 'entries': 0})
        if n % 25 == 0 and not args.quiet:
            print(f'{n}/{len(symbols)} {symbol}', flush=True)

    entered = [r for r in trades if r['entry_price'] is not None and r['exit_price'] is not None]
    wins = sum(r['pnl_per_share'] > 0 for r in entered)
    losses = sum(r['pnl_per_share'] < 0 for r in entered)
    breakeven = len(entered) - wins - losses
    exit_counts = defaultdict(int)
    target_routes = defaultdict(lambda: defaultdict(int))
    for r in entered:
        exit_counts[r['exit_reason']] += 1
        route = 'EMA50_AT_ENTRY' if r['target_price'] == r['target_ema50'] and r['target_ema50'] < r['target_050_price'] else '0.50_PERCENT_AT_ENTRY'
        target_routes[route]['entries'] += 1
        target_routes[route][r['exit_reason']] += 1
    write_rows(args.output/'Trades.csv', trades)
    write_rows(args.output/'SetupAudit.csv', audits)
    write_rows(args.output/'UniverseCoverage.csv', coverage)
    write_rows(args.output/'ExcludedStocks.csv', excluded)
    write_rows(args.output/'DataFailures.csv', failures, ['symbol','error'])
    summary = {
        'symbols_in_universe': len(symbols),
        'symbols_excluded_price_cap': len(excluded),
        'symbols_scanned': sum(r['data_available'] and not r['price_cap_excluded'] for r in coverage),
        'symbol_sessions_scanned': sum(r['selected_period_sessions'] for r in coverage if not r['price_cap_excluded']),
        'trigger_rule_passes': sum(r['trigger_rule_passes'] for r in coverage),
        'active_setups': sum(r['active_setups'] for r in coverage),
        'waiting_for_entry_labels': sum(r.get('trigger_label') == 'WAITING_FOR_ENTRY' for r in audits),
        'early_filter_bypassed_setups': sum(r.get('early_filters_bypassed', False) for r in trades),
        'entries': len(entered), 'no_entry': sum(r['status'] == 'NO_ENTRY_WITHIN_THREE_CANDLES' for r in trades),
        'wins': wins, 'losses': losses, 'breakeven': breakeven,
        'win_rate': wins/len(entered) if entered else None,
        'mean_return_pct': mean(r['return_pct'] for r in entered) if entered else None,
        'exit_counts': dict(exit_counts),
        'target_routes': {k:dict(v) for k,v in target_routes.items()},
        'data_failures': len(failures),
    }
    (args.output/'Summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    write_rows(args.output/'Summary.csv', [{'metric':k,'result':v} for k,v in summary.items() if not isinstance(v, (dict,list))])
    stage_counts = defaultdict(int)
    for row in audits:
        stage_counts[row['status']] += 1
    route_lines = []
    for route, values in summary['target_routes'].items():
        route_lines.append(f"| {route} | {values.get('entries',0)} | {values.get('TARGET_MIN_0.50_OR_EMA50',0)} | {values.get('STOP',0)} | {values.get('EOD',0)} |")
    report = [
        f"# SOT SETUP — {args.start.isoformat()} to {args.end.isoformat()} Backtest", '',
        '## Scope and results', '',
        f"- Universe: {summary['symbols_in_universe']} F&O symbols. {summary['symbols_excluded_price_cap']} symbols were excluded for any selected-period 1-minute high above ₹9,999.",
        f"- Backtested: {summary['symbols_scanned']} symbols across {summary['symbol_sessions_scanned']:,} available symbol sessions.",
        '- Trigger window: 09:30–12:00 3-minute candle start, inclusive; IST.',
        f"- Trigger-rule passes: {summary['trigger_rule_passes']}. Active setups after VWAP/range filters: {summary['active_setups']}.",
        f"- WAITING_FOR_ENTRY labels: {summary['waiting_for_entry_labels']} trigger candles closed below the preceding candle low.",
        f"- Entries: {summary['entries']}. Entry outcomes: {summary['wins']} wins, {summary['losses']} losses, {summary['breakeven']} breakeven.",
        f"- Win rate: {summary['win_rate']*100:.1f}% (wins divided by entries).",
        f"- Average gross return per trade: {summary['mean_return_pct']:.4f}%.",
        f"- Exits: {summary['exit_counts'].get('TARGET_MIN_0.50_OR_EMA50',0)} targets, {summary['exit_counts'].get('STOP',0)} stops, {summary['exit_counts'].get('EOD',0)} end-of-day.",
        '', '## Setup outcomes', '',
        '| Outcome | Setups |', '|---|---:|',
        *[f"| {k} | {v} |" for k,v in sorted(stage_counts.items())],
        '', '## Target routes', '',
        '| Lower target selected at entry | Entries | Target exits | Stop exits | End-of-day exits |', '|---|---:|---:|---:|---:|',
        *route_lines, '',
        '## Applied rules', '',
        'Opening baskets are 09:15/09:18/09:21/09:24 and 09:18/09:21/09:24/09:27. The trigger makes a new running day high, closes red, exceeds at least one basket maximum and the lower of the previous two candle volumes, and ranks first or second by volume among complete 3-minute candles from 09:18 through the trigger (excluding 09:15). This ranking uses no future candles and is available when the trigger closes. It also requires at least two earlier green 3-minute closes above the first 3-minute high. A trigger closing below the preceding candle low receives a WAITING_FOR_ENTRY label; this label does not change entry timing.',
        'The active candle must not exceed trigger high. Trigger candles starting before 09:45 bypass both VWAP and range filters. Starting at 09:45, reject trigger/active candle VWAP touches and require active-close-to-09:18-high range of at least 0.30%.',
        'Entry breaks the lower low of trigger and active candles within the next three 3-minute candles. Initial stop is trigger high. At entry, target starts at the lower price of 0.50% below entry and latest fully closed 3-minute EMA50. After every completed 3-minute candle, recalculate the target as the lower of that fixed 0.50% level and the latest fully closed EMA50. Price-cap filtering, VWAP, and EMA rules are also described in SOT_SETUP_RULES.md.',
        '', 'Win-rate and returns are unweighted per trade and gross of fees, taxes, spread, and slippage. Separate setups are evaluated independently.', '',
        'Files: Trades.csv, SetupAudit.csv, UniverseCoverage.csv, ExcludedStocks.csv, DataFailures.csv, Summary.csv, Summary.json.'
    ]
    (args.output/'BacktestReport.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()

