# 9:30 Indian Equity Scanner

Python paper/live scanner and backtester for the Trigger 1-Minute Entry strategy.

The first valid red 3-minute Trigger among 09:30, 09:33 and 09:36 starts a
four-candle 1-minute guide window. The first qualifying green guide candle is
G1. G2 must strictly break G1 high, or equal it for one G3 strict-break
opportunity.

See `STRATEGY_SPEC.md` for the complete confirmed rules.

## Safety

- Paper mode is the default.
- Live mode requires the command-line confirmation and the untracked
  `LIVE_APPROVAL.txt` approval file.
- `.env`, live data and generated live reports are excluded from Git.
- Never commit broker credentials.

## Setup

```powershell
setup_scanner.cmd
```

Copy `.env.example` to `.env` only if credentials are not being supplied by the
configured reference project.

## Run

```powershell
start_paper.cmd
```

Live trading should only be started after paper validation:

```powershell
start_live.cmd
```

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Backtest

```powershell
python backtest.py `
  --base-data C:\path\to\Data `
  --output C:\path\to\output `
  --start 2026-04-01 `
  --end 2026-07-26
```

Outputs include trade-level `NetRR`, monthly summed NetRR, setup audits,
coverage, daily results and stock-level results.
