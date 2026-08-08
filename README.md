# 9:30 Indian Equity Scanner

Python paper/live scanner and backtester for the Trigger Percentage strategy.

The first valid red 3-minute Trigger among 09:30, 09:33 and 09:36 starts a
three-candle G1 search. A strict G1-high break during only the immediately
following 1-minute candle enters the trade. TP1 is the lower of the Trigger-percentage
R1 level and the entry-based 3R R2 level.

The Golden quality overlay doubles size for any Alpha/Beta/Gamma-qualified
entry. Golden uses 200 underlying shares (or two option-paper lot-equivalents),
books 140 at a fixed 1.3R TP1 without the R1 cap, and trails the remaining 60
with the existing logic. Standard entries retain the current 100-share dynamic
target behavior. See `STRATEGY_SPEC.md` for the exact classifications.

See `STRATEGY_SPEC.md` for all confirmed entry, stop, exit and reporting rules.

## Automated option paper mode

`--option-paper` keeps every signal, stop, TP1 and trailing decision on the
underlying stock, while paper-filling the selected current-expiry stock CE at
best ask and exiting at best bid. Quotes that are stale, missing or wider than
the configured maximum spread are rejected rather than estimated.

```powershell
python run_scanner.py --option-paper
```

For unattended Ubuntu VPS installation, Telegram/email delivery, systemd
restart recovery and the weekday 09:10 IST timer, see `deploy/README_VPS.md`.

### Free GitHub Actions schedule

The workflow `.github/workflows/option-paper.yml` starts at 09:15 IST on
weekdays, allowing a buffer for GitHub runner delays, and force-closes remaining positions at 11:00 with exit reason
`TIME_EXIT_11_00`. It uses encrypted repository secrets and never enables live
broker orders. Scheduled workflows run only from the default branch. See
`deploy/README_GITHUB_ACTIONS.md` for secret setup and daily operation.

## Safety

- Paper mode is the default.
- Live mode requires the command-line confirmation and the untracked
  `LIVE_APPROVAL.txt` approval file.
- `.env`, live data and generated live reports are excluded from Git.
- Never commit broker credentials.

## Setup and run

```powershell
setup_scanner.cmd
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
  --end 2026-07-24
```

Backtest outputs include trade and monthly NetRR, target-driver fields, runner
P&L, equity curves, drawdown, runner/stop stock patterns and comparison data.
Historical results are gross of costs and are not a live audited track record.
